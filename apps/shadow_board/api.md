# 影子董事会 (ShadowBoard AI) API 接口文档

**基础 URL 前缀**: `/api/v1/shadow-board`

> **认证说明**:
> 所有接口请求都需要在 Header 中携带 Token 验证：
> `Authorization: Bearer <your_jwt_token>`

---

## 1. 发送消息并触发辩论 (Chat)

接收用户的输入（CEO发言）。如果是首次对话，后台将创建新会话；如果是已有对话，就在现有会话中追载消息。此接口会立刻返回并异步触发后续角色的反应。

*   **路径**: `/chat`
*   **方法**: `POST`
*   **入参 (JSON Body)**:

```json
{
  "text": "我们需要在产品里加个转盘抽奖功能", // [必填] 会话内容
  "topic": "转盘功能讨论",                     // [可选] 新建会话时的主题名
  "session_id": "123e4567-e89b-12d3... "       // [可选] 现有会话的ID
}
```

*   **出参 (JSON Response)**:

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "session_id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "scoring",
    "message": "消息已送达董事会，请稍候。"
  }
}
```
*   **异常情况**:
    *   `400 Bad Request`: `董事会成员正在激烈讨论中，请等待结束后再发言` (防高频并发)
    *   `403 Forbidden`: `无权操作此会话`

---

## 2. 建立 SSE 流式监听 (Stream)

前端调用此接口，保持一个单向长连接（Sever-Sent Events），监听后台 AI 角色生成的流式文本。推荐使用支持自定义 Header 的库（如 `@microsoft/fetch-event-source`）以满足全局 Token 规范。

*   **路径**: `/stream`
*   **方法**: `GET`
*   **入参 (Query Params)**:
    *   `session_id`: string (UUID) [必填]

*   **出参 (Event Stream text/event-stream)**:返回实时的 SSE 消息，每个消息的 `data` 都是经过 JSON 序列化的字符串。
    
```text
data: {"session_id": "...", "role": "System", "status": "scoring"}

data: {"session_id": "...", "role": "PM", "status": "speaking"}

data: {"session_id": "...", "role": "PM", "chunk": "我觉"}
data: {"session_id": "...", "role": "PM", "chunk": "得不行。"}

data: {"session_id": "...", "role": "PM", "status": "idle"}
```
*(前端拿到 chunk 字段即可渲染对应的打字机效果)*

---

## 3. 获取所有会话列表 (Get History)

获取当前用户发起的全部 "影子董事会" 历史会话。

*   **路径**: `/history`
*   **方法**: `GET`
*   **入参**: 无
*   **出参 (JSON Response)**:

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "sessions": [
      {
        "id": "123e4567-...",
        "topic": "转盘功能讨论",
        "status": "done",
        "current_turn": 3,
        "max_turns": 10,
        "roles_config": ["PM", "Architect", "Designer", "QA"],
        "created_at": "2024-05-15T12:00:00Z"
      }
    ]
  }
}
```

---

## 4. 获取会话消息详情 (Get Messages)

下拉加载整个会话中的全量发言历史记录（包括用户自己和所有 AI 角色的确切留言）。

*   **路径**: `/messages`
*   **方法**: `GET`
*   **入参 (Query Params)**:
    *   `session_id`: string (UUID) [必填]
*   **出参 (JSON Response)**:

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "messages": [
      {
        "id": "1111-...",
        "session_id": "123e4567-...",
        "role": "CEO",
        "content": "我们需要在产品里加个转盘抽奖功能",
        "is_finalized": true,
        "created_at": "2024-05-15T12:00:00Z",
        "meta_data": {}
      },
      {
        "id": "2222-...",
        "session_id": "123e4567-...",
        "role": "PM",
        "content": "我觉得不行。盲目加抽奖会打乱原有的业务闭环。",
        "is_finalized": true,
        "created_at": "2024-05-15T12:00:05Z",
        "meta_data": {
          "turn": 0,
          "scores": {"PM": {"score": 90, "reason": "..."}}
        }
      }
    ]
  }
}
```

---

## 5. 获取当前会话状态 (Get Status)

当用户刷新页面或在流尚未接通前，主动拉取一次当前 Session 的最新内部状态。判断当前大家是在休眠 `idle` 还是打分 `scoring` 或者谁在讲话 `speaking`。如果达到最大轮数限制，状态将显示为 `paused`。

*   **路径**: `/status`
*   **方法**: `GET`
*   **入参 (Query Params)**:
    *   `session_id`: string (UUID) [必填]
*   **出参 (JSON Response)**:

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": "123e4567-...",
    "topic": "转盘功能讨论",
    "status": "speaking",
    "current_turn": 3,
    "max_turns": 10,
    "roles_config": ["PM", "Architect", "Designer", "QA"],
    "created_at": "2024-05-15T12:00:00Z"
  }
}
```
