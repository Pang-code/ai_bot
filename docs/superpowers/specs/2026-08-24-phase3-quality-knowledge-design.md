# 第三阶段：质量与知识 — 设计规格

> 状态：待用户审阅  
> 日期：2026-08-24  
> 前置：第一阶段（服务化与持久化）、第二阶段（治理与安全）已完成

## 1. 目标与非目标

### 交付目标

每次变更（Prompt、工具、模型、检索策略）都有**可量化的质量证据**，而不是凭感觉上线。

### 非目标（本阶段不做）

- 多智能体协作、自动任务规划
- 一上来就上向量数据库（无明确私有语料前不做）
- 完整可观测平台替换（LangSmith 全量接入可后置；先用本地评测产物 + 现有审计）
- 第四阶段能力：队列、水平扩容、成本预算系统

## 2. 推进原则

1. **评测先行**：没有基线，不谈「变好了」。
2. **RAG 有门槛**：必须有明确语料清单与负责人；否则停留在搜索 + 引用校验。
3. **小步可回滚**：Prompt / 模型以版本标识切换；评测对比新旧版本。
4. **复用现有栈**：FastAPI、PostgreSQL Checkpointer、审计事件、现有工具白名单与 URL 过滤。

## 3. 子阶段总览

| 子阶段 | 名称 | 依赖 | 完成标准 |
|--------|------|------|----------|
| 3.1 | 评测基线 | 现有 agent + tools | 固定用例集可 CLI/CI 跑通；输出通过率与失败明细 |
| 3.2 | Prompt 版本 | 3.1 | Prompt 迁出硬编码；可按版本绑定并对比评测 |
| 3.3 | 引用与反馈 | 3.1（建议在 3.2 后） | 搜索类回答可校验链接；👍/👎 可落库追溯 |
| 3.4 | 模型路由 + 可选 RAG | 3.1–3.3 | 按规则选模型；RAG 仅在语料就绪后启动 |

推荐实现顺序严格按 3.1 → 3.2 → 3.3 → 3.4。每个子阶段单独可合并、可演示。

---

## 4. 子阶段 3.1：评测基线

### 4.1 问题形态

当前智能体能力边界清晰：时间、联网搜索、IP 定位（需 HITL）、普通闲聊。评测应覆盖这些路径，而不是开放域百科全集。

### 4.2 用例数据格式

路径建议：`evals/cases/*.yaml`（或单个 `evals/suite.yaml`）。

每条用例至少包含：

| 字段 | 说明 |
|------|------|
| `id` | 稳定 ID，如 `search.weather.basic` |
| `input` | 用户输入 |
| `expect.tools_any_of` / `expect.tools_none` | 期望调用的工具集合约束 |
| `expect.answer_contains` / `expect.answer_not_contains` | 可选关键词断言 |
| `expect.must_cite_url` | 搜索类是否要求出现 `http` 链接 |
| `tags` | 如 `search` / `time` / `safety` / `approval` |
| `approval` | 可选：模拟 HITL `approve` / `reject` |

示例（示意）：

```yaml
- id: time.now
  input: 现在几点了
  expect:
    tools_any_of: [get_current_time]
  tags: [time]

- id: search.with_source
  input: 用搜索查一下今天有什么科技新闻标题
  expect:
    tools_any_of: [web_search]
    must_cite_url: true
  tags: [search]

- id: location.needs_approval
  input: 我现在大概在哪
  expect:
    tools_any_of: [get_ip_location]
  approval: approve
  tags: [approval]
```

### 4.3 运行方式

- 入口：`uv run python -m ai_agents.eval`（或 `scripts/run_evals.py`）
- 每个用例使用**独立 `thread_id`**，避免会话污染（已知 DashScope 会对脏历史整包拒答）
- 对 HITL：评测运行器自动 `Command(resume=...)`，不依赖前端
- 输出：`evals/results/<timestamp>.json` + 终端摘要（总数 / 通过 / 失败 / 按 tag 分解）

### 4.4 判定器（先规则，后模型打分）

第一版只用**确定性规则**：

1. 工具名集合是否满足约束  
2. 文本包含/不包含  
3. 是否出现合法 URL（可复用 `is_safe_public_url` 的宽松变体：仅检查 scheme+host 出现即可）

第二版（可选增强，不阻塞 3.1）：对开放题用「评分模型」做 0/1 相关性；默认关闭。

### 4.5 与仓库集成

- `tests/` 继续放快单元测试（config、URL、API）
- Eval 默认**不进**每次 `pytest`（贵、慢、依赖外网与模型）；提供 `make eval` / CI 手工或 nightly job
- 文档：`README.md` 增加「如何跑评测」小节

### 4.6 风险

- 外网搜索不稳定 → 允许用例标记 `flaky: true` 或对搜索类只断言「调用了 web_search」，不断言具体新闻内容  
- 模型商内容审核 → 用例输入与期望避免敏感词；评测失败需区分 `provider_blocked` 与 `assertion_failed`

---

## 5. 子阶段 3.2：Prompt 版本管理

### 5.1 现状

`SYSTEM_PROMPT` 硬编码在 `ai_agents/agent.py`。变更无版本号、难回滚、难与评测绑定。

### 5.2 设计

- Prompt 存放：`prompts/<name>/<version>.md`（或 `.txt`），另有 `prompts/<name>/current` 指针文件（内容为版本号字符串）
- 配置：`.env` / Settings 增加可选 `PROMPT_NAME`（默认 `assistant`）、`PROMPT_VERSION`（默认读 `current`）
- `build_agent` 通过加载器读取文本；加载失败则启动失败（显式，不静默回退到过期硬编码）
- 审计：可选在 `audit_events` 的 metadata 中记录 `prompt_version`（chat 成功时）

