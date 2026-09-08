# 通用扫码登录接口

后端仅负责临时登录事务、手机授权确认和一次性凭据兑换，不生成二维码图片、不配置 H5 地址、不处理跳转。本流程不依赖公众号扫码/关注事件；手机可以通过已有手机号、公众号或小程序登录获取用户凭据。

## 基本约定

- 接口前缀：/api/v1/auth/scan；响应使用项目统一的 code、message、data 结构。
- app_key 必填，是业务标识，不是微信 AppID。先查询 apps 列表；仅允许已启用应用。客户端不能通过 app_key 获得该应用的角色或资源权限。
- 应用列表包含已启用的 admin_web；后台应用确认和兑换时都要求超级管理员。所有应用确认和兑换均要求账号正常且已绑定手机号。
- 默认有效期 300 秒，从创建时开始计算；已扫码、重复确认和轮询均不延长。PC 按 poll_interval_seconds（当前为 2 秒）轮询。
- transaction_id 是公开事务标识，可以放进二维码；poll_token 是发起端秘密，只在创建时返回，必须留在 PC，不能放进 URL、二维码或日志。后端仅保存其 SHA-256 摘要。
- exchange_code 是短期一次性兑换码，不是 Access Token。它仅通过携带正确 X-Scan-Token 的轮询接口返回，不向手机 info/scanned/confirm 接口返回。
- 所有业务跳转和二维码 URL 均由前端决定；创建请求只允许 app_key，额外参数会返回 422。
- 成功响应带 Cache-Control: no-store；请使用 HTTPS，关闭代理层对这些接口的缓存，并对 X-Scan-Token、Authorization 及请求/响应中的凭证字段做日志脱敏。

## 接口清单

| 方法 | 路径（相对此前缀） | 调用方 | 认证要求 |
| --- | --- | --- | --- |
| GET | /apps | PC / H5 | 无需登录 |
| POST | /sessions | PC | 无需登录 |
| GET | /sessions/{transaction_id} | PC | X-Scan-Token |
| GET | /sessions/{transaction_id}/info | H5 | 无需登录 |
| POST | /sessions/{transaction_id}/scanned | H5 | 无需登录，仅更新展示状态 |
| POST | /sessions/{transaction_id}/confirm | H5 | Authorization: Bearer 手机端 access_token |
| POST | /sessions/{transaction_id}/cancel | H5 | Authorization: Bearer 手机端 access_token |
| POST | /exchange | PC | X-Scan-Token |

confirm/cancel 不需要请求体；授权用户从有效的 Bearer Token 读取，而不是从客户端提交的 user_id、手机号、OpenID 或 app_key 读取。

## 1. 查询应用并创建二维码事务

GET /api/v1/auth/scan/apps 的 data 是列表，仅包含 app_key 和 name，不返回内部模块路径、微信 AppID 或凭据。应用下线后不再出现在列表中；等待中的会话也不能再授权或兑换。

PC 选取一个 app_key 后发起 POST /api/v1/auth/scan/sessions，例如：

    {"app_key": "hope_aurakey"}

成功 data 包含：

| 字段 | 说明 |
| --- | --- |
| transaction_id | 本次登录临时 UUID |
| status | 初始值 WAITING_SCAN |
| app | 后端读取的 app_key、name |
| expires_at | UTC 绝对过期时间 |
| poll_token | PC 保存的随机秘密，后续放入 X-Scan-Token 请求头 |
| poll_interval_seconds | 建议轮询间隔，当前 2 秒 |

前端把 transaction_id 拼入自己配置的 H5 地址并生成二维码。每个二维码分别保留自己的 poll_token；同一 PC 可以有多个独立事务，不能共享或串用凭证。页面刷新丢失 poll_token 时创建新二维码。

## 2. 手机通知已扫码

H5 从 URL 取得 transaction_id，调用 info 获取真实应用名称和状态，随后调用 scanned。

- WAITING_SCAN 转为 PENDING，PC 下一次轮询即可遮罩二维码。
- 已经是 PENDING 或更后面的状态时返回当前状态，不回退、不重新生成凭证。
- 这一步只表示“客户端打开页面并通知后端”，不能证明用户身份或同意授权；知道二维码 URL 的人也可能调用它。
- info 和 scanned 只返回 transaction_id、status、app、expires_at，绝不返回账号信息、poll_token 或 exchange_code。

## 3. 手机登录、绑定并确认

手机调用现有登录接口获得自身 access_token；用户信息 needs_phone_binding=true 时，先调用现有手机号绑定接口。已有手机号属于另一账号时仍遵循原来的冲突规则，不自动合并。

H5 明确展示“确认在另一设备登录某应用”，应用名使用 info 返回的 name，不信任 URL 中的文字。用户主动确认后调用 confirm，并携带自己的 Bearer Token。

