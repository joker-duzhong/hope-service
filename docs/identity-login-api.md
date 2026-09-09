# 统一身份与两阶段登录

本说明取代旧文档中“微信 code 直接创建无手机号用户并签发 Token”的流程。所有路径以 `/api/v1` 为前缀，返回 `{code, message, data}`。OpenID 仅是标识，不是凭据。

## 身份与业务数据

- 平台用户唯一标识为 `User.id`。已验证且规范化的中国大陆手机号相同，新的未归属微信身份关联到同一用户。
- 微信身份按 `(provider=wechat, provider_app_id=AppID, subject=OpenID)` 唯一定位，不仅凭 OpenID 或 UnionID 合并。公众号和不同小程序有独立身份。
- 同一个授权中心公众号供 A/B 共用时，其已绑定用户扫描 A/B 都免去重复手机验证。不能为每个业务随意更换公众号 AppID 后仍假定 OpenID 相同。
- 小程序初次出现的新身份必须验证手机号。即使同属一个微信开放平台，也不把客户端 UnionID 当成免验证依据。
- 不创建通用的“应用用户副本”或自动授予角色。业务表以统一 `User.id` 关联，已有业务资料仍由各业务的首次访问初始化逻辑负责；例如 `GaokaoService.get_or_create_persona`、`AurakeyService.get_or_create_user_asset`。同一应用 PC/小程序使用相同业务 API 和用户 ID，才访问同一份业务数据；前端缓存仍需刷新，不包含实时推送机制。
- A/B 的业务资料和角色互相隔离；手机号等基础资料属于平台共享信息。登录响应 `user.roles` 仅含当前应用有效且未删除的角色，`openid` 不暴露其他应用的身份。

## 部署配置

服务端密钥继续通过 `WECHAT_APPS` 环境变量提供，不得进入 Vite 配置、源码、二维码或日志。以下公开配置均默认空集合；两个 Passport 白名单未配置或为 `[]` 时不限制对应的 AppID 或回调源，小程序仍须配置业务映射：

| 变量 | 类型 | 含义 |
| --- | --- | --- |
| `PASSPORT_WECHAT_APP_IDS` | JSON 字符串数组 | 授权中心公众号 AppID 白名单；为空时允许 `WECHAT_APPS` 中已配置且未登记为小程序的 AppID，非空时仅允许列表内 AppID |
| `PASSPORT_CALLBACK_ORIGINS` | JSON 字符串数组 | 完整源地址（含端口，无尾部斜线）；为空时允许任意源，非空时仅允许列表内源。路径仅允许 `/passport/wechat/callback` 和兼容旧版的 `/wechat/callback`，正式环境必须 HTTPS，明确开发环境允许 HTTP |
| `MINIAPP_APP_SCOPES` | JSON 对象 | 小程序 AppID 到 `core/apps_config.py` 已启用业务 key 的单值映射 |

同一个 AppID 不能同时登记为公众号和小程序。小程序登录的目标业务完全由后端映射决定，不接受客户端 `app_key`。`AppConfig.wechat_appids` 不是本次登录范围配置的替代来源，支付和通知的 AppID 校验也不能据此视为已完成。

配置非空白名单时，前端各环境的 `VITE_*_WECHAT_APP_ID` 要对应公众号白名单，`VITE_*_PASSPORT_URL` 的源地址要对应回调源。前端部署基址可以包含 `/passport/`，但服务端回调源白名单和 CORS 只填写源地址，不填写目录。公众号平台也须登记相同网页授权域名。生产/本地分别配置 API、数据库和 Redis，禁止共享认证密钥与登录状态空间。

当前授权中心部署基址为 `https://tool.lxyy.fun/passport/`，网页回调为 `https://tool.lxyy.fun/passport/wechat/callback`。非空 `PASSPORT_CALLBACK_ORIGINS` 应包含 `https://tool.lxyy.fun`，公众号网页授权域名为 `tool.lxyy.fun`。后端保留旧回调路径用于兼容先后发布的前端，不放开任意路径、路径参数（`;...`）或路径后缀。先发布后端，再发布新版前端并更新业务端二维码地址；未修改真实环境配置。

仅服务端 `ENVIRONMENT` 为 `development`、`dev` 或 `local`（忽略大小写及首尾空格）时允许 HTTP 回调；回调白名单非空时还须匹配源地址。`production`、`prod`、预发布及未知值仍要求 HTTPS；`DEBUG=true` 或 URL 的 `env=local` 均不能放开正式后端限制。即使白名单为空，协议、有效主机名、固定回调路径、URL 用户名密码和 fragment 校验仍然有效。空回调白名单允许外部站点作为回调源，正式环境建议显式配置白名单。

前端仅在 local 环境放开 HTTP，缺少 WebCrypto 摘要 API 时使用本地打包的 SHA-256 库；安全随机 state、一次性 code 和票据规则不变。HTTP 不加密授权信息，仅限可信开发网络和测试账号。本改动不能绕过微信平台的 AppID、网页授权域名或开发者工具校验。

