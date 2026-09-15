import logging

from ddgs import DDGS
from langchain.tools import tool

from ai_agents.security.url import is_safe_public_url

logger = logging.getLogger("ai_agents.tools.search")

MAX_QUERY_LENGTH = 300
MAX_RESULTS = 5
MAX_TITLE_LENGTH = 200
MAX_SNIPPET_LENGTH = 800


@tool
def web_search(query: str) -> str:
    """使用 DuckDuckGo 搜索互联网，适合查询新闻、实时信息和外部资料。"""
    query = " ".join(query.split())
    if not query:
        return "搜索关键词不能为空。"
    if len(query) > MAX_QUERY_LENGTH:
        return f"搜索关键词过长，最多允许 {MAX_QUERY_LENGTH} 个字符。"
    try:
        results = DDGS().text(query, max_results=MAX_RESULTS)
    except Exception as e:
        logger.warning('[联网搜索] query="%s" 搜索服务不可用: %s', query, e)
        return "搜索服务暂时不可用，请稍后重试。"
    if not results:
        logger.info('[联网搜索] query="%s" 无结果', query)
        return "没有找到相关结果。"

    safe_results = []
    for item in results[:MAX_RESULTS]:
        url = str(item.get("href", ""))[:2048]
        if not is_safe_public_url(url, resolve_dns=True):
            continue
        title = str(item.get("title", ""))[:MAX_TITLE_LENGTH]
        snippet = str(item.get("body", ""))[:MAX_SNIPPET_LENGTH]
        safe_results.append(f"标题：{title}\n链接：{url}\n摘要：{snippet}")
    logger.info('[联网搜索] query="%s" 返回 %d 条安全结果', query, len(safe_results))
    return "\n\n".join(safe_results) or "没有找到安全的公开网页结果。"
