## LangGraph 智能体服务

项目使用 Python 3.13、uv 和最新的 LangGraph，包含一个可查询当前时间、
通过 DuckDuckGo 免费联网搜索，并通过 ipwho.is 和 ipapi.co 交叉获取 IP
大致位置的 OpenAI API 兼容智能体。

FastAPI 服务使用 PostgreSQL Checkpointer 持久化对话状态。客户端保存并重复
传入 `session_id` 后，服务重启仍可恢复相同会话。CLI 调试入口仍使用内存状态。

IP 定位不需要 API Key，但会把当前公网 IP 暴露给两个定位服务，且结果不具备
GPS 精度。

### 项目结构

```text
ai_agents/
├── agent.py           # 模型和 LangGraph 智能体组装
├── api/               # FastAPI 路由、Schema 和异常处理
├── cli.py             # 命令行调试入口
├── config.py          # 结构化环境配置
├── persistence/       # PostgreSQL Checkpointer
├── service.py         # 智能体服务层
└── tools/
    ├── location.py    # IP 定位
    ├── search.py      # DuckDuckGo 搜索
    └── time.py        # 当前时间
tests/                 # API 和配置测试
docker-compose.yml     # 已有本地开发基础设施
frontend/              # Vue 3 + TypeScript 聊天界面
```

### 配置

复制配置模板并填写模型信息：

```bash
cp .env.example .env
```

API 还需要 `DATABASE_URL`。本地开发可直接使用 `docker-compose.yml` 中已有的
`dev-postgres`，并按其中的数据库名、用户和密码填写连接地址。

### 快速启动（前后端）

确保 `dev-postgres` 容器正在运行，然后打开两个终端。

终端 1，启动后端：

```bash
API_PORT=8002 uv run python -m ai_agents.api
```

终端 2，启动前端：

```bash
cd frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。

### 启动 API

```bash
uv run python -m ai_agents.api
```

如果本机 `8000` 端口已被其他程序占用，可以临时指定其他端口：

```bash
API_PORT=8002 uv run python -m ai_agents.api
```

接口文档位于 `http://127.0.0.1:8000/docs`，健康检查位于
`/health/live` 和 `/health/ready`。使用 `API_PORT=8002` 启动时，将地址中的
端口对应改为 `8002`。

首次对话不传 `session_id`，服务会自动创建：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Client-ID: 11111111-1111-4111-8111-111111111111' \
  -d '{"message":"你好"}'
```

后续请求传回响应中的 `session_id` 即可延续同一会话：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Client-ID: 11111111-1111-4111-8111-111111111111' \
  -d '{"message":"继续刚才的话题","session_id":"替换为上次返回的 UUID"}'
```

### 会话历史接口

聊天和会话接口要求传入 UUID 格式的 `X-Client-ID` 请求头，用于隔离不同
客户端的会话。Vue 前端会自动生成并保存该 ID；它只是匿名客户端标识，不代替
正式的用户鉴权。

- `GET /v1/sessions`：按最近更新时间查询会话列表
- `GET /v1/sessions/{session_id}/messages`：查询会话及消息详情
- `DELETE /v1/sessions/{session_id}`：删除业务消息和 LangGraph Checkpoint

会话标题取自第一条用户消息，并记录创建时间与最近更新时间。

### CLI 调试

CLI 不依赖 PostgreSQL，退出后不会保留状态：

```bash
uv run python -m ai_agents
```

输入 `quit` 或 `exit` 退出。

### 启动 Vue 前端

前端开发代理默认连接 `http://127.0.0.1:8002`，先确保 API 已在该端口运行：

```bash
API_PORT=8002 uv run python -m ai_agents.api
```

在另一个终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。如果后端使用其他端口，复制
`frontend/.env.example` 为 `frontend/.env`，再修改
`VITE_API_PROXY_TARGET`。

生产环境构建：

```bash
cd frontend
npm run build
```

### 测试

```bash
uv run pytest
```
