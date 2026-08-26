# Teacher Logbook 后端 API 设计

## 1. 设计目标

当前前端的 HTTP 适配器使用 `GET /logbook`、`PUT /logbook`、`DELETE /logbook` 读写整份台账。真实后端不建议继续将它作为日常数据接口，原因如下：

- 任意一条记录或一个座位变化都会上传整份数据。
- 多端同时编辑时，后保存的一端可能覆盖其他端的修改。
- 学生、谈话记录、家校沟通等数据增长后，全量请求会持续变大。
- 无法针对不同资源设置权限、审计、索引、分页和数据保留规则。

建议将学生、座位、待办、风险预警和首页统计拆为独立接口；其他低频台账使用独立资源 URL，但共享统一 CRUD、分页和错误规范。整份台账接口仅用于备份恢复。

## 2. 通用约定

### 2.1 基础信息

| 项目 | 约定 |
| --- | --- |
| Base URL | `/api/v1` |
| 班级作用域 | `/classes/{classId}` |
| 数据格式 | `application/json; charset=utf-8` |
| 时间格式 | ISO 8601，例如 `2026-08-26T10:30:00+08:00` |
| 日期格式 | `YYYY-MM-DD` |
| ID | 服务端生成 UUID，不复用姓名等业务字段 |
| 登录 | 沿用现有登录态，本规范不定义登录接口 |

`classId` 必须来自当前用户有权访问的班级，后端不能只信任前端传入值。学生关联统一使用 `studentId`，不能使用姓名作为外键。

### 2.2 成功响应

单条数据：

```json
{
  "data": {
    "id": "record-uuid"
  },
  "requestId": "request-uuid"
}
```

列表数据：

```json
{
  "data": [],
  "meta": {
    "page": 1,
    "pageSize": 20,
    "total": 0,
    "totalPages": 0
  },
  "requestId": "request-uuid"
}
```

创建成功返回 `201 Created`，普通查询和修改返回 `200 OK`，成功删除返回 `204 No Content`。

### 2.3 错误响应

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法",
    "fields": {
      "date": "日期格式必须为 YYYY-MM-DD"
    }
  },
  "requestId": "request-uuid"
}
```

| HTTP 状态码 | 错误码示例 | 说明 |
| --- | --- | --- |
| `400` | `VALIDATION_ERROR` | 字段、筛选条件或文件格式错误 |
| `401` | `UNAUTHORIZED` | 未登录或登录态失效 |
| `403` | `FORBIDDEN` | 无班级或资源访问权限 |
| `404` | `NOT_FOUND` | 班级、学生或记录不存在 |
| `409` | `CONFLICT` | 重复数据或版本冲突 |
| `412` | `VERSION_MISMATCH` | `If-Match` 版本已过期 |
| `413` | `PAYLOAD_TOO_LARGE` | 导入文件或批量数据过大 |
| `422` | `BUSINESS_RULE_VIOLATION` | 参数合法但违反业务规则 |
| `429` | `RATE_LIMITED` | 请求过于频繁 |
| `500` | `INTERNAL_ERROR` | 未预期的服务端错误 |

### 2.4 分页、筛选与排序

所有可能增长的列表接口统一支持：

| Query 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `page` | integer | `1` | 页码，从 1 开始 |
| `pageSize` | integer | `20` | 每页数量，范围 1–100 |
| `sort` | string | `-createdAt` | `-` 表示降序，例如 `-date` |
| `keyword` | string | 空 | 按资源允许的文本字段搜索 |
| `dateFrom` | date | 空 | 起始日期，包含边界 |
| `dateTo` | date | 空 | 结束日期，包含边界 |
| `studentId` | UUID | 空 | 按学生筛选，适用于学生相关资源 |

不适合分页的配置型资源，如座位布局、班委职位和课程表，可以一次返回完整集合。

### 2.5 通用记录字段

所有资源响应都应包含：

```json
{
  "id": "record-uuid",
  "classId": "class-uuid",
  "createdAt": "2026-08-26T10:30:00+08:00",
  "updatedAt": "2026-08-26T10:30:00+08:00"
}
```

学生相关响应可以附带只读字段 `studentName`，方便列表直接展示；写入时仍只接收 `studentId`。

### 2.6 通用 CRUD 规则

本文后续标记为“标准 CRUD”的资源都提供以下接口：

| Method | URL | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/classes/{classId}/{resource}` | 分页查询，可使用该资源支持的筛选参数 |
| `POST` | `/api/v1/classes/{classId}/{resource}` | 新增一条记录 |
| `GET` | `/api/v1/classes/{classId}/{resource}/{id}` | 查询单条记录 |
| `PATCH` | `/api/v1/classes/{classId}/{resource}/{id}` | 部分更新，只传需要修改的字段 |
| `DELETE` | `/api/v1/classes/{classId}/{resource}/{id}` | 删除一条记录 |

