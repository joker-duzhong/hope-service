# Changelog

## 2026-09-09 SMS Test Flow Without Send Limits

- `test: "hope"` 生成验证码时跳过发送冷却及每日配额检查，不占用或清除真实短信的限流记录；真实发送仍保留 60 秒冷却和 24 小时 5 次上限。
- 保留测试验证码摘要存储、5 分钟有效期、校验尝试上限及一次性消费；重新生成覆盖原验证码会话。
- 测试模式失败提示改为验证码生成或保存失败，提示检查 Redis；新增已有配额耗尽时放行、不影响真实配额及重新生成验证码的回归测试，同步接口文档。
- 验证：短信与旧登录回归共 86 项通过，`git diff --check` 通过；使用模拟依赖及 `--noconftest`，未发送真实短信、未连接业务数据库、未部署线上。
- 文件：`core/sms.py`、`core/users/router.py`、`tests/test_sms.py`、`docs/identity-login-api.md`、`CHANGELOG.md`。

## 2026-09-09 SMS Test Flow

- 短信发送接口新增 `test` 参数，精确传入 `hope` 时跳过短信供应商调用，并在 `data.code` 返回四位测试验证码；按测试阶段要求，所有环境均可使用。
- 测试模式复用验证码摘要存储、有效期、发送配额及一次性校验；Redis 写入失败或触发频率限制时不返回验证码，普通短信流程不返回验证码。
- 新增发送专用请求及响应模型，避免测试参数扩散至其他手机号接口；修正普通发送成功响应的 `message` 字段。
- 补充短信与 HTTP 契约测试、测试入口使用说明及正式业务开放前移除要求。
- 验证：短信、旧登录及身份登录共 215 项测试通过（含 production 环境测试入口）；`git diff --check` 通过。使用模拟 Redis/短信供应商及 `--noconftest`，未发送真实短信、未连接业务数据库、未部署线上。
- 文件：`core/sms.py`、`core/users/schemas.py`、`core/users/router.py`、`tests/test_sms.py`、`docs/identity-login-api.md`、`CHANGELOG.md`。

## 2026-09-09 Passport Subdirectory Deployment

- 微信 OAuth 回调新增精确允许 `/passport/wechat/callback`，保留旧 `/wechat/callback` 兼容；API 路由和认证流程不变。
- 保留源地址白名单、开发环境 HTTP 限制和 URL 凭据/片段校验；拒绝路径参数、其他目录、重复分隔符、路径后缀及未登记源地址。空白名单的既有行为不变。
- 增加新旧路径、生产/本地环境及错误路径回归测试；文档明确 `https://tool.lxyy.fun/passport/` 的页面入口与仅含源地址的后端配置，补充业务端二维码迁移说明。未修改真实环境配置或数据库。
- 验证：身份、旧登录、扫码、凭据及 OpenID 回归共 239 项通过，使用 `--noconftest`、模拟依赖及不可用的本地数据库端口，未连接业务数据库或请求真实微信。配套前端构建、16 组浏览器场景及 16 项配置边界检查通过；两个仓库 `git diff --check` 通过。现有 Pydantic/Starlette 弃用警告未作无关修改。
- 文件：`core/users/identity_service.py`、`tests/test_identity_login.py`、`docs/identity-login-api.md`、`docs/scan-login-api.md`、`CHANGELOG.md`。

## 2026-09-09 Optional Passport Allowlists

- `PASSPORT_WECHAT_APP_IDS`、`PASSPORT_CALLBACK_ORIGINS` 未配置或为 `[]` 时放行对应的 AppID、回调源检查；非空时继续执行白名单限制。
- 保留 `WECHAT_APPS` 配置要求、公众号/小程序渠道隔离、小程序业务映射、HTTP 环境限制和固定回调路径等校验；显式拒绝缺少主机名的回调 URL。
- 增加空白名单放行、非空白名单拦截、渠道冲突和回调地址边界回归测试，并同步部署文档。
- 验证：身份登录与旧登录回归共 137 项通过；使用模拟依赖及不可用的本地数据库端口，未连接业务数据库或发送真实微信请求。
- 文件：`core/users/identity_service.py`、`tests/test_identity_login.py`、`docs/identity-login-api.md`、`CHANGELOG.md`。

## 2026-09-09 Local HTTP OAuth Callback

- 仅 ENVIRONMENT 为 development/dev/local 时允许白名单内 HTTP 回调，源地址、端口、固定回调路径和 URL 凭据/fragment 校验保持不变。
- production/prod、预发布、空值及未知环境仍要求 HTTPS；DEBUG 或客户端 env=local 不能放开正式后端限制。不修改真实公众号或环境配置。
- 新增 16 项环境/回调边界用例，与身份、扫码、刷新回归合计 123 项通过；配套前端 6 组隔离浏览器场景通过。未操作真实业务数据库或发送微信请求。
- 文件：`core/users/identity_service.py`、`tests/test_identity_login.py`、`docs/identity-login-api.md`、`CHANGELOG.md`。
- 最终扩大回归：身份、旧登录、扫码、刷新及 OpenID 契约共 174 项通过；配套前端类型检查及生产构建通过。

