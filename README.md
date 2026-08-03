## 最简 LangGraph 智能体

项目使用 Python 3.13、uv 和最新的 LangGraph，包含一个可查询当前时间、
通过 DuckDuckGo 免费联网搜索的 OpenAI API 兼容智能体。对话状态在程序
运行期间保存在内存中。

1. 在 `.env` 中填写模型的 `MODEL_API_KEY`、`MODEL_BASE_URL` 和
   `MODEL_NAME`。
2. 启动智能体：

```bash
uv run python main.py
```

输入 `quit` 或 `exit` 退出。
