from uuid import uuid4

from pydantic import ValidationError

from ai_agents.agent import build_agent
from ai_agents.config import get_settings


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError:
        raise SystemExit("配置无效，请检查 .env 中的模型配置。") from None

    agent = build_agent(settings)
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}

    print(f"智能体已启动，会话 ID：{session_id}")
    print("输入 quit 或 exit 退出。")
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