## 短信测试入口

`POST /api/v1/auth/sms/send` 的 JSON 请求体可传 `{"phone":"13800138000","test":"hope"}`。仅 `test` 精确等于 `hope` 时进入测试流程；省略、传 `null` 或其他字符串仍真实发送短信。

测试模式在所有环境（包括公网生产环境）可用，不需要短信供应商配置，也不调用短信供应商。成功响应为 `{"code":200,"message":"测试验证码已生成","data":{"code":"0123"}}`；验证码是四位字符串，保留前导零。普通发送成功返回 `data: null`，不返回验证码。

测试验证码在 Redis 中仍仅保存摘要，可用于现有手机号登录、绑定及 `/auth/identity/complete/sms` 流程；沿用 5 分钟有效期、60 秒冷却、每手机号每日 5 次和最多 5 次校验尝试，成功后只能消费一次。测试与真实发送共享限额。生成受限或 Redis 操作失败时返回 503，不返回验证码。

此入口会让知道固定参数的调用者获得指定手机号的有效登录验证码，包括已有账号；仅用于当前测试阶段，正式开放业务前必须移除或关闭该入口。测试过程可能创建或关联真实账号，并非数据库沙箱。

## H5 流程

1. PC 创建扫码会话，保存 `poll_token`，仅把 `transaction_id` 放入授权中心 `/passport/scan` URL。可附加 `app_key` 选择主题，但授权目标取后端会话。
2. H5 查询扫码状态、通知已扫码。微信内且配置 AppID 时，每个事务自动尝试一次 `snsapi_base`；已有有效 Passport 登录态则直接进入确认页。
3. `GET /auth/wechat/url?appid=...&redirect_uri=...&state=...&scope=snsapi_base` 返回微信 URL；前端保存随机 state 和原事务上下文，在回调校验并消耗。后端验证回调白名单。
4. `POST /auth/identity/h5`：`{appid, code, transaction_id?}`，不需要 Bearer Token。若携带已结束事务，先拒绝，不消耗微信 code。
5. 已归属且有手机号的身份返回 `AUTHENTICATED`；否则仅返回 `PHONE_REQUIRED` 临时验证票据，不创建用户，不下发正式 Token。
6. 验证手机成功后建立 Passport 登录态，再次检查原扫码事务；已过期就提示原设备重建。即使绑定完成，H5 也绝不自动确认 PC 登录。
7. 用户核对目标应用、账号并同意协议，点击确认。PC 用自己的 `poll_token` 和兑换码获得目标应用 Token。

## 小程序流程

1. 调用 `wx.login()` 获得一次性 code。
2. `POST /auth/identity/miniapp`：`{appid, code}`。不要先调用纯 OpenID 接口消耗同一 code，再把旧 code 提交到这里。
3. `AUTHENTICATED` 表示身份已绑定手机号，可直接建立本小程序业务登录态。
4. `PHONE_REQUIRED` 时只暂存 `login_ticket` 和有效期，不能把它作为 Bearer Token，也不能据此访问业务接口。
5. 用户主动授权手机号后，将 `getPhoneNumber` 的新 code 与票据提交到 `/auth/identity/complete/miniapp-phone`；也可使用短信验证完成接口。
6. 正式登录后以 `user.id` 访问业务资料。下次重新进入仍使用新 `wx.login()` code 验证身份，无需再次验证已绑定手机。

纯 OpenID 查询接口保持“只换标识、不创建用户、不签发 Token”，不是此登录流程的前置必经步骤。小程序业务前端不在本次两个仓库中，必须按此契约迁移才能启用新用户登录。

## 响应与完成接口

`PHONE_REQUIRED` 的 data 只包含 `status`、`login_ticket`、UTC `expires_at`。票据有效期 600 秒，Redis 仅以票据摘要作为 key，服务端保存已验证身份；票据不能放 URL 或日志。

`AUTHENTICATED` 的 data 包含 `status`、`access_token`、`refresh_token`、`token_type`、`app_scope`、`user`。此时 `user.phone` 非空且 `needs_phone_binding=false`。

| 接口 | 请求体 | 前置条件 |
| --- | --- | --- |
| `POST /auth/identity/complete/sms` | `{login_ticket, phone, code, accepted_terms:true}` | 原 `/auth/sms/send` 获得的四位短信验证码；公众号/小程序票据均可 |
| `POST /auth/identity/complete/miniapp-phone` | `{login_ticket, phone_code, accepted_terms:true}` | 小程序专用票据；后端按票据 AppID 兑换手机号，不接受客户端明文手机号替代证明 |

目前仅支持中国大陆手机号。勾选协议不是可绕过手机号证明的凭据；接口校验 `accepted_terms`，尚未建设协议版本和同意记录的持久化审计体系。

## PC 手机号登录与 Token 范围

PC 手机号登录请求改为 `POST /auth/phone/login`：`{phone, code, app_key}`。不传 `app_key` 表示 Passport，不可拿默认 Passport Token 调业务接口。

