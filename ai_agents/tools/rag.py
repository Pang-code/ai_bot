"""RAG 知识库检索工具: bge-m3 向量化问题 -> Milvus 检索 -> 去重/配额裁剪。

纯排序逻辑 (rank_hits/format_hits) 与外部 IO (retrieve_hits) 分离,
服务不可用时工具优雅降级, 不阻断普通对话。
"""

import logging
import re
from functools import lru_cache

import requests
from langchain.tools import tool

from ai_agents.config import Settings, get_settings

logger = logging.getLogger("ai_agents.tools.rag")

MAX_QUERY_LENGTH = 300
EMBED_TIMEOUT_SECONDS = 30
UNAVAILABLE_MESSAGE = "知识库暂时不可用（向量服务或 Milvus 连接失败），请稍后重试或直接根据已有信息回答。"
NO_MATCH_MESSAGE = "知识库中没有找到与该问题相关的内容。"

# 指纹: 只保留字母数字与中日韩文字, 消除空白/标点(含全半角)差异
RE_FINGERPRINT = re.compile(r"[^\w\u4e00-\u9fff]+")


def text_fingerprint(text: str) -> str:
    return RE_FINGERPRINT.sub("", text)[:100]


def rank_hits(
    hits: list[dict],
    score_threshold: float,
    top_k: int,
    per_source_cap: int,
) -> list[dict]:
    """阈值过滤 -> 文本指纹去重(保留高分) -> 单来源配额 -> 截取 top_k。

    hits 元素: {"distance": 相似度, "entity": {"text","source","product","section"}}
    """
    selected: list[dict] = []
    seen_fingerprints: set[str] = set()
    per_source: dict[str, int] = {}

    for hit in sorted(hits, key=lambda h: h["distance"], reverse=True):
        if hit["distance"] < score_threshold:
            continue  # 已按分数降序, 后续均低于阈值
        entity = hit["entity"]
        source = entity.get("source", "")
        if per_source.get(source, 0) >= per_source_cap:
            continue
        fingerprint = text_fingerprint(entity.get("text", ""))
        if fingerprint and fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        per_source[source] = per_source.get(source, 0) + 1
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected


def format_hits(hits: list[dict]) -> str:
    blocks = []
    for hit in hits:
        entity = hit["entity"]
        product = entity.get("product") or "未标注产品"
        section = entity.get("section") or "未标注章节"
        blocks.append(
            f"【来源：{product}｜章节：{section}】（相似度 {hit['distance']:.2f}）\n"
            f"{entity.get('text', '')}"
        )
    return "\n\n".join(blocks)


@lru_cache(maxsize=1)
def _milvus_client(uri: str):
    from pymilvus import MilvusClient  # 惰性导入, 保证无 pymilvus/离线时工具模块可加载

    return MilvusClient(uri=uri)


def embed_query(query: str, settings: Settings) -> list[float]:
    resp = requests.post(
        f"{settings.rag_embed_url.rstrip('/')}/api/embed",
        json={"model": settings.rag_embed_model, "input": [query]},
        timeout=EMBED_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def retrieve_hits(query: str) -> list[dict] | None:
    """执行 embedding + Milvus 检索。任何外部故障返回 None (调用方优雅降级)。"""
    settings = get_settings()
    try:
        vector = embed_query(query, settings)
        return _milvus_client(settings.rag_milvus_uri).search(
            settings.rag_collection,
            data=[vector],
            limit=settings.rag_recall,
            output_fields=["text", "source", "product", "section"],
        )[0]
    except Exception:
        return None


@tool
def search_knowledge_base(query: str) -> str:
    """检索内部知识库，适合查询产品设备使用说明、华为擎云等硬件手册、公司规章制度
    （如员工手册、考勤、离职流程）以及法律法规条文。回答时请注明检索结果中的来源。"""
    query = " ".join(query.split())
    if not query:
        return "检索问题不能为空。"
    if len(query) > MAX_QUERY_LENGTH:
        return f"检索问题过长，最多允许 {MAX_QUERY_LENGTH} 个字符。"

    hits = retrieve_hits(query)
    if hits is None:
        logger.warning('[RAG检索] query="%s" 知识库不可用（向量服务或 Milvus 连接失败）', query)
        return UNAVAILABLE_MESSAGE

    settings = get_settings()
    ranked = rank_hits(
        hits,
        score_threshold=settings.rag_score_threshold,
        top_k=settings.rag_top_k,
        per_source_cap=settings.rag_per_source_cap,
    )
    if not ranked:
        logger.info('[RAG检索] query="%s" 无相关结果 (召回 %d 条均低于阈值 %.2f)',
                    query, len(hits), settings.rag_score_threshold)
        return NO_MATCH_MESSAGE

    detail = ", ".join(
        f"{h['entity'].get('source', '?')}({h['distance']:.2f})" for h in ranked
    )
    logger.info('[RAG检索] query="%s" 返回 %d 条: %s', query, len(ranked), detail)
    return format_hits(ranked)
