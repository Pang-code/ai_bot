import uvicorn

from ai_agents.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "ai_agents.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
