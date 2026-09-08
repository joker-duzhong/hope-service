# 平台 Common 改造方案

## 范围与目标

本次只处理跨 App 的平台能力：账户、登录、JWT、App 注册、路由挂载、角色范围和 Celery。七牛存储、CORS、Alembic 自动迁移与运行环境依赖问题暂不纳入。

目标是让 `core_users` 成为唯一的平台主体，所有登录方式都映射到同一个用户；业务 App 只消费用户 ID 与固定的 App 上下文，不再自行识别微信用户或信任前端 `app` Header。

## 1. App 注册表成为唯一入口

### 改动

扩展 `AppConfig`，每个业务 App 必填维护：

- `key`：稳定的业务 scope，例如 `hope_aurakey`。
- `api_prefix`：对外 API 前缀，例如 `/aurakey`。
- `router_modules`：路由模块及 tags；支持一个 App 挂多个 router，例如时间图书馆的用户端和管理端。
- `wechat_appids`：该 App 可使用的公众号/小程序 AppID。
- `is_active`：App 的唯一生命周期开关；同时决定业务 API、任务模块导入和 Beat 调度是否启用。
- `task_modules`：需由 Celery 导入的任务模块。

将目前在 `main.py` 手工挂载的每一个业务 router 补齐到 `REGISTERED_APPS`：Trade Copilot、Just Right、Ledger Mate、Nest Talk、Time Library、AI Gateway、Zaiwen Gaokao、Project Sisyphus、Shadow Board、Typo Craft、AuraKey、Teacher Logbook。`admin_web` 保留为管理 scope，但不把 core 管理路由伪装为独立业务 App。

`main.py` 只直接挂载 `core` 路由；业务路由与 Celery 均遍历同一份注册表派生。启用 App 时动态导入并注册其 `router_modules`；下架 App 时不导入业务 router 或 task 模块，也不加载其 Beat 调度，已登记路径保持框架标准 `404`。禁止在 `main.py` 再出现业务模块的固定 import/include_router。

班主任工作台当前挂在 API 根路径，内部路径为 `/classes/...`，没有独立前缀，不能直接参与通用下架 catch-all。需在实施前二选一：迁移至 `/teacher-logbook` 并发布前端路径变更；或在注册表声明其全部 legacy 路由前缀，仅对这些前缀注册下架响应。禁止为根路径注册 catch-all，否则会拦截其他 core/App 路由。

### 依据

当前路由实际挂载了 12 个业务模块，但 `REGISTERED_APPS` 只维护 6 个，路由、角色 scope 和任务列表已经产生漂移。

### 预期

新增、停用或迁移 App 只改一个注册表；`is_active=False` 时不挂载该 App 的 API、不导入其 task 模块、不加载其 Beat 调度，业务前缀与未知路径均返回 `404`；重新设为 `True` 时三者一并恢复。

## 2. 取消前端 App Header 的授权语义

### 改动

废弃 `get_app_key` 对 `app` Header 的安全依赖，并删除业务 router 上的 Header 依赖。路由注册时由注册表注入不可伪造的固定 `app_key` 上下文；角色校验改为 `require_app_roles(app_key, roles...)`，其 `app_key` 来自后端注册数据而不是请求。所有当前接收或透传 `scope: Depends(get_app_key)` 的 router 与 service，改为从所属 router 的固定上下文取得 scope；不能仅修改注册逻辑。

登录、回调等尚未认证的接口允许传入 App key 作为“选择哪个产品”的参数，但必须校验该 App 已启用且请求的微信 AppID 属于其 `wechat_appids`。这不是权限凭据。

角色查询同时过滤 `Role.is_active=True`。`admin_web` 管理权限使用显式 global/admin scope，不通过可替换 Header 切换。

### 依据

请求者可随意伪造 Header；当前 Header 同时决定资源 scope 与角色 scope，不能构成授权边界。

### 预期

同一平台 token 可跨 App 使用，但只能访问该 URL 所属 App 的权限规则；用户无法通过改 Header 获得其他 App 的角色或数据范围。

## 3. 平台用户与外部身份拆分

### 改动

保留 `core_users` 为平台主体：个人资料、手机号、账号状态、`token_version` 和角色均只归属该表。新建 `core_user_identities`：

- `user_id`：关联 `core_users.id`。
- `provider`：初期为 `wechat`，后续可扩展密码、Apple、OIDC。
- `provider_app_id`：微信 AppID。
- `subject`：该 AppID 下的 OpenID。
- `unionid`：可选的微信开放平台标识，仅作辅助关联线索。
- `verified_at`、`metadata`、审计时间。

数据库唯一约束为 `(provider, provider_app_id, subject)`；不能再让 `core_users.openid` 做全局唯一键。`phone` 仍在 `core_users` 且全局唯一，代表已验证的平台联系方式。

