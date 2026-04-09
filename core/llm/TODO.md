# 任务目标：基于《后端宇宙架构规范》开发 LLM 核心能力模块

你好，AI 助手。请严格遵守我的《后端宇宙架构规范》，完成以下两个独立功能模块的开发。
注意：当前项目中已经存在完整的 `ResponseModel`, `PaginatedResponse`, 基础 `CoreModel`, `get_current_user` 依赖, `Celery` 与 `Redis`。
请分步骤阅读以下 TODO List，并在编码时严格落实**验收标准**。

---

## 🟢 任务一：LLM 大模型基础聊天功能

**架构定位**：底层驱动引擎及 Prompt 封装放入 `core/llm/`（供全局复用），对外暴露的 HTTP API 接口和会话模型放入新建的独立业务模块 `apps/ai_gateway/`。

### TODO 1.1 全局配置更新 (`core/config.py`)
- [ ] 在 `Settings` 类中增加 LLM 相关的环境变量支持。
- **验收标准**：
  - 支持配置多个模型提供商（建议使用 Pydantic 的 `Dict` 或嵌套模型解析，例如支持配置 OpenAI、阿里云千问等兼容 OpenAI 接口的服务商）。
  - 配置项至少包含：`provider_name`, `base_url`, `api_key`, `default_model`, `timeout_seconds`, `max_retries`。

### TODO 1.2 核心能力封装 (`core/llm/engine.py` & `core/llm/prompts.py`)
- [ ] 封装基于 OpenAI 协议的通用异步客户端（使用 `httpx` 或官方 `openai` 库的 `AsyncOpenAI`）。
- [ ] 实现基础系统 Prompt 拦截器（内容审计）。
- **验收标准**：
  - **核心逻辑不在 API 路由中**。提供统一的 `async def generate_chat()` 和 `async def generate_stream_chat()` 方法。
  - **重试与超时**：利用 `tenacity` 库实现 `max_retries` 失败重试；确保有网络超时控制避免拥塞。
  - **合规约束**：在 `prompts.py` 中内置一个 Base System Prompt（要求模型遵守中国法律法规，拒绝回答敏感政治、暴力、违法问题），并在每次组装 messages 时自动静默前置。

### TODO 1.3 会话与消息数据库模型 (`apps/ai_gateway/models.py`)
- [ ] 新建会话表 `ai_sessions` 和 消息表 `ai_messages`，继承全局 `CoreModel`。
- **验收标准**：
  - 表名带有 `ai_` 前缀。
  - `ai_sessions` 包含：`user_id` (关联用户), `title` (会话标题), `provider` (当前使用的模型商), `model_name`。
  - `ai_messages` 包含：`session_id` (所属会话), `role` (user/assistant/system), `content` (消息内容), `tokens_used` (消耗token数量，可选)。

### TODO 1.4 会话管理与上下文 Service (`apps/ai_gateway/services.py`)
- [ ] 实现会话 CRUD，以及通过会话 ID 自动提取历史消息上下文的逻辑。
- **验收标准**：
  - 提取上下文时，根据配置的最大上下文条数（如最近 10 条）进行截断，拼装成 OpenAI 标准的 `[{"role": "...", "content": "..."}]` 格式。

### TODO 1.5 API 路由与流式响应 (`apps/ai_gateway/router.py`)
- [ ] 暴露 `/chat/completions` (一次性/流式对话接口) 和 `/sessions` (会话管理接口)。
- **验收标准**：
  - 必须使用 `Depends(get_current_user)` 进行权限校验。
  - 支持 SSE (Server-Sent Events) 流式输出响应（使用 `StreamingResponse`）。
  - **非阻塞落库**：在流式输出结束时，必须将用户的提问和 AI 的完整回答保存到 `ai_messages` 表中。为了不影响流式体验，落库操作必须使用 FastAPI 的 `BackgroundTasks` 或扔进 Celery 执行。

---