## 2026-09-09 Unified Identity Login

- 新增公众号/小程序两阶段身份 API：微信 code 只建立临时验证票据；手机号证明完成前不创建正式用户、不签发 Token。新身份可关联同手机号已有用户，历史正式账号归属冲突不自动合并。
- 用户及身份通过 PostgreSQL 事务锁、行锁和唯一约束关联；Redis 票据及 code 防重。补齐票据过期、并发唯一冲突、签发失败和结果丢失后的重新验证路径。
- Access/Refresh Token 与刷新会话绑定 app_scope；业务路由拒绝跨应用凭据，扫码确认只接收 Passport Token，PC 兑换范围取可信事务。返回当前范围有效角色并隐藏跨应用 OpenID；同应用多微信身份不任选付款身份。
- 公众号登录和小程序 AppID 渠道分开登记，增加 HTTPS 回调源白名单；小程序手机号由后端按票据 AppID 兑换，不信任客户端提交的 OpenID/手机号作为证明。
- 废弃旧无手机号 Token 登录/绑定流程。旧公众号事件扫码创建、查询、兑换入口返回 410，SCAN/subscribe 不再直接创建用户或变更登录态，消除绕过两阶段验证的旧路径。
- 发布存在破坏性变化：无 app_scope 的旧 Token 拒绝；PC 手机号直登必须传 app_key，小程序须迁移新接口并配置映射。业务资料仍由各应用既有初始化机制管理，不新增用户副本、不授予角色、不自动搬迁历史资产。
- 验证：245 项测试通过（含已有 Teacher Logbook 43 项）；5 项真实 PostgreSQL 15 测试覆盖同手机号八路并发、同身份并发、不同手机竞争、已有用户角色保留及历史冲突回滚。其余认证、短信、Redis CAS、异常和 HTTP 契约使用隔离替身。未连接现有业务库、未发送真实微信/SMS 请求；不执行生产迁移。现有 Pydantic/Starlette 弃用警告未作无关修复。
- 配套 H5 类型检查、生产构建和 9 组模拟浏览器流程通过；真机微信、线上域名、SMS、各业务前端迁移仍需部署方联调。

### Files

- 新增：`core/auth_scope.py`、`core/users/identity_schemas.py`、`core/users/identity_service.py`、`core/users/identity_router.py`。
- 修改：`core/config.py`、`core/dependencies.py`、`core/security.py`、`core/users/dependencies.py`、`core/users/schemas.py`、`core/users/services.py`。
- 修改：`core/users/router.py`、`core/users/miniapp_router.py`、`core/users/scan_router.py`、`core/users/scan_service.py`、`core/wechat/router.py`、`core/wechat/services.py`、`main.py`。
- 测试：新增 `tests/test_identity_login.py`、`tests/test_identity_postgres.py`；修改 `tests/test_auth_flows.py`、`tests/test_scan_login.py`、`tests/test_security.py`，`.gitignore` 放行新增测试。
- 配置与文档：`.env.example`、`docs/identity-login-api.md`（新增）、`docs/scan-login-api.md`、`docs/miniapp_login.md`、`CHANGELOG.md`。

## 2026-09-09

- Full Teacher Logbook migration support; no common, core, shared authentication or database modules modified.
- Added explicit legacy JSON migration with dry-run validation, new student IDs, safe relation mapping, empty-class checks and transactional writes.
- Fixed date and keyword filters, stable pagination, referenced committee-role deletion checks and update validation errors.
- Seat swaps release both active unique positions before reassignment; cleared boards reuse their existing row; restored board versions remain monotonic.
- Backup validation now rejects malformed fields, duplicate IDs, unsafe ownership fields, invalid course times, broken references and invalid seat layouts before replacement. Restore errors roll back the transaction; course times serialize and restore correctly.
- Added version-controlled tests under apps/teacher_logbook/tests with a module-local ignore exception because the root rules ignore test paths. No root ignore rules changed. The local legacy test fixture was updated for the stricter backup validation.
- Verification: 43 isolated schema, service and HTTP contract tests pass. CRUD response tests cover all 18 generic resources and both PATCH/POST update methods. No live account or production business data was created or deleted.
- Teacher Logbook: typed resource, dashboard, seat layout and backup validation responses.
- Added POST update aliases within the Teacher Logbook router for miniapp compatibility; existing PATCH endpoints remain available.
- Student CSV exports query all authorized students, not only the first 100.
- Dashboard queries actual high-risk alerts, pending todos, latest exam and recent work records.
- Changes are confined to the Teacher Logbook module, its tests and this log. No common or core files modified.
