"""Markdown 切片脚本：将 md 切成 chunks.json，供后续向量化使用。

流水线位置: batch_parse.py (MinerU 转md) -> describe_images.py (图片描述并回写md) -> 本脚本

用法:
    uv run python rag_dir/chunk_md.py                # 默认处理 doc/huawei_doc/md
    uv run python rag_dir/chunk_md.py doc/common/md  # 指定目录
    uv run python rag_dir/chunk_md.py path/to/a.md   # 单文件调试

策略:
    1. 预处理: 删除"目录"章节; 把 </tr> 展开为独立行, 避免表格被硬切
    2. 图片处理: md 已被 describe_images.py 融合为 "[图片: 描述](路径)" 形式;
       切片时把描述文本保留在 text 中参与 embedding, 图片路径剔除出文本
       并提取进 metadata.images 供前端展示 (若跳过了 describe_images.py,
       原始图片标签也会被识别, 仅记路径不生成描述)
    3. 按 #/## 标题切 section (带标题元数据)
    4. 超过 CHUNK_SIZE 的 section 用 RecursiveCharacterTextSplitter 二次切分
    5. 每个 chunk 附带 source / product / section / images 元数据
"""

import json
import re
import sys
from pathlib import Path

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

CHUNK_SIZE = 800
CHUNK_OVERLAP = 80

# 图片标签(含路径捕获): ![alt](images/xxx.jpg) 与 <img src="images/xxx.jpg"/>
RE_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")
RE_HTML_IMAGE = re.compile(r'<img[^>]*?src="([^"]*)"[^>]*/?>')
# 融合格式(describe_images.py 回写后): [图片: 描述](images/xxx.jpg)
RE_FUSED_IMAGE = re.compile(r"\[图片:([^\]]*)\]\(([^)]+)\)")
# 图片占位符: 切片时用于定位图片归属, 最终从文本中剔除
RE_IMG_PLACEHOLDER = re.compile(r"\{\{IMG:([^}]+)\}\}")
# "目录"章节: 从 "## 目录" 到下一个 "## " 之间整体删除
RE_TOC = re.compile(r"##\s*目\s*录.*?(?=\n##\s)", re.S)


def clean_markdown(text: str) -> str:
    text = RE_TOC.sub("", text)
    # 表格按行展开, 让 "\n" 分隔符能在 <tr> 边界切开
    text = text.replace("</tr>", "</tr>\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def transform_images(text: str) -> str:
    """统一三种图片形态为 "{{IMG:路径}}" 占位形式:

    - [图片: 描述](路径)  -> "[图片: 描述]{{IMG:路径}}"  (描述保留参与 embedding)
    - ![alt](路径) / <img src=路径> -> "{{IMG:路径}}"  (无描述, 仅记路径)

    占位符独立成行, 避免细切时被截断; finalize_piece 再提取路径并移除占位符。
    """
    def repl(desc: str, path: str, inline: bool) -> str:
        marker = f"[图片: {desc.strip()}]" if desc.strip() else ""
        pad = " " if inline else "\n"
        return f"{pad}{marker}{{{{IMG:{path}}}}}{pad}"

    text = RE_FUSED_IMAGE.sub(lambda m: repl(m.group(1), m.group(2), inline=False), text)
    text = RE_HTML_IMAGE.sub(lambda m: repl("", m.group(1), inline=True), text)
    text = RE_MD_IMAGE.sub(lambda m: repl("", m.group(1), inline=False), text)
    return text


def finalize_piece(piece: str) -> tuple[str, list[str]]:
    """提取占位符中的图片路径进 metadata, 并把占位符从文本中剔除。"""
    images = RE_IMG_PLACEHOLDER.findall(piece)
    text = RE_IMG_PLACEHOLDER.sub("", piece)
    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    return text, images


def extract_product(filename: str) -> str:
    """从文件名提取产品型号, 如 '华为擎云 G540 用户指南-(...)' -> '华为擎云 G540'。"""
    m = re.match(r"(.+?)\s*(用户指南|用户手册|说明书|快速入门)", filename)
    return m.group(1).strip(" -_") if m else Path(filename).stem


def split_section(text: str, splitter: RecursiveCharacterTextSplitter) -> list[str]:
    if len(text) <= CHUNK_SIZE:
        return [text]
    return splitter.split_text(text)


def chunk_file(md_path: Path) -> list[dict]:
    text = clean_markdown(md_path.read_text(encoding="utf-8"))
    text = transform_images(text)

    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "doc"), ("##", "section")],
        strip_headers=False,
    )
    sections = md_splitter.split_text(text)

    fine_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )

    product = extract_product(md_path.name)
    chunks = []
    for sec in sections:
        section_title = sec.metadata.get("section", "") or sec.metadata.get("doc", "")
        for piece in split_section(sec.page_content, fine_splitter):
            piece = piece.strip()
            # 丢弃纯标题壳 (去掉 # 和空白后几乎无内容)
            if len(re.sub(r"[#\s]", "", piece)) < 10:
                continue
            text_out, images = finalize_piece(piece)
            if not text_out:
                continue
            chunks.append(
                {
                    "text": text_out,
                    "metadata": {
                        "source": md_path.name,
                        "product": product,
                        "section": section_title,
                        "images": images,
                    },
                }
            )
    return chunks


def main() -> None:
    base = Path(__file__).parent
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else base / "doc/huawei_doc/md"
    if target.is_file():
        files = [target]
    else:
        # md/<文档名>/<文档名>.md 子目录结构 (md 与 images/ 同目录, 图片相对路径有效)
        files = sorted(target.glob("*/*.md"))
    if not files:
        print(f"未找到 md 文件: {target}")
        sys.exit(1)

    all_chunks = []
    for md_path in files:
        chunks = chunk_file(md_path)
        for i, c in enumerate(chunks):
            c["metadata"]["chunk_index"] = i
            c["metadata"]["global_index"] = len(all_chunks) + i
        all_chunks.extend(chunks)
        print(f"{md_path.name}: {len(chunks)} chunks")

    out_path = target.parent / "chunks.json" if target.is_dir() else target.parent.parent / "chunks.json"
    out_path.write_text(json.dumps(all_chunks, ensure_ascii=False, indent=1), encoding="utf-8")

    sizes = [len(c["text"]) for c in all_chunks]
    n_imgs = sum(1 for c in all_chunks if c["metadata"]["images"])
    print(f"\n共 {len(files)} 个文件, {len(all_chunks)} 个 chunk (含图片引用的 {n_imgs} 个) -> {out_path}")
    print(f"长度: min={min(sizes)}, max={max(sizes)}, avg={sum(sizes) // len(sizes)}, "
          f"超{CHUNK_SIZE}的 {sum(1 for s in sizes if s > CHUNK_SIZE)} 个")


if __name__ == "__main__":
    main()
