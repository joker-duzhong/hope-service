# Hope Service

模块化单体后端服务 —— 一个共享底座 (Core) + N 个完全隔离的业务模块 (Apps) + 独立的后台任务 (Worker)。

## 技术栈

| 层级     | 技术                           |
| -------- | ------------------------------ |
| Web 框架 | FastAPI (async)                |
| ORM      | SQLAlchemy 2.0+ (AsyncSession) |
| 数据验证 | Pydantic V2                    |
| 后台任务 | Celery + Redis                 |
| 数据库   | PostgreSQL                     |
| 配置管理 | pydantic-settings              |

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
# docker logs hope-app --tail 50
# docker logs -f hope-celery-worker --tail 50
# docker logs -f hope-celery-beat --tail 50

# 停止
docker compose down

# 重新构建并启动
docker compose up -d --build

# alembic 迁移命令
# 本地
$env:PYTHONPATH="$PWD"; alembic upgrade head
# 线上：
docker compose exec app python -m alembic upgrade head
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

#### 一键启动所有服务

**Windows (PowerShell)：**

```powershell
$env:PYTHONUTF8=1
wt -p "Windows PowerShell" -d . uvicorn main:app --reload --port 8000 `; split-pane -p "Windows PowerShell" -d . celery -A worker.celery_app worker --loglevel=info `; split-pane -p "Windows PowerShell" -d . celery -A worker.celery_app beat --loglevel=info
```

这会在一个终端中启动所有服务：

- **FastAPI** - uvicorn 应用，监听 http://localhost:8000
- **Celery Worker** - 后台任务处理（AI 回复、数据生成等）
- **Celery Beat** - 定时任务调度

### 4. 访问服务

- Swagger 文档: http://localhost:8000/docs
- ReDoc 文档: http://localhost:8000/redoc
- 健康检查: http://localhost:8000/health

## API 接口

### 用户授权 `/api/v1/auth`

| 方法 | 路径                      | 说明             |
| ---- | ------------------------- | ---------------- |
| POST | /api/v1/auth/register     | 用户名密码注册   |
| POST | /api/v1/auth/login        | 用户名密码登录   |
| GET  | /api/v1/auth/wechat/url   | 获取微信授权 URL |
| POST | /api/v1/auth/wechat/login | 微信授权登录     |
| POST | /api/v1/auth/refresh      | 刷新令牌         |
| GET  | /api/v1/auth/me           | 获取当前用户信息 |
| PUT  | /api/v1/auth/me           | 更新当前用户信息 |

所有接口统一返回格式：

```json
{
    "code": 200,
    "message": "success",
    "data": { ... }
}
```

## 配置说明

| 变量              | 说明                                            | 默认值    |
| ----------------- | ----------------------------------------------- | --------- |
| SECRET_KEY        | JWT 密钥                                        | (必填)    |
| POSTGRES_SERVER   | 数据库地址                                      | localhost |
| POSTGRES_PASSWORD | 数据库密码                                      | postgres  |
| REDIS_HOST        | Redis 地址                                      | localhost |
| WECHAT_APPS       | 微信公众号配置，格式: appid:secret:token:aeskey | (可选)    |
| ALIYUN_SMS_SIGN_NAME | 号码认证服务可用的短信签名 | (短信必填) |
| ALIYUN_SMS_TEMPLATE_CODE | 阿里云号码认证服务短信模板 Code | (必填) |

### 号码认证短信

短信使用阿里云 Dypnsapi，不再使用腾讯云或普通 Dysmsapi。短信模块只提供 send_sms_code(phone) 和 verify_sms_code(phone, code) 两个能力；发送四位数字验证码，有效期 300 秒，验证码仅保存在 Redis 会话中并在成功校验后一次性消费，不携带业务 purpose 参数。

- 配置账户可用的签名和模板；示例模板 100001 需在实际账户中确认可用。
- 凭据使用项目环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID 和 ALIBABA_CLOUD_ACCESS_KEY_SECRET，不要把真实值提交到仓库；生产环境建议使用受限 RAM 用户的 AccessKey，并通过部署平台的 Secret/环境变量注入。
- 每个手机号间隔 60 秒，滚动 24 小时最多 5 次发送尝试；每个会话最多 5 次校验尝试。拒绝或超时也保留冷却和已用配额，SDK 不自动重试发送。
- Redis 不可用时拒绝发送或核验，不绕过限流。核验成功后原子消费本地会话；短信发送失败或过期后须重新申请，不保留旧短信服务的验证码兼容路径。
- 升级前从部署环境中移除废弃的腾讯云短信变量，安装 requirements.txt 中的新依赖，再重启 API、Worker 和 Beat；只修改示例文件不会更新实际部署配置。

### 资源删除与刷新令牌

公共资源删除只把数据库记录的 is_deleted 设为 true，原图、缩略图和数据库记录都保留；软删除不等于撤销已经公开的 CDN 链接。旧删除任务在新 Worker 中为空操作，上线时必须先停止旧 Worker，避免旧进程继续执行物理删除。保留的文件仍占用存储空间。

刷新接口先校验账户状态与 token_version，再通过 Redis Lua 原子写入新会话并消耗旧会话。旧 refresh token 严格一次性使用，不设 30 秒重试窗口；客户端应避免并发刷新。若刷新已成功但响应丢失，用户需重新登录。

### 公共服务隔离回归测试

安装依赖后运行以下命令。测试使用 FakeRedis（含 Lua）和 Mock 短信客户端，不发送短信、不连接实际 Redis 或数据库；--noconftest 跳过仓库本地的集成测试数据库初始化。

    python -m pytest --noconftest -q tests/test_sms.py tests/test_security.py tests/test_exceptions.py tests/test_storage_soft_delete.py

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
