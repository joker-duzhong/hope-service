# AuraKey 三端统一身份接入

本次涉及 `hope-service`、`fluffySwap` 和 `AuraKey` 三个仓库。前端会淘汰旧登录缓存，使用已验证手机号的统一用户 ID 加载资料、作品、余额和会员权益。

后端、小程序、Web、管理后台及 `www` 修改均已写入对应仓库。管理员登录仅保留扫码和手机验证码两种方式，默认展示扫码入口。完整文件清单见 [修订文件清单](aurakey-revision-files.md)。

## 登录契约

| 使用端 | 登录方式 | 成功后的范围 |
| --- | --- | --- |
| 微信小程序 | `/auth/identity/miniapp`，需要时通过微信手机号或短信完成第二阶段 | 由后端 AppID 映射为 `hope_aurakey` |
| Web PC | 手机验证码或 Passport 扫码 | `hope_aurakey` |
| AuraKey 管理后台 | 手机验证码或 Passport 扫码 | `hope_aurakey`，并通过 `/aurakey/admin/session` 权限校验 |

扫码登录仍需要手机端明确确认。二维码只放公开的 `transaction_id`，本地开发附加现有 `env=local` 环境标记；`poll_token` 和兑换凭据留在发起端。普通用户扫码成功不会获得管理员权限。

管理员密码登录已完全停用。`POST /api/v1/auth/login` 固定返回 410，客户端应使用上述两个入口；不能通过补传用户名、密码或 `app_key` 恢复旧登录方式。

`PHONE_REQUIRED` 仅表示微信身份已验证，尚无正式会话。临时票据仅存于小程序内存；同意协议和手机号证明完成后才能保存 Access/Refresh Token。Refresh Token 为一次性轮换，并发请求应共用一次刷新，失败后重新登录。

## 部署配置

- `hope-service` 的 `WECHAT_APPS` 继续提供微信凭据；`MINIAPP_APP_SCOPES` 必须将实际小程序 AppID 映射到 `hope_aurakey`。二者用途不同。
- Web 和后台的统一 API 配置为 `VITE_IDENTITY_API_BASE_URL`，包含 `/api/v1`；Passport 配置为 `VITE_PASSPORT_URL`，包含 `/passport`。这些是公开地址配置，禁止放入服务端密钥。
- `www` 继续存储艺术家、分类及风格内容。内容管理请求必须携带管理 Token，服务端通过显式 `IDENTITY_API_BASE_URL` 校验 `/aurakey/admin/session`；缺少配置时拒绝写入。该值只允许 `http://localhost:8000/api/v1` 或 `https://api.lxyy.fun/api/v1`。旧 `/api/auth/*` 返回 410。
- 管理员需要 `scope=hope_aurakey`、`code=aurakey_admin` 的有效角色，或为已绑定手机号的超级管理员。`admin_web` 和 `passport` Token 不能直接用于 AuraKey 业务接口。
- 未绑定手机号的历史超级管理员应先在统一账号管理流程中为原账号完成手机号绑定。不要另建同手机号账号后假定它会自动继承管理员权限；旧 `www` 邮箱账号也不会自动迁移或合并。
- 先更新后端认证与业务接口，再协调发布小程序、Web、后台及 `www`。现有身份表和 Redis 必须可用，前端仍需通过真实微信环境验证手机号能力和扫码域名。

## 用户资料和业务数据

- 登录响应和 `/auth/me` 使用 `user.id`（UUID），头像是资源对象，显示 `avatar.url`。
- `/aurakey/user/profile` 保留 `user_id`、头像 URL、余额和会员字段；`openid` 已废弃并返回 `null`。
- 微信支付创建订单只提交 `product_id`，由服务端确定已验证的微信支付身份。客户端不能因 `openid` 为空而阻止下单。
- 昵称或头像更新后，不应以基础用户响应覆盖余额、会员等业务数据。
- 退出、登录失效和切换账号需清理用户作品、任务及本地用户状态；迟到的请求不得回写另一会话。
- 原内容服务中的艺术家、分类和风格资料继续保留；本次不执行旧用户数据库合并或历史资产搬迁。

更完整的参数和状态恢复规则见 [两阶段身份登录](identity-login-api.md)、[扫码登录](scan-login-api.md) 和 [AuraKey API](../apps/aurakey/api.md)。
