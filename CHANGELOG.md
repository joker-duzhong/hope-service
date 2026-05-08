# Changelog

## 2026-05-08

- 为 AuraKey 公开接口补充 OpenAPI 摘要、描述和参数说明，便于在 `/docs` 查看接口用途。
- 为 `GET /api/v1/aurakey/gallery/list` 增加 `categoryId` 分类筛选参数。
- 新增无需鉴权的 `GET /api/v1/aurakey/gallery/categories` 分类列表接口，并移除旧的管理端分类列表 GET 暴露。
- 同步 AuraKey API 文档中的分类接口路径和画廊列表筛选参数。
