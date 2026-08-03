import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model_name: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        api_key = os.getenv("MODEL_API_KEY")
        base_url = os.getenv("MODEL_BASE_URL")
        model_name = os.getenv("MODEL_NAME")
        if not api_key or not base_url or not model_name:
            raise ValueError(
                "请先在 .env 中填写 MODEL_API_KEY、MODEL_BASE_URL 和 MODEL_NAME。"
            )

        return cls(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
        )