迁移按“新增表 -> 回填旧 OpenID/UnionID -> 双读双写 -> 切换读取 -> 最后删除旧字段”执行。回填时为历史记录标记来源 AppID；无法可靠判定 AppID 的记录进入人工核对队列，不猜测归属。新增表及约束先以 nullable/非阻塞索引上线，分批回填并对账后才加非空约束或删除旧列；迁移必须提供可逆的回退窗口。

所有当前读取 `User.openid` 的跨平台能力必须一并改为按 `user_id + provider_app_id` 查询身份表，包括微信支付下单、公众号客服消息/模板通知、运营检索与展示。支付或通知调用必须传入其目标微信 AppID，不能再从用户表猜测 OpenID。

### 依据

OpenID 天然是 AppID 维度；现有 UnionID 命中逻辑会覆盖旧 OpenID，导致同一用户跨公众号/小程序登录后丢失原身份。

### 预期

一个平台用户可安全绑定多个公众号和小程序身份；同一 OpenID 在不同 AppID 下不会碰撞，也不会覆盖历史身份。

## 4. 统一认证与账户绑定流程

### 改动

抽取 `core.identity`：

1. 各微信适配器只负责用 code 或回调 payload 向微信换取可信 `openid/unionid`。
2. `IdentityService.resolve_wechat_identity()` 用 `(appid, openid)` 查身份表。
3. 已绑定身份直接得到平台用户；未知身份创建待完成绑定的用户身份，不通过昵称、头像或 UnionID 自动合并账户。
4. 用户必须完成已验证手机号绑定后，才能创建资产、下单或使用需实名/账户归属的能力；公共浏览可匿名。
5. 发现已有手机号对应账户时，不自动合并。未来如启用账户合并，必须要求用户明确选择保留账户并完成双方控制权验证；仅迁移外部登录身份、使被合并账户失效，业务资产只保留所选账户，不做跨业务资产迁移，并保留不可篡改审计。

公众号 OAuth、小程序 `code2Session`、小程序手机号、短信注册/绑定和扫码登录都调用同一个身份服务与 `TokenService`。路由层不再直接创建用户、直接修改手机号或直接签发 JWT。

### 依据

目前公众号、小程序和扫码有三套重复登录代码；它们直接调用 `UserService.wechat_login()`，无法表达“外部身份绑定”和“用户账户合并”的边界。

### 预期

所有端共享一个账户体系；身份归并必须由用户证明控制权并选择保留账户，避免误合并和跨业务资产冲突。

## 5. 扫码回调与微信配置安全化

### 改动

微信 AppID 和密钥继续由部署环境的 `WECHAT_APPS` 提供，仓库不保存真实 AppID。`wechat_appids` 是可选的部署期业务映射白名单；在未配置映射前不强制校验，待各业务提供归属关系后再启用双向校验。

所有微信 POST 回调均强制验签：明文消息校验 `signature/timestamp/nonce`；加密消息校验 `msg_signature` 并成功解密。缺少签名、签名失败、未知 AppID 时返回失败，绝不解析事件或创建用户。

扫码流程改为：浏览器创建一次性 `login_transaction` 并绑定 HttpOnly 会话 cookie、目标 App 和过期时间；二维码 scene 仅映射该 transaction。回调只把 transaction 标为“已扫码”；前端轮询只得到状态。浏览器用同一会话兑换一次性 authorization code，后端验证后才签发 token。Redis 不保存 JWT，也不向任意知道 scene ID 的请求返回 JWT。

### 依据

当前明文回调未验签，且扫码状态直接缓存 access token；两者共同构成可伪造登录风险。

### 预期

只有微信有效回调能推进扫码状态，且扫码结果只能被发起该登录的浏览器兑换一次。

## 6. JWT 与会话撤销

### 改动

生产启动校验 `SECRET_KEY`：禁止默认值、空值和弱密钥，要求通过环境变量注入至少 32 字节随机值；固定允许的算法并为密钥轮换预留 `kid`。

统一 token claims：`sub`、`type`、`iss`、`aud=hope-platform`、`iat`、`exp`、`jti`、`token_version`。通过 Redis 会话索引以 `jti` 管理 refresh token；先校验账户，再原子创建新会话并消耗旧会话。旧 refresh token 严格一次性使用，复用返回 401，不设重试窗口；响应丢失时需重新登录。

`get_current_user` 校验用户启用状态与 `token_version`。密码变更、手机号/微信身份解绑、账户合并、禁用用户时递增版本并撤销会话。

### 依据

当前 JWT 使用可预测默认密钥，且只含 `sub/exp/type`；关键安全动作后旧 token 无法即时失效。

### 预期

生产环境不能因漏配密钥启动；账户安全事件后原有登录态可被确定地撤销。

## 7. 恢复 HTTP 错误语义

### 改动

全局 `AppException` 处理器将 HTTP status 从固定的 `200` 改为 `exc.code`；响应 body 保持既有 `{code, message, data}` 结构不变。参数校验、认证失败和框架抛出的 `HTTPException` 同样保持真实 HTTP 状态码，禁止在 router 中捕获后重新包装为成功响应。

