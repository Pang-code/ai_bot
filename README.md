## LangGraph 智能体服务

项目使用 Python 3.13、uv 和最新的 LangGraph，包含一个可查询当前时间、
通过 DuckDuckGo 免费联网搜索，并通过 ipwho.is 和 ipapi.co 交叉获取 IP
大致位置的 OpenAI API 兼容智能体。

FastAPI 服务使用 PostgreSQL Checkpointer 持久化对话状态，并提供 Argon2
密码哈希、JWT access token、多租户 RBAC、人工工具审批和追加式审计日志。
会话按租户和创建用户隔离。CLI 调试入口仍使用内存状态。

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

必须将 `JWT_SECRET` 替换为至少 32 字符的加密随机值，例如
`openssl rand -hex 32`。模板中的值只能作为“必须替换”提示，不能用于部署。
可通过 `JWT_ACCESS_TOKEN_MINUTES`、`JWT_ISSUER` 和 `JWT_AUDIENCE` 调整令牌。
数据库初始化会以 `CREATE TABLE IF NOT EXISTS` 和 `ALTER TABLE ... IF NOT
EXISTS` 建立用户、租户、成员、审计及新版会话字段，兼容已有会话表。

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

先注册（注册会自动创建首个租户并授予 owner）：

```bash
curl -X POST http://127.0.0.1:8000/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@example.com","password":"替换为至少8位密码","tenant_name":"我的团队"}'
```

也可通过 `POST /v1/auth/login` 登录。保存响应中的 `access_token`，然后通过
`GET /v1/tenants` 获取租户 ID。聊天请求必须同时携带 Bearer token 和
`X-Tenant-ID`；首次不传 `session_id`，后续传回即可延续：

```bash
curl -X POST http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer 替换为访问令牌' \
  -H 'X-Tenant-ID: 替换为租户UUID' \
  -d '{"message":"你好"}'
```

### 会话历史接口

匿名 `X-Client-ID` 已移除。所有会话 ID 都会再次校验 JWT 用户、租户成员关系
和会话创建者，不能仅凭 UUID 越权读取或审批。

- `GET /v1/sessions`：按最近更新时间查询会话列表
- `GET /v1/sessions/{session_id}/messages`：查询会话及消息详情
- `DELETE /v1/sessions/{session_id}`：删除业务消息和 LangGraph Checkpoint

会话标题取自第一条用户消息，并记录创建时间与最近更新时间。

### 租户、审批与审计

- `GET /v1/auth/me`：当前用户
- `GET /v1/tenants`：用户已加入的租户
- `POST /v1/tenants/{tenant_id}/members`：owner/admin 按邮箱添加已注册用户
- `GET /v1/audit-events`：owner/admin/auditor 查询当前租户审计

角色包括 owner、admin、member、auditor。仅 `get_ip_location` 经过
LangChain `HumanInTheLoopMiddleware`，允许 approve/reject；时间和搜索自动
执行。聊天响应为 `pending_approval` 时，调用
`POST /v1/sessions/{session_id}/approval`，请求体为
`{"decision":"approve"}` 或 `{"decision":"reject"}`。恢复使用原
session/thread，且审批者必须是该会话的创建用户及租户成员。

审计记录登录成功/失败、会话创建/删除、成员添加及审批决定；不写入密码、JWT、
模型密钥或完整敏感工具参数。

### 安全边界

DuckDuckGo 查询限制长度和结果数量，输出只保留有限标题、URL 和摘要。URL 只
允许无凭据的 HTTP(S) 公网目标，拒绝 localhost、私网、环回和链路本地地址。
搜索内容始终视为不可信数据，不能覆盖系统指令。IP 定位只访问代码中固定的
HTTPS 服务；批准定位意味着当前公网 IP 会发送给这些第三方服务。

Vue 前端在本阶段将 JWT 存入 `localStorage`，适合本地开发但无法抵御成功的
XSS。生产环境应改为后端设置的 `Secure`、`HttpOnly`、`SameSite` Cookie，
同时配套 CSRF 防护和更短令牌/刷新令牌策略。

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
uv run python -m compileall -q ai_agents
cd frontend && npm run build
```
