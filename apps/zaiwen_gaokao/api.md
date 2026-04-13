# 在问高考 (Zaiwen Gaokao) 接口文档

## 1. 公共返回体结构

### 1.1 标准响应 (ResponseModel)
```json
{
  "code": 200,      // 业务状态码 (200 为成功)
  "message": "...", // 提示信息
  "data": { ... }   // 具体业务数据 (可为 null)
}
```

### 1.2 分页数据结构 (PaginatedData)
当 `data` 为分页对象时，其内部结构如下：
```json
{
  "items": [],      // 数据列表
  "total": 100,     // 总记录数
  "page": 1,        // 当前页码
  "page_size": 20,  // 每页大小
  "total_pages": 5  // 总页数
}
```

---

## 2. 接口列表

### 2.0 社区个人中心 (Profile & Persona)

#### [GET] /profile/me - 个人面板数据
- **描述**: 获取当前用户的马甲信息及社区统计数据（收到的抱抱等）。
- **返回 data**:
  ```json
  {
    "persona": {
      "id": "uuid",
      "nickname": "焦虑的修狗",
      "avatar_url": "https://...",
      "status_emoji": "✍️",
      "ai_collection_enabled": true,
      "burn_after_reading_hours": 48
    },
    "received_hugs": 15,
    "sent_hugs": 0
  }
  ```

#### [POST] /profile/randomize - 随机马甲生成
- **描述**: 随机重置当前用户的昵称和头像。
- **返回 data**:
  *同 Persona 对象结构*

#### [PUT] /profile/settings - 隐私与信息更新
- **描述**: 更新用户的马甲信息、隐私设置或阅后即焚时间。
- **请求参数 (JSON)**:
  | 字段 | 类型 | 必填 | 描述 |
  | :--- | :--- | :--- | :--- |
  | nickname | string | 否 | 马甲昵称 (1-50字) |
  | avatar_url | string | 否 | 头像 URL |
  | status_emoji | string | 否 | 状态 Emoji |
  | ai_collection_enabled | bool | 否 | 是否允许 AI 收录 |
  | burn_after_reading_hours | int | 否 | 阅后即焚时间 (1-168 小时) |
- **返回 data**:
  *同 Persona 对象结构*

#### [DELETE] /profile/wipe - 抹除痕迹 (斩断前缘)
- **描述**: 
  1. 抹除用户在社区发布的所有痕迹（需结合后端实现逻辑）。
  2. 重置并分配一个新的随机马甲。
- **返回**: `{"code": 200, "message": "痕迹已抹除，已分配新身份", "data": null}`

#### [GET] /profile/my-treeholes - 获取我的树洞记录
- **描述**: 分页获取当前用户发布的树洞帖子。
- **查询参数 (Query)**:
  | 字段 | 类型 | 默认值 | 描述 |
  | :--- | :--- | :--- | :--- |
  | limit | int | 20 | 每页数量 (1-100) |
  | offset | int | 0 | 偏移量 |
- **返回 data**: `List[TreeholePostRead]`

#### [GET] /profile/my-audits - 获取我的质询（投票）记录
- **描述**: 分页获取当前用户在红黑榜的投票记录。
- **查询参数 (Query)**:
  | 字段 | 类型 | 默认值 | 描述 |
  | :--- | :--- | :--- | :--- |
  | limit | int | 20 | 每页数量 (1-100) |
  | offset | int | 0 | 偏移量 |
- **返回 data**: `List[BoardVoteRead]`

---

### 2.1 双面树洞 (Treehole)

#### [POST] /treehole/post - 树洞发帖
- **描述**: 用户在树洞发布情绪(emo)或求助(help)内容。
- **请求参数 (JSON)**:
  | 字段 | 类型 | 必填 | 描述 |
  | :--- | :--- | :--- | :--- |
  | content | string | 是 | 帖子内容 (1-1000字) |
  | type | string | 是 | 类型: "emo" 或 "help" |
- **返回 data**: `TreeholePostRead`

#### [DELETE] /treehole/post/{post_id} - 销毁单条树洞
- **描述**: 根据 ID 删除指定的树洞帖子。
- **路径参数**: `post_id` (UUID)
- **返回**: `{"code": 200, "message": "帖子已从宇宙中抹除", "data": null}`

#### [GET] /treehole/feed - 树洞信息流
- **描述**: 获取树洞帖子列表，包含作者马甲信息及 AI 回复，支持游标分页。
- **查询参数 (Query)**:
  | 字段 | 类型 | 必填 | 描述 |
  | :--- | :--- | :--- | :--- |
  | cursor | uuid | 否 | 上一页最后的 UUID，用于加载下一页 |
- **返回 data (Array)**:
  ```json
  [
    {
      "id": "uuid",
      "persona_id": "uuid",
      "content": "string",
      "type": "emo",
      "hug_count": 5,
      "has_ai_reply": true,
      "created_at": "...",
      "author": {
         "nickname": "...",
         "avatar_url": "...",
         "status_emoji": "..."
      },
      "ai_reply": { // 如果有 AI 回复则不为 null
         "id": "uuid",
         "content": "AI 回复的内容",
         "is_ai_reply": true,
         "persona_id": null,
         "created_at": "..."
      }
    }
  ]
  ```

#### [POST] /treehole/hug/{post_id} - 树洞抱抱
- **描述**: 给帖子点赞/抱抱。
- **路径参数**: `post_id` (UUID)
- **返回**: `{"code": 200, "message": "抱抱成功", "data": null}`

---

### 2.2 志愿红黑榜 (Board)

#### [GET] /board/feed - 红黑榜信息流/搜索
- **描述**: 获取红黑榜帖子列表，支持按学校搜索和排序。
- **查询参数 (Query)**:
  | 字段 | 类型 | 默认值 | 描述 |
  | :--- | :--- | :--- | :--- |
  | school_name | string | null | 搜索学校名称 |
  | sort_by | string | "new" | 排序: "new" (最新), "hot" (最热) |
  | limit | int | 20 | 每页数量 (1-100) |
  | offset | int | 0 | 偏移量 |
- **返回 data**: `List[BoardPostRead]` (包含 `author` 对象，若记录已脱敏软删(is_wiped)，则 author 显示为已抹除身份)

#### [GET] /board/{post_id} - 红黑榜详情页
- **描述**: 获取单条红黑榜帖子的详细信息及所有投票记录。
- **路径参数**: `post_id` (UUID)
- **返回 data**: `BoardDetailRead` (包含 `author` 对象与 `votes` 列表)

#### [POST] /board/post - 红黑榜发帖
- **描述**: 发布一条关于学校专业的红黑评价。
- **请求参数 (JSON)**:
  | 字段 | 类型 | 必填 | 描述 |
  | :--- | :--- | :--- | :--- |
  | school_name | string | 是 | 学校名称 (1-100字) |
  | major_name | string | 是 | 专业名称 (1-100字) |
  | content | string | 是 | 评价内容 (1-1000字) |
- **返回 data**: `BoardPostRead`

#### [POST] /board/vote - 红黑榜投票
- **描述**: 为红黑榜帖子投“红”或“绿”票。
- **请求参数 (JSON)**:
  | 字段 | 类型 | 必填 | 描述 |
  | :--- | :--- | :--- | :--- |
  | post_id | uuid | 是 | 帖子 ID |
  | option | string | 是 | "red" 或 "green" |
  | comment | string | 否 | 投票评论 (200字以内) |
- **返回**: `{"code": 200, "message": "投票成功", "data": null}`
