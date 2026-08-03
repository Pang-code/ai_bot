## 最简 LangGraph 智能体

项目使用 Python 3.13、uv 和最新的 LangGraph，包含一个可查询当前时间、
通过 DuckDuckGo 免费联网搜索，并通过 ipwho.is 和 ipapi.co 交叉获取 IP
大致位置的 OpenAI API 兼容智能体。对话状态在程序运行期间保存在内存中。

IP 定位不需要 API Key，但会把当前公网 IP 暴露给两个定位服务，且结果不具备
GPS 精度。

### 项目结构

```text
ai_agents/
├── agent.py          # 模型和 LangGraph 智能体组装
├── cli.py            # 命令行交互
├── config.py         # .env 配置读取与校验
└── tools/
    ├── location.py   # IP 定位
    ├── search.py     # DuckDuckGo 搜索
    └── time.py       # 当前时间
main.py               # 程序入口
```

1. 在 `.env` 中填写模型的 `MODEL_API_KEY`、`MODEL_BASE_URL` 和
   `MODEL_NAME`。
2. 启动智能体：

```bash
uv run python -m ai_agents
```

输入 `quit` 或 `exit` 退出。