- 只有 PENDING 可以进入 CONFIRMED。
- 未登录、账号停用/注销、未绑定手机号或无后台权限时不允许确认。
- 同一用户重复确认已经 CONFIRMED 的事务返回当前状态，不换兑换码、不延长有效期。
- 其他用户不能覆盖已经确认的账号；账号 token_version 改变后需要重新扫码。
- 手机端只收到状态，不会收到 PC 的登录凭据。
- 用户拒绝时调用 cancel；仅 WAITING_SCAN/PENDING 可以取消，重复取消返回 CANCELLED。已确认后不能取消或改绑授权账号。

## 4. PC 轮询并兑换

PC 调用 GET /sessions/{transaction_id}，必须携带：

    X-Scan-Token: <创建时返回的 poll_token>

非 CONFIRMED 状态的 exchange_code 为 null。CONFIRMED 时返回相同的有效 exchange_code，重复轮询不消费兑换码。PC 停止轮询并调用 POST /exchange：

    {
      "transaction_id": "<本次临时 UUID>",
      "exchange_code": "<轮询返回的兑换码>"
    }

兑换请求仍携带 X-Scan-Token。不需要手机端 Token，也不要把手机 Token 复制给 PC。

成功 data 与手机号/微信登录一致：access_token、refresh_token、token_type、user；user 含 phone、needs_phone_binding。PC 使用这些新凭据建立自己的登录状态，并自行跳转。

- 后端重新检查应用、账号状态、手机号、后台权限和 token_version。
- Redis 原子地把 CONFIRMED 改为 CONSUMED，并清除兑换码；并发兑换最多一个成功。
- 新 Token 仍为平台级身份凭据，不是 app_key 授予的业务权限；业务接口继续执行现有角色和资源归属校验。
- 如果已消费后签发失败，或成功响应在网络中丢失，必须重新扫码。不回滚到 CONFIRMED，不提供幂等兑换宽限窗口。

## 状态及错误处理

主流程：WAITING_SCAN → PENDING → CONFIRMED → CONSUMED。

| 状态 | PC 展示建议 |
| --- | --- |
| WAITING_SCAN | 显示二维码，等待扫码 |
| PENDING | 遮罩二维码，提示在手机完成登录/绑定/确认 |
| CONFIRMED | 停止轮询，兑换登录信息 |
| CONSUMED | 已兑换，不再尝试兑换 |
| CANCELLED | 提示取消，重新生成二维码 |
| EXPIRED | 提示过期，重新生成二维码 |

未兑换状态均受最初的 300 秒截止时间约束。CONSUMED/CANCELLED 保留到原有效期结束便于轮询；记录过期或临时 ID 不存在时，info/poll 返回 EXPIRED（app 和 expires_at 可为 null），变更和兑换接口返回 410。Redis 故障返回 503，不伪装为过期。

| HTTP 状态 | 含义 / 前端处理 |
| --- | --- |
| 400 | 应用不存在或已停用 |
| 401 / 403 | 未登录、账号/权限/手机号不满足要求，或发起端凭证/兑换码不匹配 |
| 409 | 状态冲突、已被其他账号确认或已消费；重新查询，必要时重建二维码 |
| 410 | 临时会话已过期；重建二维码 |
| 422 | 参数、UUID 或请求头格式错误 |
| 429 | 触发限流；遵循 Retry-After，不能紧密重试 |
| 503 | 服务或 Redis 异常；不要绕过确认或兑换校验 |

当前限流为每个来源 IP 每分钟 180 次扫码接口请求、每个来源 IP 每分钟 10 次创建，以及每个合法事务每分钟 40 次轮询；采用首次请求起的 60 秒固定窗口。IP 取 Request.client，不在业务代码中自行信任 X-Forwarded-For。反向代理部署须限制可信代理，避免所有用户被计入同一个代理 IP 或接受伪造来源；接口响应凭证也不得写入代理日志。

## 部署与验证

- 不新增数据库表或迁移；仍依赖现有用户/身份表和 Redis。
- 不新增阿里云或微信密钥。旧公众号扫码事件只使用 wechat_scan 命名空间，本流程使用 auth:scan，不互相授权。
- 前端跨域请求需要现有 CORS 白名单正确配置，并允许 Authorization、X-Scan-Token、Content-Type；本次不放宽 CORS 设置。
- 本次仅完成后端接口，H5 页面、二维码绘制、遮罩和跳转需前端实现。手机端必须保留用户主动确认步骤，不能扫码即自动确认登录。
- 使用隔离测试（FakeRedis、模拟账号及 ASGI 客户端），无需真实扫码或短信：

    .\.venv\Scripts\python.exe -m pytest --noconftest -q tests/test_scan_login.py
