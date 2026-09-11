#!/usr/bin/env python3
"""Batch-convert PDFs to Markdown via the local MinerU API.

Usage:
    python batch_parse.py                          # default: huawei_doc/
    python batch_parse.py --input-dir doc/common   # 指定其他目录
    python batch_parse.py -i doc/common -o doc/common/md  # 指定输入输出目录

Output format (mirrors huawei_doc/md):
    <output>/<stem>.md                  # 纯文本版 (RAG 用)
    <output>/<stem>/<stem>.md           # 带图版 (md 内引用 images/xxx.jpg)
    <output>/<stem>/images/*.jpg        # 解析出的图片

Features:
    - Async /tasks endpoint (no long-lived HTTP connection while processing).
    - Auto-retry on connection reset / service restart: each PDF is attempted
      several times and the script waits for the service to recover, so a
      container restart mid-batch no longer fails the whole run.
    - Skips PDFs that already have a non-empty output (resumable).
    - Backfill: 纯文本 md 已存在但缺少带图版时，仅从 output/ 补图，不重新解析。
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

import requests

API_BASE = "http://localhost:8080"
HERE = Path(__file__).parent
OUTPUT_ROOT = HERE / "doc" / "output"  # MinerU 容器挂载的输出目录

# lang_list is a list-typed form field; pass it as repeated key=value.
LANG_LIST = ("ch",)

BASE_FORM = [
    ("backend", "pipeline"),
    ("parse_method", "auto"),
    ("formula_enable", "true"),
    ("table_enable", "true"),
    ("return_md", "true"),
    ("return_middle_json", "false"),
    ("return_model_output", "false"),
    ("return_content_list", "false"),
    ("return_images", "false"),
]

# 容错参数
MAX_PDF_ATTEMPTS = 4   # 单个 PDF 整体重试轮数(提交→轮询→拉取)
RETRY_BACKOFF = 8      # 重试退避基数(秒)
HEALTH_TIMEOUT = 900   # 等待服务恢复的最长时间(秒)


def log(*args, **kwargs):
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def form():
    return BASE_FORM + [("lang_list", v) for v in LANG_LIST]


class TaskLost(Exception):
    """任务在服务端不存在(通常因容器重启丢失)，需要重新提交。"""


def wait_for_health(timeout: int = HEALTH_TIMEOUT) -> bool:
    """阻塞等待 MinerU 服务恢复健康。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{API_BASE}/health", timeout=10)
            if r.status_code == 200 and r.json().get("status") == "healthy":
                return True
        except requests.RequestException:
            pass
        time.sleep(5)
    return False


