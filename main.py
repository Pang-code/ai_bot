import os
from datetime import datetime

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver


@tool
def get_current_time() -> str:
    """获取当前系统时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


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
        tools=[get_current_time],
        system_prompt="你是一个简洁、可靠的中文助手。",
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
