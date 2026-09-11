"""图片描述生成脚本：调用视觉模型为 md 目录下的图片生成结合章节上下文的中文描述。

流水线位置: batch_parse.py (MinerU 转md) -> 本脚本 -> chunk_md.py (切片)

用法:
    uv run python rag_dir/describe_images.py                # 默认处理 doc/huawei_doc/md
    uv run python rag_dir/describe_images.py doc/common/md  # 指定目录

做法:
    1. 扫描每个 md 文件, 为每张图片定位其所属章节标题与前后文文本窗口
    2. 将 "产品型号 + 章节 + 上下文片段" 与图片一起发给视觉模型, 生成贴合语境的描述
    3. 相同图片文件(内容 hash 同名)且相同章节的引用共享一次调用结果
    4. 回写 md: 把图片标签替换为 "[图片: 描述](路径)", 使 md 成为图文融合版;
       无描述(调用失败)的标签保留原样, 重跑可重试

输出:
    - <md目录>/image_descriptions.json  描述缓存 (断点续传用)
    - 直接改写 <md目录> 下的 *.md 文件

支持断点续传: 已有描述的图片自动跳过; 限速(429)自动等待重试; 每张图落盘一次。
幂等: 已替换的 md 中原始标签已消失, 重跑只会处理仍保留原样的标签。
"""

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 需在读取模型配置前加载 .env
load_dotenv(Path(__file__).parent.parent / ".env")

MODEL = os.environ.get("VLM_MODEL", "qwen-vl-plus")
MAX_RETRY = 5
RETRY_429_WAIT = 20  # 免费模型限速(429)时的等待秒数

RE_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
RE_HTML_IMAGE = re.compile(r'<img[^>]*?src="([^"]*)"[^>]*/?>')
RE_HEADER = re.compile(r"^#{1,2}\s+(.+?)\s*$", re.M)
RE_HTML_TAG = re.compile(r"<[^>]+>")
RE_TOC = re.compile(r"##\s*目\s*录.*?(?=\n##\s)", re.S)
RE_PRODUCT = re.compile(r"(.+?)\s*(用户指南|用户手册|说明书|快速入门)")


def build_context_map(md_dir: Path) -> dict[str, dict]:
    """扫描 md 文件, 为每张图片提取所属章节标题与前后文窗口。

    返回 {f"{md_path.stem}/{图片相对路径}": {"section": 标题, "window": 上下文文本}}
    只收录 md 中实际引用的图片。
    """
    contexts = {}
    for md_path in sorted(md_dir.glob("*/*.md")):  # 子目录结构: md/<文档名>/<文档名>.md
        raw = md_path.read_text(encoding="utf-8")
        raw = RE_TOC.sub("", raw)  # 先删"目录"章节, 避免目录条目污染上下文窗口
        refs = [(m.start(), m.end(), m.group(1)) for m in RE_MD_IMAGE.finditer(raw)]
        refs += [(m.start(), m.end(), m.group(1)) for m in RE_HTML_IMAGE.finditer(raw)]
        refs.sort()
        for start, end, path in refs:
            secs = RE_HEADER.findall(raw[:start])
            window_raw = raw[max(0, start - 200):end + 200]
            window_raw = RE_MD_IMAGE.sub(" ", window_raw)
            window_raw = RE_HTML_IMAGE.sub(" ", window_raw)
            window_raw = RE_HTML_TAG.sub(" ", window_raw)
            window = re.sub(r"\s+", " ", window_raw).strip()
            contexts[f"{md_path.stem}/{path}"] = {
                "section": secs[-1] if secs else "",
                "window": window[:300],
            }
    return contexts


def build_prompt(doc_title: str, section: str, window: str) -> str:
    m = RE_PRODUCT.match(doc_title)
    product = m.group(1).strip() if m else doc_title
    parts = [f"这是华为产品手册《{product}》中的一张插图。"]
    if section:
        parts.append(f"它位于「{section}」章节。")
    if window:
        parts.append(f"图片所在位置的上下文：{window}")
    parts.append(
        "请结合上下文，用简体中文描述这张图片展示的内容和它在手册中的作用，80字以内，"
        "直接输出描述文字，不要任何前缀，不要提及具体产品型号。"
    )
    return " ".join(parts)


def describe_image(client: OpenAI, img_path: Path, prompt: str) -> str:
    b64 = base64.b64encode(img_path.read_bytes()).decode()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return resp.choices[0].message.content.strip()