def submit(pdf: Path) -> str:
    """提交任务，带连接级重试；每次重试重新打开文件(避免文件指针耗尽)。"""
    last = None
    for attempt in range(1, MAX_PDF_ATTEMPTS + 1):
        try:
            with pdf.open("rb") as f:
                r = requests.post(
                    f"{API_BASE}/tasks",
                    files=[("files", (pdf.name, f, "application/pdf"))],
                    data=form(),
                    timeout=180,
                )
            if r.status_code < 500:
                r.raise_for_status()
                data = r.json()
                task_id = data.get("task_id") or data.get("id")
                if not task_id:
                    raise RuntimeError(f"no task_id in submit response: {data}")
                return task_id
            last = RuntimeError(f"HTTP {r.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        wait = RETRY_BACKOFF * attempt
        log(f"    [submit] 连接失败({last})，等待服务恢复 {wait}s 后重试 "
            f"({attempt}/{MAX_PDF_ATTEMPTS})")
        wait_for_health()
        time.sleep(wait)
    raise RuntimeError(f"submit 重试 {MAX_PDF_ATTEMPTS} 次后仍失败: {last}")


def poll(task_id: str, timeout: int = 2400) -> dict:
    """轮询任务状态；连接中断时等待恢复继续轮询，任务丢失则抛 TaskLost。"""
    deadline = time.time() + timeout
    url = f"{API_BASE}/tasks/{task_id}"
    warned = False
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 404:
                raise TaskLost(f"任务 {task_id} 不存在(服务可能重启)")
            r.raise_for_status()
            s = r.json()
            warned = False
        except TaskLost:
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            if not warned:
                log(f"    [poll] 连接中断({e})，等待服务恢复后继续...")
                warned = True
            wait_for_health()
            time.sleep(5)
            continue

        state = (s.get("status") or s.get("state") or "").lower()
        if state in {"done", "completed", "success"}:
            return s
        if state in {"failed", "error", "cancelled"}:
            raise RuntimeError(f"task {task_id} failed: {s.get('error') or s}")
        time.sleep(5)
    raise TimeoutError(f"task {task_id} did not finish in {timeout}s")


def fetch_markdown(task_id: str, expected_name: str) -> str:
    """拉取结果，带连接级重试。"""
    last = None
    for attempt in range(1, MAX_PDF_ATTEMPTS + 1):
        try:
            r = requests.get(f"{API_BASE}/tasks/{task_id}/result", timeout=600)
            if r.status_code == 404:
                raise TaskLost(f"结果 {task_id} 不存在(服务可能重启)")
            r.raise_for_status()
            data = r.json()
            md = data.get("md") or data.get("markdown")
            if md is None and isinstance(data.get("results"), dict):
                item = data["results"].get(expected_name) or next(
                    iter(data["results"].values()), {}
                )
                md = item.get("md_content") or item.get("md") or item.get("markdown")
            if not md:
                raise RuntimeError(f"no markdown in result: keys={list(data.keys())}")
            return md
        except TaskLost:
            raise
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            wait = RETRY_BACKOFF * attempt
            log(f"    [fetch] 连接失败({e})，{wait}s 后重试 ({attempt}/{MAX_PDF_ATTEMPTS})")
            wait_for_health()
            time.sleep(wait)
    raise RuntimeError(f"fetch 重试后仍失败: {last}")


def copy_images(task_id: str, stem: str, out_dir: Path) -> int:
    """从 output/<task_id>/.../auto/images 复制图片到 out_dir/<stem>/images/。"""
    task_dir = OUTPUT_ROOT / task_id
    if not task_dir.is_dir():
        return 0
    images_src = None
    for auto_dir in task_dir.rglob("auto"):
        img = auto_dir / "images"
        if img.is_dir() and any(img.iterdir()):
            images_src = img
            break
    if images_src is None:
        return 0
    target = out_dir / stem / "images"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in images_src.iterdir():
        if src.is_file():
            shutil.copy2(src, target / src.name)
            copied += 1
    return copied


def copy_images_by_stem(stem: str, out_dir: Path) -> int:
    """按 pdf stem 在 output/ 中查找对应任务并复制图片(用于 backfill)。"""
    if not OUTPUT_ROOT.is_dir():
        return 0
    for task_dir in OUTPUT_ROOT.iterdir():
        if not task_dir.is_dir():
            continue
        for auto_dir in task_dir.rglob("auto"):
            if not auto_dir.is_dir():
                continue
            md_files = list(auto_dir.glob("*.md"))
            if not md_files or md_files[0].stem != stem:
                continue
            img = auto_dir / "images"
            if not (img.is_dir() and any(img.iterdir())):
                return 0
            target = out_dir / stem / "images"
            target.mkdir(parents=True, exist_ok=True)
            copied = 0
            for src in img.iterdir():
                if src.is_file():
                    shutil.copy2(src, target / src.name)
                    copied += 1
            return copied
    return 0


def process(pdf: Path, out_dir: Path, pdf_dir: Path) -> None:
    stem = pdf.stem
    plain_md = out_dir / (stem + ".md")     # 纯文本版
    full_md = out_dir / stem / (stem + ".md")  # 带图版
    full_md.parent.mkdir(parents=True, exist_ok=True)

    # 已完整处理(纯文本 + 带图版均存在)
    if (plain_md.exists() and plain_md.stat().st_size > 0
            and full_md.exists() and full_md.stat().st_size > 0):
        log(f"  skip (exists): {stem}")
        return

    # 纯文本已存在但缺带图版 → 从 output/ 补图，不重新解析
    if plain_md.exists() and plain_md.stat().st_size > 0:
        log(f"  backfill images: {stem}")
        n = copy_images_by_stem(stem, out_dir)
        shutil.copy2(plain_md, full_md)
        log(f"  ok (backfill) -> {full_md.relative_to(pdf_dir)} ({n} images)")
        return

    t0 = time.time()
    for attempt in range(1, MAX_PDF_ATTEMPTS + 1):
        try:
            wait_for_health()
            task_id = submit(pdf)
            log(f"  task_id={task_id} (尝试 {attempt}/{MAX_PDF_ATTEMPTS})")
            poll(task_id)
            md = fetch_markdown(task_id, pdf.name)
            plain_md.write_text(md, encoding="utf-8")
            full_md.write_text(md, encoding="utf-8")
            n = copy_images(task_id, stem, out_dir)
            log(f"  ok -> {plain_md.relative_to(pdf_dir)} "
                f"({len(md)} chars, {time.time()-t0:.1f}s, {n} images)")
            return
        except TaskLost as e:
            log(f"  [尝试 {attempt}/{MAX_PDF_ATTEMPTS}] 任务丢失: {e}，重新提交")
        except Exception as e:
            log(f"  [尝试 {attempt}/{MAX_PDF_ATTEMPTS}] 失败: {e}")
        if attempt < MAX_PDF_ATTEMPTS:
            time.sleep(RETRY_BACKOFF)
    raise RuntimeError(f"{pdf.name} 重试 {MAX_PDF_ATTEMPTS} 次后仍失败")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch convert PDFs to Markdown via MinerU API"
    )
    parser.add_argument(
        "-i", "--input-dir",
        default=str(HERE / "huawei_doc"),
        help="PDF 输入目录 (默认: huawei_doc)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="MD 输出目录 (默认: <input-dir>/md)",
    )
    args = parser.parse_args()

    pdf_dir = Path(args.input_dir).resolve()
    if not pdf_dir.exists():
        log(f"input dir not found: {pdf_dir}", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir).resolve() if args.output_dir else pdf_dir / "md"
    out_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        log(f"no PDFs found in {pdf_dir}", file=sys.stderr)
        return 1

    log(f"found {len(pdfs)} PDFs in {pdf_dir}")
    # 注意：不在此强制检查 MinerU 服务——backfill/skip 路径无需服务，
    # 真正需要解析时 process() 内部会先 wait_for_health()。

    failed = []
    for i, pdf in enumerate(pdfs, 1):
        log(f"[{i}/{len(pdfs)}] {pdf.name}")
        try:
            process(pdf, out_dir, pdf_dir)
        except Exception as e:
            log(f"  FAIL: {e}", file=sys.stderr)
            failed.append(pdf.name)
    log("done")
    if failed:
        log(f"\n{len(failed)} 个文件最终失败:", file=sys.stderr)
        for name in failed:
            log(f"  - {name}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
