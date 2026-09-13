"""向量化入库: 读 chunks.json -> bge-m3 embedding -> 写入 Milvus。

流水线位置: 3.chunk_md.py (切片) -> 本脚本 -> 检索服务
用法:
    uv run python rag_dir/4.embed_and_insert.py                      # huawei_doc + common 全部入库
    uv run python rag_dir/4.embed_and_insert.py rag_dir/doc/huawei_doc/chunks.json  # 指定单个文件
    uv run python rag_dir/4.embed_and_insert.py --rebuild            # 删除 collection 重建

特性:
    - 断点续传: 已存在的 id 跳过, 中断重跑不重复计算
    - 批量 embedding (BATCH=64) + 批量插入, 失败整批重试
    - embedding 结果缓存到 <chunks同名>.emb_cache.json, 与入库解耦
"""

import json
import os
import sys
import time
from pathlib import Path

import requests
from pymilvus import DataType, MilvusClient

BASE = Path(__file__).parent
CHUNK_FILES = [BASE / "doc/huawei_doc/chunks.json", BASE / "doc/common/chunks.json"]

MILVUS_URI = os.environ.get("MILVUS_URI", "http://localhost:19530")
COLLECTION = os.environ.get("MILVUS_COLLECTION", "rag_chunks")
EMBED_URL = os.environ.get("EMBED_URL", "http://192.168.1.19:11434/api/embed")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
DIM = 1024  # bge-m3 输出维度
BATCH = 16  # 局域网 CPU 机器跑 bge-m3, 64 条长文本会超 120s, 16 条稳妥

client = MilvusClient(uri=MILVUS_URI)


def fit_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断 (Milvus VARCHAR max_length 单位是字节, 中文占3字节)。"""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore")


def embed(texts: list[str]) -> list[list[float]]:
    """调用局域网 Ollama bge-m3 批量 embedding, 失败重试。"""
    for attempt in range(1, 6):
        try:
            r = requests.post(
                EMBED_URL,
                json={"model": EMBED_MODEL, "input": texts},
                timeout=180,
            )
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception as e:  # noqa: BLE001
            wait = 5 * attempt
            print(f"  [embedding失败x{attempt}] {str(e)[:100]}, {wait}s后重试")
            time.sleep(wait)
    raise RuntimeError("embedding 连续5次失败, 终止")


def ensure_collection() -> None:
    """建 collection: 标量字段供过滤, COSINE 度量 (bge 官方推荐)。"""
    if client.has_collection(COLLECTION):
        return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("source", DataType.VARCHAR, max_length=512)
    schema.add_field("product", DataType.VARCHAR, max_length=256)
    schema.add_field("section", DataType.VARCHAR, max_length=256)
    schema.add_field("images", DataType.VARCHAR, max_length=2048)  # json 序列化的路径列表
    schema.add_field("chunk_index", DataType.INT32)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(COLLECTION, schema=schema, index_params=index_params)
    print(f"已创建 collection: {COLLECTION} (dim={DIM}, COSINE)")


def load_all_chunks(chunk_files: list[Path]) -> list[dict]:
    """读入全部切片, 统一加上 source 文档集名。"""
    chunks = []
    for cf in chunk_files:
        subset = "huawei_doc" if "huawei_doc" in str(cf) else "common"
        for c in json.loads(cf.read_text(encoding="utf-8")):
            c["dataset"] = subset
            chunks.append(c)
    return chunks


def main() -> None:
    chunk_files = CHUNK_FILES
    if len(sys.argv) > 1 and sys.argv[1] != "--rebuild":
        chunk_files = [Path(sys.argv[1])]
    if "--rebuild" in sys.argv:
        if client.has_collection(COLLECTION):
            client.drop_collection(COLLECTION)
            print(f"已删除 collection: {COLLECTION}")

    ensure_collection()
    chunks = load_all_chunks(chunk_files)

    # 主键: 文档集/文档名/chunk_index, 天然唯一
    for c in chunks:
        m = c["metadata"]
        c["pk"] = f"{c['dataset']}/{m['source']}/{m['chunk_index']}"

    # 断点续传: 跳过已入库 id
    existing = set()
    for batch_start in range(0, len(chunks), 1000):
        batch = chunks[batch_start:batch_start + 1000]
        existing |= {r["id"] for r in client.query(
            COLLECTION, filter=f'id in [{", ".join(chr(34) + c["pk"] + chr(34) for c in batch)}]',
            output_fields=["id"],
        )}
    todo = [c for c in chunks if c["pk"] not in existing]
    print(f"总切片 {len(chunks)}, 已入库 {len(chunks) - len(todo)}, 待处理 {len(todo)}")
    if not todo:
        print("全部已入库, 完成")
        return

    t0 = time.time()
    inserted = 0

    def build_rows(batch: list[dict], vectors: list[list[float]]) -> list[dict]:
        rows = []
        for c, v in zip(batch, vectors):
            m = c["metadata"]
            # 图片路径均为 ASCII, 字节数=字符数; 超长时丢弃末尾路径直到放得下
            imgs = m.get("images", [])
            while imgs and len(json.dumps(imgs, ensure_ascii=False).encode("utf-8")) > 2048:
                imgs = imgs[:-1]
            rows.append({
                "id": fit_utf8(c["pk"], 512),
                "text": fit_utf8(c["text"], 8192),
                "vector": v,
                "source": fit_utf8(m["source"], 512),
                "product": fit_utf8(m.get("product", "") or "", 256),
                "section": fit_utf8(m.get("section", "") or "", 256),  # 长中文标题按字节截
                "images": json.dumps(imgs, ensure_ascii=False),
                "chunk_index": m["chunk_index"],
            })
        return rows

    def process_batch(batch: list[dict]) -> None:
        """embedding + 插入; 整批反复超时(可能某批文本太长)时折半递归, 单条失败才终止。"""
        nonlocal inserted
        try:
            vectors = embed([c["text"] for c in batch])
        except RuntimeError:
            if len(batch) == 1:
                raise
            mid = len(batch) // 2
            print(f"  批次({len(batch)}条)反复超时, 折半为 {mid}+{len(batch)-mid} 重试")
            process_batch(batch[:mid])
            process_batch(batch[mid:])
            return
        client.insert(COLLECTION, build_rows(batch, vectors))
        inserted += len(batch)

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        process_batch(batch)
        done = min(i + BATCH, len(todo))
        speed = done / (time.time() - t0)
        eta = (len(todo) - done) / speed if speed > 0 else 0
        print(f"[{done}/{len(todo)}] 已插入 {inserted} 条, {speed:.1f}条/s, 预计剩余 {eta:.0f}s")

    print(f"\n完成: 共插入 {inserted} 条 -> {COLLECTION}, 耗时 {time.time() - t0:.0f}s")
    print(f"collection 行数: {client.get_collection_stats(COLLECTION)['row_count']}")


if __name__ == "__main__":
    main()