- H5 公共公众号登录签发 `app_scope=passport`，仅该范围可以确认或取消扫码请求。
- PC 扫码兑换签发会话内目标 `app_key` 范围的 Token；小程序签发其后端映射范围的 Token；PC 手机号登录签发已校验目标范围。
- Access/Refresh Token 和 Redis refresh 会话都保存范围，刷新不能改变范围。A 的 Token 访问 B 路由返回 403；伪造 Header `app` 不会改变路由范围。
- 应用角色仍需单独授权，普通用户首次登录不会变成会员或管理员。后台密码登录仅超级管理员，签发 `admin_web` Token。
- 公共 `/auth/me` 返回当前凭据范围内的基础用户信息。各业务仍须执行资源归属检查；本次不宣称审计并修复了所有支付、存储等公共接口的业务权限。

## 冲突、并发与恢复

| 场景 | 行为 |
| --- | --- |
| 新身份 + 已验证手机号已有用户 | 直接增加身份关联，不创建另一用户 |
| 新身份 + 新手机号 | 用户与身份在同一数据库事务中创建 |
| 身份已属于另一个有手机号用户 | 409，不转移身份，不合并用户 |
| 历史无手机号用户 + 未占用手机号 | 验证对应身份及版本后补手机，保留原用户 ID 和业务数据 |
| 历史无手机号用户 + 已属另一个用户的手机号 | 409，交人工审核；不得自动丢弃任一账号或搬迁资产 |
| 已停用用户或已删除身份 | 拒绝登录/关联，不创建替代账号 |
| 同手机号/身份并发关联 | PostgreSQL 事务 advisory lock、行锁及唯一约束；唯一冲突回滚后重查一次 |
| 短信验证码错误 | 400，票据保留，用户可重新取短信 |
| 票据已使用/过期 | 410，获取新微信 code 重启验证 |
| 微信 code 重复 | 409；同 channel + AppID + code 原子防重，不能重试旧 code |
| 429 | 等待 `Retry-After`，勿自动快速重试 |
| 数据库失败或手机号 code 已被消耗 | 重新取得微信 code 和手机号证明，不能保证原凭据可重试 |
| 身份已提交、签发失败或响应丢失 | 重新获取微信 code；已完成关联会被查到，不重复创建用户 |

Redis 票据先消费，数据库再提交，二者不声称构成分布式事务。失败时宁可要求重新验证，不允许复用票据抢占已关联身份。扫码兑换和 Refresh Token 同样严格一次性，响应丢失须重新登录/扫码。

同一用户在同一 AppID 下可能绑定多个微信身份。只凭 `user.id + appid` 无法确定付款或通知对象时，`get_wechat_openid` 拒绝任选一个；需要明确当前操作身份的业务应另行接入相应身份上下文，而不是把平台用户 ID 误当 OpenID。

## 发布和验证

这是认证契约的破坏性升级：无 `app_scope` 的旧 Token 一律拒绝；前端缓存也会清理。旧 `/auth/wechat/login` 和 `/auth/miniapp/login` 只允许已绑定手机号身份，不再创建无手机号账号；旧 Bearer 绑定接口已标为废弃。PC 直登、小程序及管理端必须协调发布，不提供无期限旧凭据兼容。

旧公众号事件扫码 `/auth/wechat/qrcode`、`/auth/wechat/status`、`/auth/wechat/exchange` 返回 410，公众号 SCAN/subscribe 事件不再创建用户或变更登录态。原 Webhook 验签及纯 OpenID 查询保留。不要继续生成旧版事件二维码；它不能绕过手机号验证和 H5 显式确认。

不新增数据库迁移，但必须先具备现有迁移创建的 `core_user_identities`、手机号和身份唯一约束及 `token_version`。部署前核对历史 `core_users.openid` 记录；没有可验证 AppID 的旧记录不得猜测迁移。先备份和对账，再做人工迁移；本次未操作现有数据库。

隔离验证命令：

```powershell
./.venv/Scripts/python.exe -m pytest --noconftest -q tests/test_identity_login.py tests/test_identity_postgres.py tests/test_auth_flows.py tests/test_scan_login.py tests/test_security.py tests/test_wechat_openid.py tests/test_sms.py tests/test_exceptions.py tests/test_storage_soft_delete.py tests/user_profile_test.py
```

真实数据库测试仅接受 `PASSPORT_TEST_DATABASE_URL` 指向本机名为 `passport_identity_test` 的一次性 PostgreSQL；未提供时跳过。测试只创建并清理随机测试 schema，不应提供现有业务数据库连接。

发布前仍需真机验证公众号网页授权资格与域名、小程序手机号能力、SMS、两个环境的 HTTPS/CORS 和应用端兑换。关闭身份/扫码响应缓存，对 OAuth 查询参数、Authorization、微信 code、票据、短信和 Token 做日志脱敏。内置协议仍为草案，运营资料与审阅不可由技术测试替代。
