# Hope Service

模块化单体后端服务 —— 一个共享底座 (Core) + N 个完全隔离的业务模块 (Apps) + 独立的后台任务 (Worker)。

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI (async) |
| ORM | SQLAlchemy 2.0+ (AsyncSession) |
| 数据验证 | Pydantic V2 |
| 后台任务 | Celery + Redis |
| 数据库 | PostgreSQL |
| 配置管理 | pydantic-settings |

## 项目结构

```
hope-service/
├── core/                        # 🔴 核心底座（全局共享）
│   ├── config.py                # 环境变量与全局配置
│   ├── database.py              # 数据库引擎与 Session 依赖
│   ├── security.py              # 密码哈希、JWT 生成与校验
│   ├── exceptions.py            # 全局自定义异常拦截
│   ├── response.py              # 统一响应模型
│   └── users/                   # 统一用户中心
│       ├── models.py            # ORM 表 (core_users)
│       ├── schemas.py           # Pydantic 进出参
│       ├── services.py          # 业务逻辑
│       ├── router.py            # API 路由
│       └── dependencies.py      # get_current_user 等
├── apps/                        # 🔵 业务模块（独立插槽）
├── worker/                      # 🟡 任务引擎
│   ├── celery_app.py            # Celery 实例
│   └── scheduler.py             # Beat 定时时间表
├── main.py                      # 🟢 唯一入口
├── tests/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 快速开始

### 1. 环境准备

```bash
cp .env.example .env
# 编辑 .env，填入数据库密码、JWT 密钥、微信配置等
```

### 2. Docker 部署（推荐）

```bash
# 启动所有服务（PostgreSQL + Redis + FastAPI + Celery Worker + Celery Beat）
docker compose up -d

# 查看日志
docker compose logs -f app

# 停止
docker compose down

# 重新构建并启动
docker compose up -d --build
```

### 3. 本地开发

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt

uvicorn main:app --reload --port 8000
```

### 4. 访问服务

- Swagger 文档: http://localhost:8000/docs
- ReDoc 文档: http://localhost:8000/redoc
- 健康检查: http://localhost:8000/health

## API 接口

### 用户授权 `/api/v1/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/auth/register | 用户名密码注册 |
| POST | /api/v1/auth/login | 用户名密码登录 |
| GET  | /api/v1/auth/wechat/url | 获取微信授权 URL |
| POST | /api/v1/auth/wechat/login | 微信授权登录 |
| POST | /api/v1/auth/refresh | 刷新令牌 |
| GET  | /api/v1/auth/me | 获取当前用户信息 |
| PUT  | /api/v1/auth/me | 更新当前用户信息 |

所有接口统一返回格式：

```json
{
    "code": 200,
    "message": "success",
    "data": { ... }
}
```

## 配置说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| SECRET_KEY | JWT 密钥 | (必填) |
| POSTGRES_SERVER | 数据库地址 | localhost |
| POSTGRES_PASSWORD | 数据库密码 | postgres |
| REDIS_HOST | Redis 地址 | localhost |
| WECHAT_APPS | 微信公众号配置，格式: appid:secret:token:aeskey | (可选) |

## 新增业务模块

在 `apps/` 下创建新目录，包含以下文件即可：

```
apps/your_module/
├── models.py      # ORM 表 (表名带模块前缀，如 billing_records)
├── schemas.py     # Pydantic 模型
├── services.py    # 业务逻辑
├── router.py      # 路由 (在 main.py 中挂载)
└── tasks.py       # (可选) Celery 任务
```

然后在 `main.py` 中挂载路由、在 `worker/scheduler.py` 中配置定时任务。

**严禁跨模块导入**，`apps/module_a` 不得 `import apps/module_b`。
