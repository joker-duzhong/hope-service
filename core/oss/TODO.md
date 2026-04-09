# 任务目标：基于《后端宇宙架构规范》开发 Qiniu OSS 存储模块

你好，AI 助手。请严格遵守我的《后端宇宙架构规范》，完成以下两个独立功能模块的开发。
注意：当前项目中已经存在完整的 `ResponseModel`, `PaginatedResponse`, 基础 `CoreModel`, `get_current_user` 依赖, `Celery` 与 `Redis`。
请分步骤阅读以下 TODO List，并在编码时严格落实**验收标准**。


## 🟡 任务：七牛云 OSS 客户端直传与资源管理

**架构定位**：底层 OSS 签名与直传 Token 生成逻辑放入 `core/oss/`，文件元数据落库与对外接口放在已有（或新建）的独立模块 `apps/storage/`。

### TODO 2.1 全局配置更新 (`core/config.py`)
- [ ] 在 `Settings` 类中增加七牛云相关的环境变量。
- **验收标准**：
  - 必须包含：`QINIU_ACCESS_KEY`, `QINIU_SECRET_KEY`, `QINIU_BUCKET_NAME`, `QINIU_DOMAIN` (绑定用于访问的公网 CDN 域名)。

### TODO 2.2 七牛云核心鉴权与签名逻辑 (`core/oss/qiniu_client.py`)
- [ ] 基于七牛云官方 Python SDK 提供凭证生成等能力。
- **验收标准**：
  - 实现 `generate_upload_token()` 方法，支持前端直传策略。
  - 实现 `delete_file_from_oss(object_key: str)` 方法，供后续异步调用。

### TODO 2.3 数据库模型 (`apps/storage/models.py`)
- [ ] 确保存在 `storage_files` 表，继承全局 `CoreModel`。
- **验收标准**：
  - 严格包含以下字段：`name`, `url` 与 `thumb_url` (仅存储 OSS object_key), `size`, `type`, `hash`, `owner` (user_id)。
  - 注意：`url` 只能存相对路径（如 `2024/05/uuid.png`），严禁在数据库中直接存带域名的绝对路径。

### TODO 2.4 上传与 URL 转换服务 (`apps/storage/services.py`)
- [ ] 实现生成直传凭证业务、前端上传成功后的确认落库业务、动态拼接 URL 业务。
- **验收标准**：
  - 提供 `get_file_urls_by_ids(file_ids: list) -> dict` 服务：能够根据文件 ID 列表，读取 `url` 字段，配合全局 `QINIU_DOMAIN` 动态拼接成 `http(s)://domain/url` 格式返回。

### TODO 2.5 接口与异步删除逻辑 (`apps/storage/router.py` & `apps/storage/tasks.py`)
- [ ] 暴露获取 Upload Token、确认上传元数据保存、获取文件信息、逻辑删除文件接口。
- [ ] 在 `tasks.py` 中编写异步物理删除任务。
- **验收标准**：
  - **上传流**：接口 1 (`/upload-token`) 颁发 Token 给前端 -> 前端直传七牛 -> 接口 2 (`/confirm-upload`) 接收前端传回的 hash、key 等元数据并落库。所有操作必须带入 `get_current_user` 以绑定 `owner`。
  - **删除流（必须严格执行）**：当用户调用 `/delete/{file_id}` 时，Router 调用 Service 先将数据库对应的记录标记为 `is_deleted = True`（软删除），随后触发 `delete_oss_file_task.delay(object_key)` 交由 Celery 在后台异步调用七牛云 SDK 清理真实存储，避免产生无用资费且不阻塞当前接口响应。
  - 统一使用 `ResponseModel` 封装返回值。