#!/usr/bin/env python3
"""Batch-convert PDFs in huawei_doc to Markdown via the local MinerU API.

Uses the async /tasks endpoint so we don't keep a long-lived HTTP connection
open while the server is processing (which previously caused "Connection reset
by peer" failures).  Writes one .md per PDF to huawei_doc/md/ and skips PDFs
that already have a non-empty output.
"""

import sys
import time
from pathlib import Path

import requests

API_BASE = "http://localhost:8080"
HERE = Path(__file__).parent
PDF_DIR = HERE / "huawei_doc"
OUT_DIR = PDF_DIR / "md"
OUT_DIR.mkdir(parents=True, exist_ok=True)

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


def form():
    return BASE_FORM + [("lang_list", v) for v in LANG_LIST]


def submit(pdf: Path) -> str:
    with pdf.open("rb") as f:
        r = requests.post(
            f"{API_BASE}/tasks",
            files=[("files", (pdf.name, f, "application/pdf"))],
            data=form(),
            timeout=120,  # submission is fast; polling handles the long work
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


def process(pdf: Path) -> None:
    out = OUT_DIR / (pdf.stem + ".md")
    if out.exists() and out.stat().st_size > 0:
        print(f"  skip (exists): {out.name}")
        return
    t0 = time.time()
    task_id = submit(pdf)
    print(f"  task_id={task_id}")
    poll(task_id)
    md = fetch_markdown(task_id, pdf.name)
    out.write_text(md, encoding="utf-8")
    print(f"  ok -> {out.relative_to(PDF_DIR)} ({len(md)} chars, {time.time()-t0:.1f}s)")


def main() -> int:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print("no PDFs found", file=sys.stderr)
        return 1
    print(f"found {len(pdfs)} PDFs in {PDF_DIR}")
    for i, pdf in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf.name}")
        try:
            process(pdf)
        except Exception as e:
            print(f"  FAIL: {e}", file=sys.stderr)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
