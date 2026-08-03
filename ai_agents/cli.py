from ai_agents.agent import build_agent
from ai_agents.config import Settings


def main() -> None:
    try:
        settings = Settings.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from None

    agent = build_agent(settings)
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