### 5.3 评测联动

```bash
uv run python -m ai_agents.eval --prompt-version 2026-08-24
uv run python -m ai_agents.eval --prompt-version 2026-08-25 --compare-with 2026-08-24
```

对比输出：同 `id` 的 pass/fail 变化表。

### 5.4 不做

- 不做完整 Prompt CMS / 管理后台（本阶段文件 + git 即可）
- 不做按租户自定义 Prompt（可列为后续；会触及多租户产品决策）

---

## 6. 子阶段 3.3：引用校验与反馈闭环

### 6.1 引用校验

**策略（轻量）**：

1. Prompt 明确要求：使用 `web_search` 后，回答中附上结果里出现过的链接  
2. 运行时或评测时：从最近一轮 `ToolMessage(web_search)` 解析 URL 集合 `S`；从最终回答提取 URL 集合 `A`；要求 `A ⊆ S` 且（若 `must_cite_url`）`A` 非空  
3. 线上可选：校验失败时不阻断回答，但写审计事件 `citation.invalid`（避免误杀）；评测中则记失败

### 6.2 用户反馈

- 前端：每条助手消息旁 👍 / 👎（可选附短文本）
- API：`POST /v1/sessions/{id}/messages/{message_id}/feedback`  
  body：`{ "rating": "up"|"down", "comment": "..." }`
- 存储：新表 `message_feedback`（tenant_id、user_id、session_id、message_id、rating、comment、created_at）或写入 `audit_events`（若希望少表，可用审计；独立表更利于聚合）
- 用途：人工抽检 → 沉淀进 `evals/cases`；不在本阶段做自动 fine-tune

### 6.3 与脏会话问题的关系

长会话 + 低质搜索摘要会触发上游内容审核。3.3 不强制做摘要压缩，但设计上预留：

- 评测始终新 thread  
- 产品侧可后续加「上下文裁剪 / 工具结果截断」作为可靠性修复（可挂在 3.3 末或单独 hotfix，不阻塞反馈 API）

---

## 7. 子阶段 3.4：模型路由与可选 RAG

### 7.1 模型路由

- Settings 支持多模型配置：`MODEL_DEFAULT`、可选 `MODEL_FAST` / `MODEL_STRONG`（名称 + base_url + api_key 可共用）
- 路由规则第一版用**显式规则**，不用分类模型：
  - 默认：`MODEL_DEFAULT`
  - 含「详细分析 / 长文」等标签或评测指定：`MODEL_STRONG`
  - 纯工具探测用例：`MODEL_FAST`
- 路由决策写入审计 metadata：`model_name`

后续可改为轻量分类器；第一版禁止隐式魔法。

### 7.2 RAG（有条件启动）

**启动门槛（全部满足才做）：**

1. 有书面语料清单（文档路径/负责人/更新频率）  
2. 语料以租户隔离（沿用 `tenant_id`）  
3. 评测集中至少 10 条「必须靠私有知识才能答对」的用例  

**技术选择（门槛满足后）：**

- 第一刀：**Postgres 全文检索（或简单 chunk 表 + ILIKE/tsvector）**，不引入专用向量库  
- 工具形态：新增 `knowledge_search(query)`（租户范围），纳入现有工具策略与审计  
- 回答同样走引用校验：引用必须来自检索命中  

向量检索列为明确后续项，不在 3.4 必做范围。

---

## 8. 架构关系（逻辑）

```text
                    ┌─────────────┐
  用户 / CI ───────►│ Eval Runner │──► results/*.json
                    └──────┬──────┘
                           │ build_agent(prompt_version, model)
                           ▼
  Frontend ──► FastAPI ──► AgentService ──► LangGraph Agent
                 │              │                │
                 │              │                ├─ tools (time/search/location[/knowledge])
                 │              │                └─ Postgres Checkpointer (thread_id)
                 │              ├─ Session messages (展示)
                 │              └─ Audit + Feedback
                 └─ Auth / Tenant / HITL（第二阶段已有）
```

## 9. 文件与模块预估（实现时再落计划）

| 区域 | 预期新增/调整 |
|------|----------------|
| `evals/` | 用例与结果目录 |
| `ai_agents/eval/` | 加载用例、运行、判定、对比 |
| `prompts/` | 版本化 Prompt |
| `ai_agents/prompts.py` | 加载器 |
| `ai_agents/agent.py` | 改为注入 Prompt / 模型 |
| `ai_agents/api/` | feedback 路由；可选 citation 审计 |
| `frontend/` | 反馈按钮 |
| `README.md` | 评测与 Prompt 版本说明 |

## 10. 成功度量

| 指标 | 目标 |
|------|------|
| 核心用例通过率 | 建立基线后，主分支不因变更静默掉下（CI/发布前对比） |
| Prompt 变更 | 每次有版本号 + 评测对比记录 |
| 搜索回答 | 评测集中 `must_cite_url` 用例通过 |
| 反馈 | 负反馈可关联到 session/message，并能复现进用例 |
| RAG | 未达门槛则标记为「未启动」而非半成品 |

## 11. 开放决策（实现前可再确认）

1. Eval 是否进 GitHub Actions（建议先 local + 手动，稳定后再 nightly）  
2. Feedback 用独立表还是只写 `audit_events`（建议独立表）  
3. 3.4 的多模型是否同一供应商兼容接口（当前已是 OpenAI compatible，保持即可）

---

## 12. 审批记录

- 总切分方案（评测先行 3.1→3.4）：用户已同意（2026-08-24）  
- 本规格文档：待用户审阅确认后再写 implementation plan
