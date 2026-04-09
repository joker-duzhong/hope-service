Todo List V1

模块命名约定：本业务模块命名为 time_library, 所属目录为 apps/time_library/。所有数据库表前缀必须严格使用 time_library_（禁止简写）。

阶段一：领域模型与数据契约 (Models & Schema) —— 奠定数据基石
 任务 1.1：创建业务模块目录结构
  - 操作：在 apps/ 下新建 time_library 目录，创建 __init__.py, models.py, schemas.py, services.py, router.py, admin_router.py。
  - 验收标准 (AC)：目录符合规范第 2 条。C 端接口和 B 端（管理端）接口在路由文件上做到物理分离。

 任务 1.2：定义 SQLAlchemy ORM 模型 (models.py)
  - 操作：编写 Book, BookContent, AIPersona, ChatSession, ChatMessage 模型。
  - ChatSession 现在有预留这个功能点， 但是真正和llm对话的模块之后再开发，会在 core里面去做 ，你之后只需要接入即可， 不需要在这个应用中去做底层能力。
  - 验收标准 (AC)：
    1. 所有模型必须继承全局的 core.database.CoreModel。
    2. 表名严格遵守前缀规范，如 __tablename__ = "time_library_books"。
    4. 成功生成 Alembic 迁移脚本（alembic revision --autogenerate）并执行升级至数据库无报错。

 任务 1.3：定义 Pydantic 进出参模型 (schemas.py)
  - 操作：定义 API 的请求体（入参）和响应体（出参）模型。
  - 验收标准 (AC)：
    1. 必须使用 Pydantic V2 语法（如 model_config = ConfigDict(...)）。
    2. 出参 (Read)：包含 BookListResponseItem, BookDetailResponse 等。
    3. 入参 (Write)：包含 BookCreate, BookUpdate（所有字段设为 Optional，禁止全量覆盖更新）, BookContentCreate, AIPersonaCreate。
    4. 严禁在 Schema 中包含数据库操作，仅作数据校验（如：校验经度范围 -180~180）。

阶段二：核心业务逻辑 (Services) —— 隔离数据操作
 任务 2.1：编写 C 端只读业务逻辑 (services.py)
  - 操作：实现 get_books_by_year_range 和 get_book_detail 方法。
  - 验收标准 (AC)：
    1. 严格使用 SQLAlchemy 2.0 异步语法（select(...)）。
    2. 查询时必须带上 is_deleted == False 的过滤条件（遵守软删除规范）。

 任务 2.2：编写 B 端管理 CRUD 业务逻辑 (services.py)
  - 操作：为书籍、内容、AI人设补充 create_book, update_book, soft_delete_book 以及相关联表的写入方法。
  - 验收标准 (AC)：
    1. 绝对遵守软删除纪律：删除方法内部严禁调用 session.delete()，必须使用 update().where(...).values(is_deleted=True)。
    2. 写入操作必须显式捕获可能的异常（如唯一索引冲突 IntegrityError），并向上抛出业务友好的异常信息。

阶段三：C 端接口暴露 (Router) —— 为 3D 地图提供弹药
 任务 3.1：编写普通用户只读接口 (router.py)
  - 操作：实现 GET /books (按年份区间取列表) 和 GET /books/{book_id} (书籍详情与 AI 人设)。
  - 验收标准 (AC)：
    1. 参数校验由 Pydantic 完成，路由函数内部只调用 services.py。
    2. 返回值必须使用全局统一的 ResponseModel 包装（即 {"code": 200, "message": "success", "data": [...]}）。
阶段四：B 端权限与管理接口 (Admin API) —— 打造数据录入管道
 任务 4.1：配置专属业务角色 (RBAC/权限系统)
  - 操作：主要是基于现有系统的权限规则，定义该模块的管理角色（例如 role_time_library_admin）。
  - 验收标准 (AC)：准备好供路由层使用的依赖注入（Dependencies），例如能够基于 Token 验证当前用户是否拥有 role_time_library_admin 权限。

 任务 4.2：开发管理端独立路由 (admin_router.py)
  - 操作：编写对书籍、章节内容、AI 人设的增删改查 RESTful API。
  - 验收标准 (AC)：
    1. 提供 POST /books, PUT /books/{id}, DELETE /books/{id}。
    2. 提供 POST /books/{id}/contents (追加章节), POST /books/{id}/persona (设置/修改 AI 人设)。
    3. 核心安全约束：以上所有接口必须注入高权限依赖（如 Depends(require_role('role_time_library_admin'))），严禁普通 Token 访问。

 任务 4.3：主应用挂载路由 (main.py)
  - 操作：在根目录的 main.py 中挂载 C 端与 B 端路由。
  - 验收标准 (AC)：

```python
# prefix="/api/v1/time_library/admin"
app.include_router(router, prefix="/api/v1/time_library", tags=["时空图书馆"])
```

阶段五：工程验证与数据灌入 (Testing & Seeding)
 任务 5.1：使用管理端 API 灌入初始化数据
  - 操作：使用 Postman 或写一个临时的 HTTP 请求脚本，携带管理员 Token，调用 POST /api/v1/time_library/admin/books 等接口，录入《奥德赛》与《论语》及其测试数据。
  - 验收标准 (AC)：
    1. 不通过直接写 SQL 的方式，而是完全通过 API 成功存入数据。
    2. 检查数据库中关联表的 is_deleted、user_id、created_at 等底层自动维护的字段是否正确生成。