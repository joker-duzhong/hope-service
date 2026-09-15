# Changelog

## 2026-09-15 AuraKey Image Error Diagnostics

- 上游 HTTP 错误增加安全诊断：错误码、类型、参数、请求 ID、响应格式及固定消息分类；关联任务 ID、模型、JSON/multipart、参考图类型/大小和请求耗时。
- 不记录上游自由文本、提示词、参考图文件名或凭据；任务失败文案保持通用 HTTP 信息，退款和单次请求行为保持原有逻辑。
- 验证：`image_generation_test.py` 与 `aurakey_asset_log_schema_test.py` 共 105 项通过，覆盖 400/503、敏感内容回显、异常响应和退款一次；`git diff --check` 通过。未调用真实生图接口或部署线上。
- 文件：`core/llm/engine.py`、`apps/aurakey/tasks.py`、`image_generation_test.py`、`aurakey_asset_log_schema_test.py`、`CHANGELOG.md`。

## 2026-09-15 Remove Temporary Review Artifacts

- 删除已完成应用的 `artifacts/` 临时审阅目录，包括补丁、代码副本与隔离构建文件；先解除两个依赖目录联接，真实 AuraKey 项目依赖保持完整。
- 移除修订文件清单中的留档链接。已确认三个仓库的源码和构建脚本不依赖该目录，目录删除及依赖目录保留检查通过。
- 文件：删除 `artifacts/`；修改 `docs/aurakey-revision-files.md`、`CHANGELOG.md`。

## 2026-09-15 AuraKey Unified Identity Clients

- 管理员仅保留扫码与手机验证码登录；旧 `POST /api/v1/auth/login` 返回 410，不查询账号或签发凭据，移除密码登录请求模型。新登录继续要求已验证手机号和相应权限，不放宽跨业务校验。
- 新增 `GET /api/v1/aurakey/admin/session`，统一校验业务范围、账号状态、Token 版本及当前有效管理角色，返回无缓存的标准用户资料。支持前端在扫码、短信登录后确认管理权限。
- AuraKey 业务资料弃用旧用户表 OpenID，返回 `null`；头像资源 ID 批量解析为可显示 URL，覆盖用户资料、公开图库和管理图库，失效头像资源返回空值。管理图库的 OpenID 搜索改用当前 AuraKey 小程序映射内的有效微信身份。
- 更新两阶段身份接入、管理会话、头像和支付参数文档；支付客户端只提交 `product_id`，不再依赖或传入 OpenID。
- 小程序、Web、管理后台及 `www` 配套修改均已写入实际仓库；管理后台默认扫码，保留手机验证码入口并彻底移除密码表单及调用。扫码与短信登录完成后均由后端校验管理权限。详见 `docs/aurakey-client-migration.md` 和 `docs/aurakey-revision-files.md`。
- 验证：身份、扫码、会话范围、刷新、用户资料与生图回归共 356 项通过（`.venv/Scripts/python.exe -m pytest aurakey_identity_test.py tests/test_auth_flows.py tests/test_identity_login.py tests/test_scan_login.py tests/test_security.py tests/user_profile_test.py aurakey_asset_log_schema_test.py image_generation_test.py -q --noconftest`），包括密码入口固定 410、扫码及短信登录后普通用户拒绝访问管理会话。使用模拟微信/短信、Redis 和数据库，未操作真实账户或部署服务。
- 配套仓库验证：小程序 17 项、Web 25 项、管理端 28 项、`www` 9 项模拟测试通过，类型检查及四个项目构建通过；Web 与管理端修改文件 ESLint 通过。浏览器工具不可用，尚未进行微信真机或生产环境联调。
- 本次文件：`core/users/schemas.py`、`core/users/router.py`、`apps/aurakey/admin_router.py`、`apps/aurakey/router.py`、`apps/aurakey/schemas.py`、`apps/aurakey/services.py`、`tests/test_auth_flows.py`、`aurakey_identity_test.py`、`docs/identity-login-api.md`、`docs/aurakey-client-migration.md`、`docs/aurakey-revision-files.md`、`docs/repository-follow-up-todo.md`、`apps/aurakey/api.md`、`CHANGELOG.md`。

## 2026-09-14 AuraKey OneAPI Image Generation

- AuraKey `/task/generate` 与 `/task/generate-stream` 统一使用现有 Celery 后台任务；无参考图发送 JSON，有参考图通过 multipart 的 `image` 文件字段上传，两种请求均调用 `/v1/images/generations`。
- 按在问接口约定固定 `n=1`、`response_format=b64_json`，保留响应 usage；解码生成图片后存入资源系统，存储完成后才标记任务成功。图片接口错误不再输出上游原始响应体。
- 两个入口均通过 `reference_images_ids` 接收最多一张参考图（原 stream 上限为九张）；读取原图文件而非构造聊天消息，缺失、无效或读取失败时明确失败，避免忽略参考图继续生成。
- 复用现有模型权限、扣费、查询、发布及失败退款逻辑；任务入队失败也及时标记失败并退款，保留已有远程任务的状态查询兼容，同步接口字段和示例。
- 验证：`.venv/Scripts/python.exe -m pytest aurakey_asset_log_schema_test.py image_generation_test.py -q --noconftest`，78 项通过；请求、资源存储、任务队列和退款使用模拟依赖，未调用真实收费图片接口。系统默认 Python 缺少短信 SDK，改用项目现有 `.venv` 验证。
- 文件：`core/llm/engine.py`、`apps/aurakey/tasks.py`、`apps/aurakey/services.py`、`apps/aurakey/schemas.py`、`apps/aurakey/router.py`、`apps/aurakey/api.md`、`aurakey_asset_log_schema_test.py`、`image_generation_test.py`、`CHANGELOG.md`。

## 2026-09-10 Identity Link Database Diagnostics

- 账号关联数据库异常新增 `Identity link database failure` 日志，记录操作阶段、重试次数、SQLAlchemy/驱动异常类型、经格式校验的 SQLSTATE 及本模块出错代码位置。
- 阶段覆盖事务锁、身份/手机号用户/身份所属用户查询、账号创建与关联、事务提交及刷新；通过 ContextVar 隔离并发请求，在回滚前记录异常，并在每次尝试后恢复上下文。
- 不记录异常文本、完整堆栈、SQL 语句或参数、手机号、OpenID、验证码及登录票据；保留已有事务、重试及客户端错误响应行为。本次为诊断改动，线上根因需部署后根据新日志确认。
- 补充故障阶段、诊断脱敏、SQLSTATE 格式过滤、完整性冲突重试、回滚失败和并发阶段隔离测试。
- 验证：身份登录与旧登录回归共 191 项通过，`git diff --check` 通过；使用模拟依赖及 `--noconftest`，未连接业务数据库、未发送真实短信或微信请求、未部署线上。
- 文件：`core/users/identity_service.py`、`tests/test_identity_login.py`、`CHANGELOG.md`。

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
