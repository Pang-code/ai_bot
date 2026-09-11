#!/usr/bin/env python3
"""
Monitor huawei_doc directory for new PDF files, auto-parse via MinerU API,
auto-save Markdown and images to md directory.
"""
import sys
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import requests

API_BASE = "http://localhost:8080"
HERE = Path(__file__).parent
WATCH_DIR = HERE / "huawei_doc"
MD_DIR = WATCH_DIR / "md"
MD_DIR.mkdir(parents=True, exist_ok=True)

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


def form():
    return BASE_FORM + [("lang_list", v) for v in LANG_LIST]


def submit(pdf: Path) -> str:
    with pdf.open("rb") as f:
        r = requests.post(
            f"{API_BASE}/tasks",
            files=[("files", (pdf.name, f, "application/pdf"))],
            data=form(),
            timeout=120,
        )
    r.raise_for_status()
    data = r.json()
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise RuntimeError(f"no task_id in submit response: {data}")
    return task_id


def poll(task_id: str, timeout: int = 1800) -> dict:
    deadline = time.time() + timeout
    url = f"{API_BASE}/tasks/{task_id}"
    while time.time() < deadline:
        s = requests.get(url, timeout=30).json()
        state = (s.get("status") or s.get("state") or "").lower()
        if state in {"done", "completed", "success"}:
            return s
        if state in {"failed", "error", "cancelled"}:
            raise RuntimeError(f"task {task_id} failed: {s.get('error') or s}")
        time.sleep(5)
    raise TimeoutError(f"task {task_id} did not finish in {timeout}s")


def fetch_markdown(task_id: str, expected_name: str) -> str:
    r = requests.get(f"{API_BASE}/tasks/{task_id}/result", timeout=600)
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


def copy_images(task_id: str, md_stem: str):
    """Copy images from output/<task_id> to md/<md_stem>/images"""
    task_dir = HERE / "output" / task_id
    if not task_dir.exists():
        return
    auto_dir = next(task_dir.rglob("auto"), None)
    if not auto_dir:
        return
    images_dir = auto_dir / "images"
    if not images_dir.exists() or not any(images_dir.iterdir()):
        return
    target_dir = MD_DIR / md_stem
    target_images = target_dir / "images"
    if target_images.exists():
        import shutil
        shutil.rmtree(target_images)
    shutil.copytree(images_dir, target_images)


def process_pdf(pdf_path: Path):
    if not pdf_path.suffix.lower() == ".pdf":
        return
    if pdf_path.stat().st_size == 0:
        return
    # 等待文件写入完成
    time.sleep(2)
    out_md = MD_DIR / (pdf_path.stem + ".md")
    if out_md.exists() and out_md.stat().st_size > 0:
        print(f"[skip] {pdf_path.name} already parsed")
        return
    print(f"[auto parse] {pdf_path.name}")
    try:
        task_id = submit(pdf_path)
        poll(task_id)
        md = fetch_markdown(task_id, pdf_path.name)
        out_md.write_text(md, encoding="utf-8")
        copy_images(task_id, pdf_path.stem)
        print(f"[done] {pdf_path.name} → {out_md.name}")
    except Exception as e:
        print(f"[fail] {pdf_path.name}: {e}", file=sys.stderr)


class PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        process_pdf(path)

    def on_moved(self, event):
        if event.is_directory:
            return
        path = Path(event.dest_path)
        process_pdf(path)


def main():
    # 先处理目录下已有的未解析PDF
    print(f"scanning existing PDFs in {WATCH_DIR}")
    for pdf in WATCH_DIR.glob("*.pdf"):
        process_pdf(pdf)
    # 启动监控
    event_handler = PDFHandler()
    observer = Observer()
    observer.schedule(event_handler, str(WATCH_DIR), recursive=False)
    observer.start()
    print(f"watching for new PDFs in {WATCH_DIR}...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
