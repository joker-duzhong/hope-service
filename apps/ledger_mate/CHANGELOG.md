# 账伴后端修订记录

## 2026-09-27 全局分类模板管理

- 新增账伴全局分类模板模型和 Alembic 迁移，默认收入/支出分类由模板初始化；用户分类首次访问时同步模板的名称、启停、排序和图标，保留既有分类 UUID 及历史账单引用。
- 新增 `/ledger-mate/admin/categories` 管理端 CRUD，使用 `hope_ledger_mate/ledger_mate_admin` 角色白名单；分类图标字段扩展到 500 字符，可保存七牛等对象存储 URL。
- 新增分类模板同步与 CRUD 隔离测试；账伴测试集 41 项通过。

## 2026-09-26 AI 调用错误诊断

- AI 发送失败的 `message` 区分配置、超时、连接、上游 HTTP 错误、无效 JSON、异常响应和空回复；HTTP 错误附带上游状态码及脱敏后的说明，仍返回 `code=502`、`data=null`。
- 接入共享的 `ChatGenerationError`，日志关联会话 UUID 和安全诊断；原始用户输入及待澄清历史加入脱敏范围，未知异常仅记录类型，不向客户端透传原始异常。
- 扩充服务和真实 HTTP 异常响应测试，验证失败时消息、账单、操作日志与引用无记录，同一消息编号可重试成功。账伴独立测试 39 项通过，共享聊天调用测试 31 项通过；未请求真实模型。
- 本次模块文件：`services.py`、`tests/test_ai_chat.py`、`tests/test_http_contract.py`、`CHANGELOG.md`。共享文件及验证命令见仓库根目录 `CHANGELOG.md`。

## 2026-09-26 Chat Completions 消息格式

- 按 OneAPI Chat Completions 文档发送 `system` 与 `user` 两条消息，用户原文使用 `user.content` 传递，保持 JSON 输出和现有幂等事务逻辑。
- HTTP 契约测试补充消息角色与用户内容断言。

## 2026-09-26 JSON 输出模式兼容

- 账伴 user 消息明确要求返回合法 JSON，以满足 OneAPI JSON mode 的输入校验；该规则仅属于账伴调用层，不加入通用 LLM 服务。

## 2026-09-27 账单日期排序

- `/records` 列表按 `occurred_date` 倒序返回；同一发生日期继续按发生时间、创建时间和 ID 稳定排序。
- 补充日期优先及编辑日期后的排序回归测试，无需数据库迁移。

## 2026-09-26

- 账单新增、编辑和响应支持 `occurred_date`（YYYY-MM-DD）；新日期按上海午夜保存，旧 `occurred_at` 请求与历史记录继续兼容。
- 账单列表新增 `start_date` / `end_date`，结束日期不包含；排序改为 `created_at DESC, id DESC`。
- AI 发送要求稳定的 `client_message_id`。通过用户会话行锁与消息持久化实现重试幂等，消息、账单、操作日志及关联在同一事务提交。
- AI 仅解析尚未入账的澄清上下文；缺少金额等关键字段时返回问题，整组不入账；分类、支付方式必须属于当前用户且有效。
- 消息历史返回最近指定条数并按时间正序展示；关联账单读取最新编辑结果，删除后不再返回或因重试重建。
- 统计补充收入分类、收支笔数、分类笔数与每日结余，统一使用上海日期；保留旧时间参数和字段。
- 旧 AI 确认接口改为整组原子提交，并兼容原幂等键；手动账单补充支付方式权限校验和请求锁。
- 新增 28 条隔离测试：内存数据库、真实路由与响应序列化、日期/排序/统计、AI 澄清/幂等/回滚/权限，以及 PostgreSQL 锁语句验证。不访问真实数据库、真实模型或 `.env`。

测试命令（服务仓库根目录）：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -m pytest -p no:cacheprovider apps/ledger_mate/tests -q
```

测试额外依赖列于 `tests/requirements.txt`。数据库结构未变更，不需要迁移。真实 PostgreSQL 并发锁与模型供应商端到端连接需在已配置的测试环境验收。

本轮修改文件：

- `prompts.py`
- `router.py`
- `schemas.py`
- `services.py`
- `dates.py`
- `CHANGELOG.md`
- `tests/conftest.py`
- `tests/test_dates_and_records.py`
- `tests/test_ai_chat.py`
- `tests/test_http_contract.py`
- `tests/requirements.txt`
- `tests/README.md`
- `tests/.gitignore`：在本测试目录取消根规则对 `test_*.py` 的忽略，确保回归测试可纳入版本控制。
