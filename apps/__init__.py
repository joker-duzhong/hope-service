"""
业务模块插槽

在此目录下创建独立的应用，每个应用必须包含:
- models.py   ORM 表结构定义
- schemas.py  Pydantic 进出参模型
- services.py 核心业务逻辑
- router.py   FastAPI 路由定义
- tasks.py    (可选) Celery 异步/定时任务

严禁跨业务导入。
"""
