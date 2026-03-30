# Hope Service

统一后端服务中心 - 支持多项目共享的后端服务

## 技术栈

- **Web框架**: FastAPI
- **数据库**: PostgreSQL + SQLAlchemy ORM
- **认证**: JWT Token
- **异步任务**: Celery + Redis
- **部署**: Docker + Docker Compose

## 项目结构

```
hope-service/
├── app/
│   ├── api/v1/              # API路由
│   │   ├── auth.py          # 认证接口
│   │   └── tools.py         # 工具集接口
│   ├── core/                # 核心配置
│   │   ├── config.py        # 配置管理
│   │   ├── database.py      # 数据库连接
│   │   └── security.py      # 安全模块
│   ├── models/              # 数据库模型
│   ├── schemas/             # Pydantic数据验证
│   ├── services/            # 业务服务
│   │   ├── auth/            # 认证服务
│   │   ├── tools/           # 工具集（simpletex等）
│   │   ├── yuzhu/           # 语筑模块（预留）
│   │   ├── tianqi/          # 天启量化（预留）
│   │   └── llm/             # 大模型服务（预留）
│   └── utils/               # 工具函数
├── celery_worker/           # Celery任务
├── tests/                   # 测试
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## 快速开始

### 1. 环境准备

```bash
# 复制环境变量配置
cp .env.example .env

# 编辑 .env 文件，修改必要的配置
```

### 2. Docker 部署（推荐）

```bash
# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f app

# 停止服务
docker-compose down
```

### 3. 本地开发

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --reload --port 8000
```

### 4. 访问服务

- API文档: http://localhost:8000/docs
- ReDoc文档: http://localhost:8000/redoc
- 健康检查: http://localhost:8000/health

## API接口

### 认证接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/auth/register | 用户注册 |
| POST | /api/v1/auth/login | 用户登录 |
| POST | /api/v1/auth/refresh | 刷新令牌 |
| GET | /api/v1/auth/me | 获取当前用户 |

### 工具集接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/tools/formula-ocr | 公式识别（文件上传） |
| POST | /api/v1/tools/formula-ocr-base64 | 公式识别（Base64） |

## 配置说明

主要环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| SECRET_KEY | JWT密钥 | - |
| POSTGRES_PASSWORD | 数据库密码 | - |
| SIMPLETEX_API_TOKEN | SimpleTex API Token | - |

## 模块说明

### ✅ 已实现
- 认证系统（注册/登录/JWT）
- 工具集 - SimpleTex公式识别

### ⏸️ 预留模块
- 语筑 - 爬虫 + 微信公众号
- 天启量化 - 策略接口
- 大模型服务 - Claude/OpenAI/国内模型