创建请求不传 `id`、`classId`、`createdAt`、`updatedAt`。`PATCH` 不允许把资源移动到其他班级。

## 3. 首页统计

### 3.1 获取班级首页统计

`GET /api/v1/classes/{classId}/dashboard`

Query：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `date` | date | 否 | 统计基准日期，默认服务端当天 |
| `timezone` | string | 否 | 默认班级时区，例如 `Asia/Shanghai` |

响应：

```json
{
  "data": {
    "studentSummary": { "total": 36, "male": 18, "female": 18 },
    "leaveToday": 2,
    "unsubmittedHomework": 6,
    "violationCount": 4,
    "workRecordsThisMonth": 8,
    "pendingTodoCount": 3,
    "alertSummary": {
      "emotion": 1,
      "specialHealth": 2,
      "dropoutRisk": 0,
      "notReturned": 0,
      "pending": 3
    },
    "highRiskStudents": [],
    "upcomingTodos": [],
    "latestExam": null,
    "recentWorkRecords": []
  },
  "requestId": "request-uuid"
}
```

备注：这是聚合读取接口，不接受写入。可缓存 15–60 秒；任一相关资源更新后可主动失效缓存。首页不应为了统计分别请求十几个列表接口。

## 4. 学生管理

学生是其他业务数据的核心关联资源，必须独立接口和独立数据表。

### 4.1 查询学生列表

`GET /api/v1/classes/{classId}/students`

额外 Query：`gender`、`keyword`。学生数量通常不大，前端排座位时可使用 `pageSize=100` 获取完整花名册。

### 4.2 新增学生

`POST /api/v1/classes/{classId}/students`

请求：

```json
{
  "name": "张伟",
  "gender": "男",
  "contact": "13800000000"
}
```

响应 `201`：返回完整学生对象。姓名不要求唯一，同名学生由 `id` 区分。

### 4.3 查询、修改、删除学生

