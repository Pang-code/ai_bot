from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from ai_agents.config import Settings
from ai_agents.tools import TOOLS


SYSTEM_PROMPT = (
    "你是一个简洁、可靠的中文助手。遇到实时信息或不确定的外部知识时，"
    "使用 web_search 搜索并附上来源链接。仅在用户需要当前位置时调用 "
    "get_ip_location，并说明 IP 定位可能不准确。"
)


def build_agent(settings: Settings, checkpointer: Any | None = None):
    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key.get_secret_value(),
        base_url=settings.model_base_url,
        timeout=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
    )
    return create_agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer or InMemorySaver(),
    )