def replace_images_in_md(md_path: Path, descriptions: dict) -> tuple[int, int]:
    """把 md 中的原始图片标签替换为 "[图片: 描述](路径)"。

    从后往前替换避免位置偏移; 无描述的标签保留原样(重跑可重试)。
    返回 (替换数, 跳过数)。
    """
    raw = md_path.read_text(encoding="utf-8")
    refs = [(m.start(), m.end(), m.group(1)) for m in RE_MD_IMAGE.finditer(raw)]
    refs += [(m.start(), m.end(), m.group(1)) for m in RE_HTML_IMAGE.finditer(raw)]
    refs.sort()
    if not refs:
        return 0, 0
    replaced = skipped = 0
    for start, end, path in reversed(refs):
        desc = descriptions.get(f"{md_path.stem}/{path}", "")
        if not desc:
            skipped += 1
            continue
        raw = raw[:start] + f"[图片: {desc}]({path})" + raw[end:]
        replaced += 1
    md_path.write_text(raw, encoding="utf-8")
    return replaced, skipped


def main() -> None:
    base = Path(__file__).parent
    md_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else base / "doc/huawei_doc/md"

    out_path = md_dir / "image_descriptions.json"
    descriptions = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}

    contexts = build_context_map(md_dir)  # 只含 md 中实际引用的图片
    # 图片路径 -> 实际文件 (未引用的多余图片文件跳过)
    images = []
    for key in sorted(contexts):
        img_path = md_dir / key
        if img_path.exists():
            images.append(img_path)
        else:
            print(f"  [警告] md 引用的图片不存在: {key}")

    todo = [p for p in images if str(p.relative_to(md_dir)) not in descriptions]
    print(f"md 引用图片 {len(images)} 张, 已有描述 {len(images) - len(todo)} 张, "
          f"待处理 {len(todo)} 张, 模型: {MODEL}")

    # 相同图片文件 + 相同章节共享一次调用结果
    shared: dict[str, str] = {}
    api_key = os.environ.get("VLM_API_KEY") or os.environ["MODEL_API_KEY"]
    base_url = os.environ.get("VLM_BASE_URL") or os.environ["MODEL_BASE_URL"]
    client = OpenAI(api_key=api_key, base_url=base_url)

    called = 0
    for i, img in enumerate(todo, 1):
        key = str(img.relative_to(md_dir))
        ctx = contexts.get(key, {})
        cache_key = f"{img.name}:{ctx.get('section', '')}"
        if cache_key in shared:
            descriptions[key] = shared[cache_key]
            out_path.write_text(json.dumps(descriptions, ensure_ascii=False, indent=1), encoding="utf-8")
            continue

        prompt = build_prompt(key.split("/")[0], ctx.get("section", ""), ctx.get("window", ""))
        for attempt in range(1, MAX_RETRY + 1):
            try:
                desc = describe_image(client, img, prompt)
                called += 1
                descriptions[key] = desc
                shared[cache_key] = desc
                break
            except Exception as e:  # noqa: BLE001
                is_429 = "429" in str(e) or "rate" in str(e).lower()
                print(f"  [失败x{attempt}] {key}: {e}")
                if attempt == MAX_RETRY:
                    descriptions[key] = ""
                else:
                    time.sleep(RETRY_429_WAIT if is_429 else 2 * attempt)
        # 每张图都落盘, 中断不丢进度
        out_path.write_text(json.dumps(descriptions, ensure_ascii=False, indent=1), encoding="utf-8")
        if called % 10 == 0 or i == len(todo):
            print(f"[图 {i}/{len(todo)} | 调用 {called}] {key} -> {descriptions[key][:40]}")

    out_path.write_text(json.dumps(descriptions, ensure_ascii=False, indent=1), encoding="utf-8")
    empty = sum(1 for v in descriptions.values() if not v)
    print(f"\n完成: {len(descriptions)} 条描述 (实际调用 {called} 次) -> {out_path}" + (f", 失败 {empty} 条" if empty else ""))

    # 回写 md: 图片标签 -> [图片: 描述](路径), 得到图文融合版 (幂等, 重复执行无副作用)
    # 只处理子目录 md (md/<文档名>/<文档名>.md, 与 images/ 同目录, 相对路径可正常看图)
    md_files = sorted(md_dir.glob("*/*.md"))
    total_replaced = total_skip = 0
    for md_path in md_files:
        r, s = replace_images_in_md(md_path, descriptions)
        total_replaced += r
        total_skip += s
    msg = f"\n已回写 {len(md_files)} 个 md: 替换 {total_replaced} 处图片标签"
    if total_skip:
        msg += f", {total_skip} 处无描述保留原样 (重跑可重试)"
    print(msg)


if __name__ == "__main__":
    main()
