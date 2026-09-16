"""RAG 知识库检索工具: bge-m3 向量化问题 -> Milvus 检索 -> 去重/配额裁剪。

查询中出现产品型号(如 b5-440/G540/hak180)时, 先在该产品的 chunk 范围内
过滤检索(避免 23 份手册的近似重复章节挤占名额), 不足 top_k 再全局补齐。
纯排序逻辑 (rank_hits/format_hits) 与外部 IO (retrieve_hits) 分离,
服务不可用时工具优雅降级, 不阻断普通对话。
"""

import json
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
RE_MODEL_TOKEN = re.compile(r"[a-z0-9]{4,}")


def normalize_text(text: str) -> str:
    return RE_FINGERPRINT.sub("", text.lower())


def text_fingerprint(text: str) -> str:
    return normalize_text(text)[:100]


def extract_model_tokens(query: str) -> list[str]:
    """提取查询中的型号 token: 长度>=4 的字母数字串, 排除纯数字(年份等)。

    "b5 440怎么连接打印机" -> ["b5440"]; "扩展坞怎么用" -> []
    """
    tokens = []
    for run in RE_MODEL_TOKEN.findall(normalize_text(query)):
        if any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
            tokens.append(run)
    return tokens


def match_products(query: str, known_products) -> list[str]:
    """按型号 token 匹配查询涉及的已知产品。

    只匹配字母数字型号, 不做中文产品名匹配(如"擎云"会命中全部 23 份手册, 无过滤价值)。
    """
    tokens = extract_model_tokens(query)
    if not tokens:
        return []
    return [p for p in known_products if any(t in normalize_text(p) for t in tokens)]


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


@lru_cache(maxsize=4)
def _known_products(uri: str, collection: str) -> tuple[str, ...]:
    """库内全部产品名(缓存; 重新入库新文档后需重启进程刷新)。"""
    client = _milvus_client(uri)
    rows = client.query(
        collection, filter='product != ""', output_fields=["product"], limit=16384
    )
    return tuple(sorted({r["product"] for r in rows if r.get("product")}))


def embed_query(query: str, settings: Settings) -> list[float]:
    resp = requests.post(
        f"{settings.rag_embed_url.rstrip('/')}/api/embed",
        json={"model": settings.rag_embed_model, "input": [query]},
        timeout=EMBED_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def retrieve_hits(query: str) -> list[dict] | None:
    """执行 embedding + Milvus 检索, 返回最终排序结果(已过滤/去重/配额/截断)。

    查询含产品型号时先在该产品范围内过滤检索(结果优先), 不足 top_k 再全局补齐。
    任何外部故障返回 None (调用方优雅降级)。
    """
    settings = get_settings()
    try:
        vector = embed_query(query, settings)
        client = _milvus_client(settings.rag_milvus_uri)

        def _search(expr: str) -> list[dict]:
            return client.search(
                settings.rag_collection,
                data=[vector],
                limit=settings.rag_recall,
                output_fields=["text", "source", "product", "section"],
                filter=expr,
            )[0]

        products = match_products(
            query, _known_products(settings.rag_milvus_uri, settings.rag_collection)
        )
        selected: list[dict] = []
        if products:
            logger.info('[RAG检索] query="%s" 产品过滤: %s', query, products)
            selected = rank_hits(
                _search(f"product in {json.dumps(products, ensure_ascii=False)}"),
                score_threshold=settings.rag_score_threshold,
                top_k=settings.rag_top_k,
                per_source_cap=settings.rag_per_source_cap,
            )

        if len(selected) < settings.rag_top_k:
            seen = {text_fingerprint(h["entity"].get("text", "")) for h in selected}
            for hit in rank_hits(
                _search(""),
                score_threshold=settings.rag_score_threshold,
                top_k=settings.rag_top_k + len(selected),  # 补齐目标 + 已占名额
                per_source_cap=settings.rag_per_source_cap,
            ):
                if len(selected) >= settings.rag_top_k:
                    break
                fingerprint = text_fingerprint(hit["entity"].get("text", ""))
                if fingerprint and fingerprint in seen:
                    continue
                seen.add(fingerprint)
                selected.append(hit)
        return selected
    except Exception:  # noqa: BLE001
        logger.exception("[RAG检索] 检索失败")
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
    if not hits:
        logger.info('[RAG检索] query="%s" 无相关结果', query)
        return NO_MATCH_MESSAGE

    detail = ", ".join(
        f"{h['entity'].get('source', '?')}({h['distance']:.2f})" for h in hits
    )
    logger.info('[RAG检索] query="%s" 返回 %d 条: %s', query, len(hits), detail)
    return format_hits(hits)
