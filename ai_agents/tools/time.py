from datetime import datetime

from langchain.tools import tool


@tool
def get_current_time() -> str:
    """获取当前系统时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")
