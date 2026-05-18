# Changelog

## 2026-05-13

- 修复 AuraKey `GET /api/v1/aurakey/user/history` 的 `processing` 任务进度不再固定为初始值，改为复用详情接口的进度计算逻辑。
- AuraKey 流式生图参考图改为下载后转换成 base64 data URL 传给上游，避免第三方无法识别参考图公网地址。
- AuraKey 流式生图任务支持最多 9 张参考图资源 ID，后端内部转换为多模态消息传给上游，任务状态、历史和画廊详情返回参考图资源结构。
- 为 AuraKey 创作历史列表补充 `progress` 进度字段，规则与任务详情接口保持一致。
- 缩短 Alembic 迁移 `0012` 的 revision ID，修复 PostgreSQL 中 `alembic_version.version_num` 长度不足导致升级失败的问题。
- AuraKey 画廊列表、详情和点赞改为基于 `aurakey_tasks` 返回公开作品，公开条件为生成成功、用户已公开且审核通过。
- 为 AuraKey 任务新增 `category_id`、`publish_status`、`published_at`、`like_count`、`view_count` 字段，并提供增量迁移脚本。
- 新增 C 端作品公开状态变更接口和 B 端画廊审核状态接口，支持通过审核状态关闭公开访问。
- 新增 AuraKey B 端作品列表、单个公开状态变更和批量公开状态变更接口；审核状态不再修改用户公开意愿。

## 2026-05-11

- 为用户中心当前用户响应增加 `is_superuser` 标识，便于前端识别超级管理员权限。
- 修复 Celery worker 未预先注册核心用户/角色 ORM 模型，导致 AuraKey 流式生图后台任务在查询任务记录时失败的问题。
- 补充 JustRight 定时任务显式导入，避免 worker 收到 `apps.just_right.tasks.notify_state_updates` 时提示未注册。

## 2026-05-08

- 修复 AuraKey VIP 商品支付成功后不发放算力、不写资产流水的问题，并补充微信回调金额校验。
- AuraKey 商品新增 `vip_type`、`vip_level`、`valid_days` 字段，支持会员和点数包自定义权益有效期。
- 新增 AuraKey 点数批次账本，支持点数按商品、签到、邀请等来源设置有效期并按最早过期优先消耗。
- 新增 `GET /api/v1/aurakey/user/entitlement` 当前权益接口和 `GET /api/v1/aurakey/orders` 购买记录接口。
- 新增 AuraKey 系统配置接口和管理端配置读写接口，支持配置签到奖励、邀请奖励、默认商品有效期并预留 `custom` JSON。
- 将微信支付回调迁移到 core 统一入口 `POST /api/v1/payments/wechat/notify`，并移除 AuraKey 业务内 `/wechat-notify`。
- 支持 `WECHAT_PAY_PRIVATE_KEY` 配置 PEM 文件路径读取商户 API 私钥。
- 为 `GET /api/v1/aurakey/user/profile` 增加 `openid` 返回字段。
- 为 `POST /api/v1/aurakey/task/generate-stream` 增加公开参数，选择公开时生成成功后自动发布到画廊。
- 新增基于聊天流式接口的图片生成能力，并为 AuraKey 增加后台任务化流式生图入口。
- 修复 AuraKey 邀请信息、签到和任务状态接口返回字段与响应模型不一致的问题。
- 修复 AuraKey 算力流水列表在序列化 ORM 记录时可能返回 500 的问题。
- 为用户中心增加通用资料修改能力，支持更新昵称、用户名、邮箱与头像。
- 注册和资料修改时补充 `username`、`email` 唯一性校验。
- 用户响应中的头像统一返回结构体；资源 ID 会展开资源信息，外链头像会包装到结构体 `url` 字段。
- 为 AuraKey 公开接口补充 OpenAPI 摘要、描述和参数说明，便于在 `/docs` 查看接口用途。
- 为 `GET /api/v1/aurakey/gallery/list` 增加 `categoryId` 分类筛选参数。
- 新增无需鉴权的 `GET /api/v1/aurakey/gallery/categories` 分类列表接口，并移除旧的管理端分类列表 GET 暴露。
- 同步 AuraKey API 文档中的分类接口路径和画廊列表筛选参数。
