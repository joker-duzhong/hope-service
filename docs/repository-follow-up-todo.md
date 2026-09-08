# 后续待办清单

最后审查日期：2026-09-08

本清单记录平台 Common 层改造后已经确认、但尚未处理的工作；已完成事项不再重复列出。

## P0 - 生产部署前必须完成

- [ ] **验证号码认证短信配置**：使用 Dypnsapi 可用签名和模板，为注册、绑定配置不同验证服务名称；部署环境移除废弃的腾讯云短信变量。预发布验证发送、错误码、过期和用途隔离，禁止在日志中输出凭据或验证码。
- [ ] **停止旧资源删除进程**：公共资源已改为仅软删除。切换前停止旧 Worker，再启动新 Worker 安全消费旧删除消息；不执行 OSS 物理清理，不清空其他业务队列。
- [ ] **执行并验证数据库迁移**：先备份数据库，执行 `alembic upgrade head`，确认 `core_users.token_version` 与 `core_user_identities` 已创建，且外键和唯一约束符合预期。本工作区尚未连接真实数据库执行该迁移。
- [ ] **核对历史微信账户**：旧 `core_users.openid` 不含 AppID 维度。需要建设运营侧映射/导入流程，只将已验证的映射回填至 `core_user_identities`，并输出未匹配记录。禁止猜测 AppID；在此之前，历史微信用户通过新身份流程登录可能会获得新的平台账户。
- [ ] **配置生产密钥与来源白名单**：设置至少 32 字节的 `SECRET_KEY`、真实的 `WECHAT_APPS`、支付凭据、OSS 凭据和明确的 `BACKEND_CORS_ORIGINS` 白名单。启用凭据时不得以 `*` 作为生产 CORS 默认值部署。
- [ ] **微信支付回调必须拒绝未验签请求**：`WechatPayNotificationHandler.verify_signature()` 目前在 `DEBUG=True` 且未配置平台证书时会接受回调。删除该绕过逻辑，测试改用 mock 验签。
- [ ] **资产类操作强制完成手机验证**：已确定的产品策略要求用户在创建资产或消耗价值前完成手机验证。增加共享依赖，并应用于 AuraKey 生图、支付及其它付费/用户资产接口。

## P1 - 正确性与 Common 层加固

- [ ] **完成外部身份迁移收尾**：将管理端搜索、通知和业务特有查询中遗留的 `User.openid` 读取，改为按目标 AppID 查询 `UserIdentity`。只有在数据对账完成并经过回滚窗口后，才删除未使用的 `get_by_openid()` 与 `get_by_unionid()`。
- [ ] **定义并实现显式账户归并**：仅在产品需求启用后实施。必须验证用户对两个账户的控制权并选择保留账户；只迁移外部登录身份，业务资产保留在选定账户，停用另一账户，并记录不可变审计日志。
- [ ] **部署时绑定产品与 WeChat AppID**：从部署配置为每个活跃 `AppConfig.wechat_appids` 填入映射，并在 OAuth、小程序登录、扫码登录、支付和通知流程中强制校验。真实 AppID 不写入仓库。
- [ ] **校验直传文件声明**：`/storage/confirm-upload` 仍信任客户端提交的 OSS key、MIME 类型、大小和 hash。应使用每用户的签名上传策略/key 前缀，并在创建资源记录前向 OSS 校验实际对象。
- [ ] **完成按 App 的角色校验**：`core.dependencies.require_app_roles()` 目前没有过滤停用角色，和 `core.users.dependencies` 的行为不一致。需要统一行为，并明确时间图书馆等遗留角色 scope 的映射。
- [ ] **所有安全变更撤销既有会话**：改密码、绑定/解绑手机、解绑身份和未来账户归并必须递增 `token_version`。在公开相应功能前实现其接口与流程。

## P2 - 可靠性、可测试性与维护

