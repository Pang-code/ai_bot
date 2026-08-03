from ddgs import DDGS
from langchain.tools import tool


@tool
def web_search(query: str) -> str:
    """使用 DuckDuckGo 搜索互联网，适合查询新闻、实时信息和外部资料。"""
    results = DDGS().text(query, max_results=5)
    if not results:
        return "没有找到相关结果。"

    return "\n\n".join(
        f"标题：{item.get('title', '')}\n"
        f"链接：{item.get('href', '')}\n"
        f"摘要：{item.get('body', '')}"
        for item in results
    )
