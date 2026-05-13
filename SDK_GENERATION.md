# 前端 SDK 自动生成指南

## 概述

本项目已配置 OpenAPI 文档自动生成，前端可以通过 `@hey-api/openapi-ts` 工具自动生成类型安全的 SDK。

## OpenAPI 文档地址

### 本地开发环境
- **OpenAPI JSON**: http://localhost:8000/api/v1/openapi.json
- **Swagger UI 文档**: http://localhost:8000/docs
- **ReDoc 文档**: http://localhost:8000/redoc

### 生产环境
- **OpenAPI JSON**: https://api.lxyy.fun/api/v1/openapi.json
- **Swagger UI 文档**: https://api.lxyy.fun/docs
- **ReDoc 文档**: https://api.lxyy.fun/redoc

## 前端生成 SDK

### 方式一：使用 npx 命令（推荐）

```bash
# 从本地开发环境生成
npx @hey-api/openapi-ts \
  -i http://localhost:8000/api/v1/openapi.json \
  -o src/lib/client

# 从生产环境生成
npx @hey-api/openapi-ts \
  -i https://api.lxyy.fun/api/v1/openapi.json \
  -o src/lib/client
```

### 按需导入特定模块

如果只需要部分模块的接口，可以使用 `--services.include` 或 `--services.exclude` 参数：

```bash
# 只生成"用户授权"和"资源存储"模块
npx @hey-api/openapi-ts \
  -i http://localhost:8000/api/v1/openapi.json \
  -o src/lib/client \
  --services.include "^(用户授权|资源存储)"

# 排除"管理后台"模块
npx @hey-api/openapi-ts \
  -i http://localhost:8000/api/v1/openapi.json \
  -o src/lib/client \
  --services.exclude "^管理后台"
```

**可用的模块标签**：
- 用户授权、管理后台、资源存储、微信认证、小程序登录
- 交易助手、恰好、语筑
- 时空图书馆、时空图书馆-管理端
- AI对话网关、西西弗斯认知引擎、影子董事会
- 言图引擎、AuraKey AI 绘画、在线高考

### 方式二：使用配置文件

项目根目录已提供 `openapi-config.json` 配置文件，可以直接运行：

```bash
# 安装依赖
npm install -D @hey-api/openapi-ts

# 使用配置文件生成
npx @hey-api/openapi-ts -c openapi-config.json
```

### 方式三：添加到 package.json scripts

在前端项目的 `package.json` 中添加：

```json
{
  "scripts": {
    "generate:api": "openapi-ts -i http://localhost:8000/api/v1/openapi.json -o src/lib/client",
    "generate:api:prod": "openapi-ts -i https://api.lxyy.fun/api/v1/openapi.json -o src/lib/client"
  },
  "devDependencies": {
    "@hey-api/openapi-ts": "^0.x.x"
  }
}
```

然后运行：

```bash
npm run generate:api
```

## 生成的 SDK 使用示例

```typescript
import { client } from './lib/client';
import { StorageService } from './lib/client/services';

// 配置 base URL
client.setConfig({
  baseUrl: 'https://api.lxyy.fun'
});

// 使用生成的服务
const response = await StorageService.getUploadToken();
console.log(response.data);
```

## 注意事项

1. **确保后端服务运行**：生成 SDK 前需要确保后端服务正在运行
2. **CORS 配置**：如果从浏览器访问 OpenAPI JSON，确保后端 CORS 配置正确
3. **版本管理**：建议在 CI/CD 中自动生成 SDK，确保前后端接口同步
4. **类型安全**：生成的 SDK 包含完整的 TypeScript 类型定义

## 高级配置选项

```bash
npx @hey-api/openapi-ts \
  -i http://localhost:8000/api/v1/openapi.json \
  -o src/lib/client \
  --client @hey-api/client-fetch \
  --types.enums javascript \
  --services.asClass true
```

### 配置说明
- `--client`: 选择 HTTP 客户端（fetch, axios, xhr）
- `--types.enums`: 枚举类型生成方式
- `--services.asClass`: 将服务生成为类而不是函数

## 更新 SDK

当后端 API 有更新时，重新运行生成命令即可：

```bash
npm run generate:api
```

## 故障排查

### 问题：无法访问 OpenAPI JSON
- 检查后端服务是否运行：`curl http://localhost:8000/health`
- 检查端口是否正确：默认 8000

### 问题：生成的类型不正确
- 确保后端所有路由都定义了 `response_model`
- 检查 Pydantic 模型是否有完整的类型注解
- 访问 `/docs` 查看 OpenAPI 文档是否正确

### 问题：CORS 错误
- 后端已配置 `allow_origins=["*"]`，应该不会有 CORS 问题
- 如果仍有问题，检查 `core/config.py` 中的 `BACKEND_CORS_ORIGINS` 配置