为前端 SDK、API 网关和调用方增加兼容检查：业务成功仍是 HTTP `2xx` 且 body `code=200`；业务失败同时为非 `2xx` 和对应 body `code`。仅对必须兼容的历史调用方提供短期版本化接口，不保留全局“失败也 200”的开关。

### 依据

当前全局异常处理器固定返回 HTTP `200`，会让网关、监控、缓存和调用方误判失败为成功，重试和告警失效。

### 预期

HTTP 协议层与业务响应层表达一致；客户端可按标准状态码处理认证、限流、参数与服务端错误，平台监控可准确统计失败率。

## 8. Celery 按 App 注册和可靠失败

### 改动

`worker/celery_app.py` 改为遍历 `REGISTERED_APPS` 的 `task_modules`，仅导入 `is_active=True` 的模块；移除逐个 `try/except ImportError: pass` 的静默导入。导入失败应阻止 worker 启动并输出模块名。路由注册复用同一份 App 配置的 `router_modules`，不再维护第二份 App 清单。

`worker/scheduler.py` 的每项 Beat 配置附带 `app_key`，在启动时仅过滤 `is_active=True` 的 App。请求内投递任务统一通过 `dispatch_app_task(app_key, task_name, ...)`，停用 App 时拒绝投递或明确返回业务错误，避免 `NotRegistered`。将 `is_active` 设为 `False` 前，先停止 Beat、等待或撤销该 App 已入队任务，再部署不导入 task 的 worker；否则队列中旧消息会因任务未注册失败或被其他旧 worker 执行。

为可恢复失败定义统一 `BaseTask`：记录 task/app/request ID，使用有限重试与指数退避；不要捕获异常后返回成功。任务的重复执行由各业务任务使用业务幂等键处理，平台层负责可靠投递、观测和失败告警。

### 依据

当前任务模块依赖硬编码且导入异常被吞掉；部分 task 捕获异常后正常结束，Celery 会错误标记为成功。

### 预期

一个 App 用单一开关同时暂停或恢复 API 和后台任务；下架 App 的既有 API 前缀与未知地址均返回 `404`；任务加载失败、重试耗尽和重复投递都可观测且不会静默丢失。

## 9. 客户端与数据库影响

### 必须同步的前端/调用方改动

- 不再要求或依赖 `app` Header；旧客户端保留发送 Header 不影响请求，但后端不读取它作授权或数据 scope。
- 微信登录、小程序登录和扫码登录请求改传平台 App key，而不是任意微信 AppID；后端根据注册表选择并校验微信 AppID。
- 扫码轮询不再返回 token，需增加“用一次性 code 兑换 token”的调用；这是唯一不可避免的登录流程改动。
- 所有客户端应以 HTTP status 判断成功与否，同时继续读取现有响应 body；下架 App 与未知地址均按 `404` 处理。
- 班主任工作台若迁移至独立 `/teacher-logbook` 前缀，所有 `/classes/...` 调用必须同步升级；若当前无法升级，先保留 legacy mount，不能获得通用的 App 前缀下架能力。

### 数据库验收

- 身份表唯一约束 `(provider, provider_app_id, subject)` 与历史数据无冲突。
- 每条旧 `openid` 记录均已回填、人工待处理或明确废弃，三类数量可对账。
- 每个现有微信通知/支付使用点都能以目标 AppID 查到可用身份；查不到时明确失败并告警，不能使用错误 AppID 的 OpenID。
- 所有历史 JWT 在切换 `token_version` 后按既定兼容窗口失效；发布前通知用户重新登录的范围。

## 10. 实施顺序与兼容策略

1. 先建立注册表字段和动态路由注册，保持现有 API 路径、业务 key 与 Header 兼容；下架响应使用 `410/APP_OFFLINE`。
2. 建立身份表与回填迁移，完成 OpenID 对账，并先改造支付、通知等所有 `User.openid` 消费点；保留旧字段双读双写。
3. 接入统一身份服务，切换公众号 OAuth、小程序和扫码流程；先发布验签与一次性扫码兑换，再移除旧扫码 token 返回。
4. 恢复异常 HTTP 状态码；先完成前端 SDK、网关和监控的兼容验证，再切换全量流量。
5. 上线 JWT 启动校验、会话版本与 refresh 轮换；部署前完成生产密钥配置并安排历史 token 的失效窗口。
6. 将现有 Header scope 参数迁移为后端固定 App scope；完成所有业务回归后删除 `get_app_key` 授权路径。
7. 最后按注册表接管 Celery 导入和 Beat；下架或关闭任务前执行队列排空/撤销流程，再删除旧手工任务清单与用户旧身份字段。

每一步需覆盖：同一人跨两个微信 App 登录、手机号显式合并、回调伪造拒绝、扫码结果不可被其他浏览器兑换、禁用后 token 失效、业务异常返回匹配的 HTTP 状态码、下架 App 的既有前缀返回 `410/APP_OFFLINE`、未知路径返回 `404`、关闭 App 后 Beat/task 均不可用、已入队任务按策略排空或撤销。
