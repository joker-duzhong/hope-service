# Changelog

## 2026-05-08

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
