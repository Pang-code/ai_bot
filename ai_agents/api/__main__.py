import logging

import uvicorn

from ai_agents.config import get_settings


def main() -> None:
    settings = get_settings()
    # 配置 root logger: 让应用日志 ([RAG检索]/[联网搜索] 等工具调用记录) 打到终端;
    # uvicorn 默认只配置自己的 logger, 不碰 root
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    uvicorn.run(
        "ai_agents.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
