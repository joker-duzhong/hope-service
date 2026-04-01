# 🌌 后端宇宙：模块化单体架构开发规范 (Backend Architecture Rules)

## 0. 文档目的 (Objective)

本文档是当前后端项目的**最高级开发约束**。无论是人类开发者还是 AI 编程助手，在进行业务需求开发、生成代码、重构逻辑时，**必须绝对遵守**本文档定义的架构原则和编码规范。
项目的核心思想是：**“一个共享底座 (Core) + N个完全隔离的业务模块 (Apps) + 独立的后台任务 (Worker)”**。

## 1. 技术栈基准 (Technology Stack)

开发与生成代码时，严格限制在以下技术栈范围内（严禁引入未授权的重量级框架）：

- **Web 框架**: `FastAPI` (严格使用异步 `async def`)
- **ORM 框架**: `SQLAlchemy 2.0+` (使用 AsyncSession)
- **数据验证与序列化**: `Pydantic V2`
- **后台任务与定时任务**: `Celery` + `Redis` (或 `Taskiq`)
- **核心数据库**: `PostgreSQL`
- **环境变量管理**: `pydantic-settings`

## 2. 目录结构规范 (Directory Structure)

所有代码必须严格按照以下拓扑结构放置，严禁跨界乱放文件：

```text
backend_universe/
├── core/                   # 🔴 【核心底座】全局共享，所有 App 均可读取
│   ├── config.py           # 环境变量与全局配置 (Pydantic Settings)
│   ├── database.py         # 数据库引擎与 Session 依赖 (Depends)
│   ├── security.py         # 密码哈希、JWT 生成与校验
│   ├── exceptions.py       # 全局自定义异常拦截
│   └── users/              # 统一用户中心 (注册、登录、权限判定)
│
├── apps/                   # 🔵 【业务模块】独立应用插槽
│   ├── trade_copilot/      # 示例应用 A
│   │   ├── models.py       # ORM 表结构定义
│   │   ├── schemas.py      # Pydantic 进出参模型
│   │   ├── services.py     # 核心业务逻辑 (不含 HTTP 请求处理)
│   │   ├── router.py       # FastAPI 路由定义 (仅解析请求，调用 service)
│   │   └── tasks.py        # 属于该应用的 Celery 异步/定时任务
│   │
│   └── project_b/          # 示例应用 B (结构同上)
│
├── worker/                 # 🟡 【任务引擎】负责统筹后台进程
│   ├── celery_app.py       # Celery 实例初始化与配置
│   └── scheduler.py        # 定时任务 (Beat) 的时间表配置
│
└── main.py                 # 🟢 【唯一入口】FastAPI 实例化，路由挂载，中间件配置
```

## 3. 🛡️ 核心开发纪律 (绝对禁令 / The "NEVER" Rules)

为了防止代码耦合，必须严格遵守以下隔离原则：

1. **【严禁跨业务导入】**：`apps.trade_copilot` 里的任何文件，**绝对不允许** `import apps.project_b` 里的任何代码。业务之间必须完全物理隔离。
2. **【底层单向依赖】**：`apps` 下的业务代码可以 `import core` 下的代码（如验证用户、获取 DB 会话），但 `core` 下的代码**绝对不允许**反向依赖 `apps` 里的代码。
3. **【业务逻辑下沉】**：`router.py` (控制器) 中只允许包含请求接收、参数校验、调用 Service 和返回结果的代码。**严禁在 Router 中写复杂的 IF-ELSE 和数据库增删改查逻辑**，必须全部封装在 `services.py` 中。
4. **【阻塞代码隔离】**：FastAPI 是异步框架。如果业务中包含耗时 > 1秒的操作（如：请求三方 API、大量计算、生成报表），**严禁直接在接口中 `await` 阻塞**，必须将其放入 `tasks.py` 并作为后台任务 (Celery) 异步执行。

## 4. 统一数据库与 ORM 规范

虽然所有项目共享同一个物理数据库，但必须做到逻辑隔离：

