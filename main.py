import json
import os
from datetime import datetime
from urllib.request import Request, urlopen

from ddgs import DDGS
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver


@tool
def get_current_time() -> str:
    """获取当前系统时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def _fetch_location(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "ai-agents/0.1"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


@tool
def get_ip_location() -> str:
    """通过公网 IP 获取大致位置；结果仅精确到国家、地区或城市，不是 GPS 定位。"""
    services = {
        "ipwho.is": "https://ipwho.is/",
        "ipapi.co": "https://ipapi.co/json/",
    }
    locations = {}

    for name, url in services.items():
        try:
            data = _fetch_location(url)
            locations[name] = {
                "ip": data.get("ip"),
                "country": data.get("country") or data.get("country_name"),
                "region": data.get("region"),
                "city": data.get("city"),
                "postal": data.get("postal"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "timezone": (
                    data.get("timezone", {}).get("id")
                    if isinstance(data.get("timezone"), dict)
                    else data.get("timezone")
                ),
                "organization": data.get("connection", {}).get("org")
                or data.get("org"),
            }
        except Exception as error:
            locations[name] = {"error": f"{type(error).__name__}: {error}"}

    return json.dumps(locations, ensure_ascii=False, indent=2)


def main() -> None:
    load_dotenv()

    api_key = os.getenv("MODEL_API_KEY")
    base_url = os.getenv("MODEL_BASE_URL")
    model_name = os.getenv("MODEL_NAME")
    if not all((api_key, base_url, model_name)):
        raise SystemExit(
            "请先在 .env 中填写 MODEL_API_KEY、MODEL_BASE_URL 和 MODEL_NAME。"
        )

    model = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
    )
    agent = create_agent(
        model=model,
        tools=[get_current_time, web_search, get_ip_location],
        system_prompt=(
            "你是一个简洁、可靠的中文助手。遇到实时信息或不确定的外部知识时，"
            "使用 web_search 搜索并附上来源链接。仅在用户需要当前位置时调用 "
            "get_ip_location，并说明 IP 定位可能不准确。"
        ),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "cli-session"}}

    print("智能体已启动，输入 quit 或 exit 退出。")
    while True:
        user_input = input("\n你：").strip()
        if user_input.lower() in {"quit", "exit"}:
            break
        if not user_input:
            continue

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            config=config,
        )
        print(f"智能体：{result['messages'][-1].content}")


if __name__ == "__main__":
    main()