- [ ] **完成 Common Celery 控制面**：将 Beat 的 `app_key` 放入每个调度项，移除并行的 `BEAT_APP_KEYS` 映射，并增加按 App 感知的统一任务投递器。在各业务任务具备幂等性/补偿设计前，不修改业务任务的重试语义。
- [ ] **补充带数据库的 API 契约测试**：已有短信、严格一次性令牌轮换（无重试窗口）、参数校验异常和资源软删除隔离回归测试；仍需真实 PostgreSQL/Redis 下的注册、登录、AppID 维度身份、二维码兑换和支付集成测试。
- [ ] **在 CI 提供集成测试基础设施**：为全量测试启动 PostgreSQL 和 Redis 服务。当前本地全量测试中 3 个 ShadowBoard 失败仅因 PostgreSQL 不可用；本工作区未安装 Docker。
- [ ] **支付下单请求幂等**：防止用户双击或重试为同一用户、同一商品创建多个 `waiting` 订单。使用幂等键或复用最近有效待支付订单，并定义过期/关闭策略。
- [ ] **迁移 Pydantic v2 已弃用的 `Config` 类声明**：在 Pydantic v3 移除兼容能力前，将 schema 和配置迁移为 `ConfigDict` / `model_config`。
- [ ] **对齐历史文档**：更新 `platform-common-refactor-plan.md` 和 SDK 说明中仍写“下线 App 返回 `410`”的内容；当前确认的实现是不注册下线路由并返回框架标准 `404`。




## 推荐发布顺序

- 备份数据库，记录当前镜像与 alembic current，准备可回滚镜像。
- 先补齐生产环境变量：ENVIRONMENT=production、DEBUG=false、至少 32 字节 SECRET_KEY、Redis、阿里云短信、真实  WECHAT_APPS、支付私钥/平台证书、OSS、CORS 白名单。
- 在预发布库执行 alembic upgrade head，验证 core_users.token_version 与 core_user_identities。
- 完成历史微信 (AppID, OpenID) -> UserIdentity 对账和回填；无法确认 AppID 的记录不要猜测。
- 用预发布环境验证：密码登录、微信登录、短信、刷新令牌、AuraKey 下单、支付回调、资源读取，以及三个仍启用业务的前端。
- 先发布数据库迁移，再发布 API 和 Celery Worker/Beat；发布窗口明确通知用户需重新登录。
- 发布后重点监控 401/403/404、短信发送失败、Redis 错误、支付 failed 订单与新建微信账户数量。



可以用 `docker compose up -d --build`，但**不能把它当作这次改动的完整部署流程**。

它会重建应用、Worker、Beat 容器，且现有 `postgres_data`、`redis_data` 命名卷会保留，不会清库；但它**不会自动执行 Alembic 迁移**。新代码依赖 `0019_core_user_identities`，直接启动会导致认证相关接口访问缺列/缺表。

这次建议按以下顺序上线：

```powershell
# 1. 先备份线上 PostgreSQL 数据库

# 2. 停止应用和后台消费；PostgreSQL、Redis 保持运行
docker compose stop app celery-worker celery-beat

# 3. 构建新镜像
docker compose build

# 4. 执行迁移
docker compose run --rm app python -m alembic upgrade head

# 5. 确认迁移版本
docker compose run --rm app python -m alembic current

# 6. 启动全部新服务
docker compose up -d
```

上线前必须确认：
- `.env` 配好阿里云号码认证 Dypnsapi 签名、模板和两个独立验证服务名称，移除已废弃的腾讯云短信变量。
- `ENVIRONMENT=production`、`DEBUG=false`，并配置足够强的 `SECRET_KEY`。
- `WECHAT_APPS`、支付私钥、微信支付平台证书、Redis、数据库和 CORS 白名单都是真实生产值。
- 历史微信用户的 OpenID/AppID 映射已处理，否则老用户重新微信登录可能创建新账户。
- 预期所有用户重新登录，因为旧 JWT 与 refresh token 不兼容。

对已下线 App 还多一步：停掉旧 Worker 前，要确认 Redis 中没有其排队任务。新 Worker 不会注册其任务；遗留消息会执行失败。不能直接无差别 `celery purge`，那会清空所有 App 的待执行任务。应先让旧 Worker 尽量处理完在途任务，或按任务名有针对性地撤销。

另外，`docker-compose.yml` 中 `app` 使用了 `uvicorn --reload`，线上建议改为不带 `--reload`；否则会增加文件监听和重载行为，不适合作为生产进程配置。