| Method | URL | 入参 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/classes/{classId}/students/{studentId}` | Path | 查询学生详情 |
| `PATCH` | `/api/v1/classes/{classId}/students/{studentId}` | `name?`, `gender?`, `contact?` | 修改学生 |
| `DELETE` | `/api/v1/classes/{classId}/students/{studentId}` | Path | 删除学生 |

删除学生前必须处理关联数据。建议默认返回 `409 STUDENT_HAS_REFERENCES` 并给出关联数量；如产品确认需要级联删除，应增加显式参数 `cascade=true` 并记录审计日志。座位分配可以随学生删除自动清理。

### 4.4 CSV 导入学生

`POST /api/v1/classes/{classId}/students/import`

Content-Type：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | CSV file | 是 | UTF-8，最大建议 2 MB |
| `dryRun` | boolean | 否 | `true` 时只校验，不写入 |
| `duplicateStrategy` | enum | 否 | `skip` 或 `create`，默认 `skip` |

响应：

```json
{
  "data": {
    "totalRows": 40,
    "created": 36,
    "skipped": 3,
    "failed": 1,
    "errors": [{ "row": 12, "field": "name", "message": "姓名不能为空" }]
  },
  "requestId": "request-uuid"
}
```

去重建议使用“姓名 + 性别 + 联系方式”的组合，仅作为导入判断，不作为数据库唯一键。

### 4.5 导出学生

`GET /api/v1/classes/{classId}/students/export?format=csv`

响应为 `text/csv; charset=utf-8`，文件名通过 `Content-Disposition` 返回。

## 5. 座位板

座位板属于高频、局部更新资源，应独立于学生和其他台账。布局调整低频，学生移动高频，两者应分开。

### 5.1 获取座位板

`GET /api/v1/classes/{classId}/seat-board`

响应：

```json
{
  "data": {
    "layout": {
      "rows": 12,
      "columnGroups": [2, 3, 3, 2],
      "columns": 10
    },
    "assignments": [
      { "studentId": "student-uuid", "studentName": "张伟", "row": 1, "column": 1 }
    ],
    "version": 7,
    "updatedAt": "2026-08-26T10:30:00+08:00"
  },
  "requestId": "request-uuid"
}
```

响应头返回 `ETag: "seat-board-7"`。`columns` 是 `columnGroups` 的派生值，不允许客户端独立设置。

### 5.2 保存座位布局

`PUT /api/v1/classes/{classId}/seat-board/layout`

Headers：`If-Match: "seat-board-7"`

请求：

```json
{
  "rows": 12,
  "columnGroups": [2, 3, 3, 2]
}
```

响应返回更新后的 `layout` 和新 `version`。

规则：行数 1–30；每组 1–10 列；至少一组；总列数不超过 30。缩小布局导致座位越界时，响应应返回被移回未放置列表的学生：

```json
{
  "data": {
    "layout": { "rows": 10, "columnGroups": [2, 4, 2], "columns": 8 },
    "removedStudentIds": ["student-uuid"],
    "version": 8
  }
}
```

### 5.3 放置或移动单个学生

`PUT /api/v1/classes/{classId}/seat-board/assignments/{studentId}`

Headers：`If-Match: "seat-board-8"`

请求：

```json
{
  "row": 3,
  "column": 5,
  "swap": true
}
```

响应返回发生变化的座位和新 `version`。`swap=true` 表示目标座位已有学生时交换位置；来源为未放置列表时，目标学生回到未放置列表。

备注：这是拖放和 H5 点选的主要高频写接口，不应每次上传完整座位板。

### 5.4 移除单个学生座位

`DELETE /api/v1/classes/{classId}/seat-board/assignments/{studentId}`

Headers：`If-Match`。响应 `200` 并返回新 `version`，便于继续编辑。

### 5.5 批量保存座位

`PUT /api/v1/classes/{classId}/seat-board/assignments`

适用于首次排座、导入或离线操作后一次同步。

```json
{
  "mode": "replace",
  "assignments": [
    { "studentId": "student-a", "row": 1, "column": 1 },
    { "studentId": "student-b", "row": 1, "column": 2 }
  ]
}
```

`mode` 可为 `replace` 或 `merge`。同一学生只能出现一次，同一坐标只能有一个学生。

### 5.6 全部清空座位

`DELETE /api/v1/classes/{classId}/seat-board/assignments`

Headers：`If-Match`。只清空学生座位，不删除布局、学生或其他台账。响应返回新 `version`。

### 5.7 并发处理

座位修改必须使用 `ETag` 和 `If-Match`。版本过期返回 `412 VERSION_MISMATCH`，并在响应中附带当前版本；前端重新获取座位板后提示用户合并或重试，不能静默覆盖。

## 6. 请假管理

资源名：`leave-requests`，使用标准 CRUD。

记录字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `studentId` | UUID | 是 | 学生 ID |
| `reason` | enum | 是 | `病假`、`事假`、`其他` |
| `date` | date | 是 | 请假日期 |

查询支持 `studentId`、`reason`、`dateFrom`、`dateTo`。

## 7. 作业管理

资源名：`homework-records`，使用标准 CRUD。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `subject` | string | 是 | 学科，最长建议 50 |
| `title` | string | 是 | 作业内容，最长建议 200 |
| `unsubmitted` | integer | 是 | 未交人数，最小 0 |
| `date` | date | 是 | 布置日期 |

查询支持 `subject`、`dateFrom`、`dateTo`。如果未来需要记录具体未交学生，应拆出 `homework-submissions`，不要继续只保存人数。

## 8. 违纪记录

资源名：`violations`，使用标准 CRUD。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `studentId` | UUID | 是 | 学生 ID |
| `type` | string | 是 | 违纪类型，最长建议 50 |
| `date` | date | 是 | 发生日期 |
| `note` | string | 否 | 处理说明，最长建议 2000 |

查询支持 `studentId`、`type`、`dateFrom`、`dateTo`。

## 9. 风险预警

风险预警会频繁出现在首页并按状态统计，建议独立资源和索引。

资源名：`alerts`，使用标准 CRUD。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `studentId` | UUID | 是 | 学生 ID |
| `type` | enum | 是 | `情绪预警`、`特殊体质`、`辍学风险`、`未返校`、`其他` |
| `level` | enum | 是 | `高`、`中`、`低` |
| `status` | enum | 是 | `待处理`、`跟进中`、`已关闭` |
| `note` | string | 否 | 跟进情况，最长建议 5000 |

查询支持 `studentId`、`type`、`level`、`status`。建议为 `(classId, status, level)` 建联合索引。

### 9.1 单独更新处理状态

`PATCH /api/v1/classes/{classId}/alerts/{id}/status`

```json
{
  "status": "已关闭",
  "note": "已与家长确认并完成跟进"
}
```

备注：状态处理是常用动作，单独接口便于权限、审计和幂等控制。

## 10. 待办与备忘录

资源名：`todos`，使用标准 CRUD。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `title` | string | 是 | 事项，最长建议 200 |
| `due` | date | 否 | 截止日期 |
| `status` | enum | 是 | `待完成`、`已完成` |

查询支持 `status`、`dateFrom`、`dateTo`。

### 10.1 快速完成或恢复待办

`PATCH /api/v1/classes/{classId}/todos/{id}/status`

```json
{
  "status": "已完成"
}
```

这是首页“完成”按钮的高频接口，不需要提交整条待办或整份台账。

## 11. 工作简报

资源名：`work-records`，使用标准 CRUD。

字段：`title` string 必填、`date` date 必填、`note` string 可选。查询支持日期范围和关键词。

## 12. 成绩分析

资源名：`exams`，使用标准 CRUD。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `subject` | string | 是 | 学科 |
| `name` | string | 是 | 考试名称 |
| `average` | number | 是 | 班级平均分 |
| `date` | date | 是 | 考试日期 |

查询支持 `subject` 和日期范围。若未来录入每名学生成绩，应新增 `exam-scores` 子资源，不要将大量明细塞进 `exams` 单条 JSON。

## 13. 班委管理

### 13.1 班委职位

资源名：`committee-roles`，使用标准 CRUD，不分页也可以。

字段：`role` string 必填且班级内唯一、`duty` string 必填。

### 13.2 班委成员

资源名：`committee-members`，使用标准 CRUD。

字段：`studentId` UUID 必填、`roleId` UUID 必填。同一职位是否允许多人由后端配置决定。响应可以展开只读字段 `studentName`、`role`、`duty`。

备注：不要在成员记录中复制职责正文；职责来自职位资源，避免修改职位后出现历史不一致。

## 14. 卫生安排

资源名：`hygiene-assignments`，使用标准 CRUD。

字段：`studentId` UUID 必填、`area` string 必填、`day` string 必填。查询支持 `studentId` 和 `day`。

## 15. 班级活动

资源名：`activities`，使用标准 CRUD。

字段：`title` string 必填、`date` date 必填、`note` string 可选。查询支持日期范围和关键词。

## 16. 班级收支

资源名：`finance-records`，使用标准 CRUD。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | enum | 是 | `收入`、`支出` |
| `amount` | decimal string | 是 | 金额，例如 `"125.50"`，禁止浮点直接存储 |
| `note` | string | 是 | 说明 |
| `date` | date | 是 | 日期 |

查询支持 `type` 和日期范围。

### 16.1 获取收支汇总

`GET /api/v1/classes/{classId}/finance-summary?dateFrom=2026-08-01&dateTo=2026-08-31`

响应：

```json
{
  "data": {
    "income": "1000.00",
    "expense": "325.50",
    "balance": "674.50"
  },
  "requestId": "request-uuid"
}
```

## 17. 个人奖惩

资源名：`awards`，使用标准 CRUD。

字段：`studentId` UUID 必填、`type` enum（`表扬`、`奖励`、`批评`、`处分`）、`note` string 必填、`date` date 必填。

## 18. 课程表

资源名：`courses`，使用标准 CRUD。课程数量有限，可不分页。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `course` | string | 是 | 课程名称 |
| `teacher` | string | 是 | 任课教师 |
| `day` | enum | 是 | `周一` 至 `周日` |
| `startTime` | time | 是 | `HH:mm` |
| `endTime` | time | 是 | `HH:mm`，必须晚于开始时间 |

响应可附带兼容字段 `time: "08:00-08:45"`，但后端建议拆分存储开始和结束时间。

## 19. 谈话记录

资源名：`talks`，使用标准 CRUD。

字段：`studentId` UUID 必填、`date` date 必填、`note` string 必填。查询支持学生和日期范围。

备注：谈话内容可能涉及敏感个人信息，应单独配置查看权限、操作审计和数据导出权限。

## 20. 家校沟通

资源名：`contacts`，使用标准 CRUD。

字段：`studentId` UUID 必填、`method` enum（`电话`、`微信`、`面谈`、`家访`）、`date` date 必填、`note` string 必填。查询支持学生、方式和日期范围。

## 21. 班主任培训

资源名：`training-records`，使用标准 CRUD。

字段：`category` enum（`培训`、`讲座`、`活动`）、`title` string 必填、`hours` decimal string 必填、`date` date 必填。

### 21.1 获取学时统计

`GET /api/v1/classes/{classId}/training-summary?dateFrom=2026-01-01&dateTo=2026-12-31`

响应包含总学时和各类别数量、学时。

## 22. 常用网址

资源名：`links`，使用标准 CRUD，可不分页。

字段：`title` string 必填、`url` string 必填。仅允许 `http` 和 `https`，后端需要规范化并防止危险协议。

## 23. UI 偏好

UI 皮肤属于用户偏好，不应放在班级数据内。

### 23.1 获取 UI 偏好

`GET /api/v1/users/me/preferences/ui`

响应：

```json
{
  "data": {
    "skin": "mr"
  },
  "requestId": "request-uuid"
}
```

### 23.2 修改 UI 偏好

`PATCH /api/v1/users/me/preferences/ui`

```json
{
  "skin": "apple"
}
```

自定义皮肤名必须进行白名单或格式校验。若不要求跨设备同步，UI 偏好也可以继续保存在浏览器本地，不接后端。

## 24. 数据备份与恢复

这些接口用于数据管理，不参与日常 CRUD。

### 24.1 导出完整备份

`GET /api/v1/classes/{classId}/backup`

Query：`format=json`，响应为下载文件。备份内容包含 `schemaVersion`、`exportedAt`、班级数据和资源集合。

### 24.2 校验备份

`POST /api/v1/classes/{classId}/backup/validate`

Content-Type：`multipart/form-data`，字段 `file`。只校验版本、结构、引用关系和预计影响，不写入数据。

### 24.3 恢复备份

`POST /api/v1/classes/{classId}/backup/restore`

Content-Type：`multipart/form-data`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | JSON file | 是 | 由系统导出的备份 |
| `mode` | enum | 是 | `replace` 或 `merge` |
| `confirmation` | string | 是 | 固定值 `RESTORE_CLASS_DATA` |

恢复应作为异步事务执行，大数据量时返回 `202 Accepted` 和 `jobId`。任一资源失败时整体回滚。

### 24.4 查询恢复任务

`GET /api/v1/classes/{classId}/backup/jobs/{jobId}`

返回 `pending`、`running`、`succeeded`、`failed`、进度和错误摘要。

### 24.5 清空班级数据

`POST /api/v1/classes/{classId}/data/clear`

```json
{
  "confirmation": "CLEAR_CLASS_DATA"
}
```

建议保留班级实体和用户权限，只清除业务记录。此操作必须记录操作者、时间、IP、清除范围，并建议提供短期恢复能力。

## 25. 是否需要拆分接口的结论

### 25.1 必须独立

| 资源 | 原因 |
| --- | --- |
| 学生 | 其他业务的主外键，包含隐私数据，支持导入导出 |
| 座位板 | 拖放产生高频局部更新，需要版本控制 |
| 首页统计 | 多资源聚合，避免首页发起大量列表请求 |
| 待办状态 | 首页高频操作，只需修改一个字段 |
| 风险预警状态 | 需要审计、权限和快速统计 |
| 收支汇总 | 金额计算必须由服务端完成 |
| 备份恢复 | 大请求、低频、事务性强，不能混入日常保存 |

### 25.2 可共享实现但保留独立 URL

请假、作业、违纪、工作简报、考试、卫生、活动、奖惩、课程、谈话、家校沟通、培训和常用网址可以在后端复用一套 CRUD 基础设施，但对外仍应使用明确资源 URL。不要设计 `/records/{type}` 这类万能接口，否则字段校验、权限、索引和后续演进会重新耦合在一起。

### 25.3 不建议日常使用

`PUT /logbook` 不应继续承担日常保存。迁移期可以暂时保留为内部兼容接口，完成前端适配后下线；完整数据读写只保留在 `/backup` 系列接口中。

## 26. 后端实现备注

- 所有写操作校验当前用户对 `classId` 的权限。
- 学生姓名允许重复，业务关联只使用 `studentId`。
- 记录删除建议按敏感程度选择软删除，并保留审计信息。
- 谈话、家校沟通、风险预警、联系方式应加密存储或进行字段级访问控制。
- 批量导入、备份恢复和清空数据必须记录审计日志。
- 创建类接口可支持 `Idempotency-Key`，防止网络重试产生重复记录。
- 列表查询必须使用数据库分页，不能先读取全部数据再在应用层分页。
- 服务端负责金额、汇总和座位唯一性校验，不能只依赖前端。
- 建议为常用条件建立联合索引，例如学生与日期、预警状态与等级、待办状态与截止日期。
- 接口字段从姓名关联迁移到 ID 时，应提供一次性迁移脚本，不要在运行时靠姓名猜测同名学生。