1. **表名前缀原则**：所有的 ORM 模型必须显式声明 `__tablename__`，且必须带有所属模块的前缀。
   - 核心系统表：`core_users`, `core_roles`
   - Trade 应用表：`trade_positions`, `trade_logs`
   - 某记账应用表：`billing_records`
2. **必留审计字段**：所有业务表必须继承基础 BaseModel，包含 `created_at` (创建时间) 和 `updated_at` (更新时间)，并在每次更新时自动刷新。
3. **禁止物理删除**：对于核心业务数据，严禁使用 `DELETE` 语句，必须使用 `is_deleted = True` 的软删除设计。
4. **统一用户绑定**：任何需要区分用户的业务表，必须包含 `user_id` 字段，并通过 FastAPI 的 `Depends(get_current_user)` 获取操作人，严禁前端伪造 user_id。

## 5. API 路由与接口规范

1. **统一路由挂载**：所有应用的路由必须在 `main.py` 中统一挂载，并带有版本和应用前缀：
   ```python
   # main.py
   app.include_router(users.router, prefix="/api/v1/auth", tags=["用户授权"])
   app.include_router(trade_router, prefix="/api/v1/trade", tags=["交易助手"])
   ```
2. **标准响应格式**：无论成功或失败，HTTP 接口必须返回统一的 JSON 结构。严禁直接裸返回字符串或列表：
   ```json
   {
       "code": 200,                // 业务状态码 (非 HTTP 状态码)
       "message": "success",       // 提示信息
       "data": { ... }             // 实际载荷，列表或对象
   }
   ```
3. **安全与跨域**：必须在 `main.py` 中配置 `CORSMiddleware`。API 严禁在未经 JWT Token 验证的情况下暴露敏感业务。

## 6. 后台与定时任务规范 (Worker Rules)

1. **任务注册**：每个 App 可以在自己的 `tasks.py` 中定义业务任务（如拉取股票数据）。
2. **调度统一管理**：定时任务的触发频率（Cron 表达式）严禁分散在各个应用中，必须在全局统一的 `worker/scheduler.py` 中集中配置，方便维护和一键暂停。
3. **独立进程运行**：在部署时，Worker（后台任务）与 Beat（定时触发器）必须独立于 API 服务运行，保证爬虫/发消息等重型任务不拖垮前端的接口响应。

## 7. 给 AI 助手的特别指令 (System Prompts for AI)

当 AI 助手阅读到本段时，代表你已被注入上述架构灵魂。在接下来为用户生成代码时：

1. 请先思考**要修改的是 Core 还是具体的 App**？
2. 如果用户要求写一个 API 接口，你需要**按顺序**生成或修改：`schemas.py` (定义入参出参) -> `services.py` (写 DB 操作和核心逻辑) -> `router.py` (写 API 暴露端点)。
3. 使用 `SQLAlchemy 2.0` 的现代语法（`select()`, `execute()`, 等），避免使用旧版的 `.query()`。
4. 在需要调用外部网络（如 requests, httpx, akshare）的地方，如果是同步库，请务必使用 `run_in_threadpool` 或将其放入后台 Celery 任务，严禁阻塞 FastAPI 的主事件循环。

---

### 💡 如何使用这份文档？

1. **存下来**：在你的项目根目录下新建一个名为 `README_ARCHITECTURE.md` 或者 `.cursorrules`（如果你用 Cursor 编辑器）的文件，把上面这段话全贴进去。
2. **喂给 AI**：以后你想加新项目了，比如：“我要开发一个日记本模块，需要暴露增加和查询接口，帮我写后端代码。” **在你发这段话之前，先把这份文档发给 AI 或者指定为上下文（Context）**。
3. **见证奇迹**：AI 会非常乖巧地在 `apps/diary/` 目录下帮你分别建好 `models.py`、`schemas.py`、`router.py`，并且自动给你加上 `diary_` 的表前缀，还会自动用 `get_current_user` 帮你做用户隔离。

拥有这份规范，你的后端就不会随着项目增多而变成“意大利面条”，而是像积木一样，干干净净，随时插拔！可以开始搭建第一行框架代码了。
