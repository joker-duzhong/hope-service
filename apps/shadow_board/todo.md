## 🛠️ 第一阶段：后端开发 (Backend - FastAPI 模块化单体)
**工作目录**: `/apps/shadow_board/`

### [ ] 1. 数据库模型定义 (`models.py`)
*   **AC1:** 严格继承 `core/database.py` 中的 `CoreModel`（自带 id, created_at, updated_at, is_deleted）。
*   **AC2:** 表名必须前缀隔离：
    *   `shadow_board_sessions`: 记录对话会话 (user_id, topic, status: `idle`|`scoring`|`speaking`|`done`)。
    *   `shadow_board_messages`: 记录单条消息 (session_id, role, content, is_finalized)。
*   **AC3:** 必须关联 `user_id`（但不强求物理外键，通过 `Depends(get_current_user)` 获取）。

### [ ] 2. Pydantic 验证模型 (`schemas.py`)
*   **AC1:** 定义入参：`SendMessageRequest` (包含 text)。
*   **AC2:** 定义出参：`MessageResponse`, `SessionStatusResponse`。
*   **AC3:** 严格使用 Pydantic V2 语法 (`Field`, `model_dump` 等)。

### [ ] 3. 核心业务服务 (`services.py`)
*   **AC1:** 编写 `start_new_session(user_id, topic)` 和 `get_session_history(session_id)`，仅包含 DB 逻辑。
*   **AC2:** 封装大模型调用 `llm_service.py`（放在本 app 目录下），包含两个方法：
    *   `evaluate_motivation(chat_history)` -> 返回 JSON (裁决使打分)。
    *   `generate_agent_reply_stream(role, chat_history)` -> 返回 AsyncGenerator 流。

### [ ] 4. 后台引擎与事件总线 (`tasks.py` - Celery Worker)
*   *核心逻辑：脱离 HTTP 请求的异步 AI 引擎。*
*   **AC1:** 创建一个 Celery 任务 `@celery_app.task(name="shadow_board_agent_loop")`。
*   **AC2:** 任务包含死循环逻辑：查 DB 历史 -> 调裁决使 -> 如果有人说话 -> 调大模型生成 -> 写回 DB -> 继续循环；如果没人说话 -> 状态设为 `done` -> 结束任务。
*   **AC3:** **关键事件广播：** 在大模型生成流式 Token 时，通过 Redis Pub/Sub（频道名为 `shadow_board_exec_stream_{session_id}`）推送实时字块 `{"chunk": "x"}`。

### [ ] 5. 路由与 API 端点 (`router.py`)
*   **AC1:** `POST /api/v1/shadow_board/chat` - 接收用户输入，将消息存入 DB，更新 Session 状态为 `scoring`，并**异步触发 (delay)** `run_agent_loop` Celery 任务。立刻返回标准 `ResponseModel(data={"session_id": xx})`。
*   **AC2:** `GET /api/v1/shadow_board/history` - 获取已完成的历史记录，返回 `ResponseModel`。
*   **AC3:** `GET /api/v1/shadow_board/status` - 获取当前状态，返回 `ResponseModel`。
*   **AC4:** `GET /api/v1/shadow_board/stream` - **不使用 ResponseModel**，而是返回 FastAPI 的 `StreamingResponse(media_type="text/event-stream")`。该接口监听对应的 Redis Pub/Sub 频道，实现 SSE 向前端推流。
*   **AC5:** 在 `main.py` 中通过 `app.include_router` 挂载，携带 Token 验证依赖。

### 💡 核心联调验收测试 (E2E AC)

1.  **正常循环测试：** CEO 发送一句话 -> 看到“评估中” -> 架构师开始打字 -> 架构师打完 -> 再次“评估中” -> 产品经理反驳 -> 结束，等待 CEO 再次发言。
2.  **断线重连测试（杀手锏）：**
    *   CEO 发送消息，当某角色正在流式输出时，**直接刷新浏览器 (F5)**。
    *   **期望结果：** 页面重新加载后，瞬间拉取已生成的记录，由于后台 Celery 未被中断且状态仍为 `speaking`，前端自动触发 SSE 接流，页面立刻继续出现打字机效果。不会丢失任何上下文！