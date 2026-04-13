# 📝 【在问高考】项目全景开发记录与 TODO LIST (V1.0)

## 🗄️ 后端核心系统与 API (Backend)
**架构目标**：高并发防覆盖、底层数据严密溯源、业务与账户体系完全解耦、大模型异步非阻塞执行。

### 🛡️ 模块一：核心架构与数据合规 (Core & Compliance)
- [x] **马甲表与用户基建解耦**：
  - 创建业务专属马甲表 `community_personas` (字段：`id`, `core_user_id`, `nickname`, `avatar_url`, `status_emoji`)。
  - 所有社区内的帖子、投票外键只关联 `persona_id`，实现业务系统与主站账户系统的物理剥离。
- [x] **高并发防覆盖机制 (竞态条件处理)**：
  - 弃用 Python 内存级的累加 (`count += 1`)。
  - 针对点赞、抱抱、投票等高频操作，全面改用 SQLAlchemy 底层原子更新 (`UPDATE ... SET count = count + 1`)。
- [x] **一键销毁软隔离 (Wipe 溯源防线)**：
  - 树洞软删：增加 `TreeholePost.is_deleted`，执行销毁时置为 `True`，接口不再返回，但数据库留存溯源。
  - 红黑榜脱敏：增加 `BoardPost.is_wiped`，执行销毁时置为 `True`。接口照常返回该帖（保留公共避坑价值），但强行拦截并覆盖作者信息为“已抹除痕迹的旅行者”，切断与前台用户的关联。

### 🌳 模块二：双面树洞 (Treehole Area)
- [x] **发帖接口 (`POST /treehole/post`)**：
  - 极速响应：接收入参（Emo/Help类型），存库后 `<200ms` 内返回 200 OK。
  - 异步投递：通过 Celery/消息队列触发大模型脚本，严禁在主线程阻塞等待 AI。
- [x] **信息流拉取 (`GET /treehole/feed`)**：
  - 支持基于游标 (`Cursor`) 的无限分页。
  - **字段聚合**：返回时自动嵌套发帖人当时的马甲状态 (`author: {nickname, avatar, emoji}`)，并过滤掉 `is_deleted=True` 的帖子。
- [x] **抱抱原子操作 (`POST /treehole/hug`)**：基于唯一索引或 Redis 锁防刷。
- [x] **自主删帖 (`DELETE /treehole/post/{id}`)**：鉴权发帖人，执行软删除。
- [x] **AI 引擎：动态回复生成任务 (Celery Task)**：
  - 根据帖子类型注入对应的 System Prompt（Emo贴主打同理心安慰，Help贴主打客观过来人建议）。
  - 大模型返回后，更新 `ai_reply` 内容并将 `has_ai_reply` 置为 `True`。

### ⚖️ 模块三：志愿逻辑红黑榜 (Board Area)
- [x] **结构化发布 (`POST /board/post`)**：保存学校、专业及核心评价。
- [x] **红黑榜信息流 (`GET /board/feed`)**：支持按学校搜索，按热度(总票数)排序分页。
- [x] **红黑榜详情 (`GET /board/{post_id}`)**：返回红绿票数、AI 总结及短评列表。
- [x] **互斥投票 (`POST /board/vote`)**：
  - 强校验：基于 `unique(persona_id, post_id)` 确保一人一帖只能投一票（可改票，不可刷票）。
- [x] **AI 引擎：自动化财报生成 (Trigger Job)**：
  - 监听投票动作，当某帖 `vote_count == 5` 且 `has_ai_summary == False` 时触发。
  - 聚合 5 条红绿短评，调用 LLM 生成 80 字极简中立的“志愿数据审计报告”。

### 🥷 模块四：赛博保险箱与个人中心 (Profile Area)
- [x] **个人面板数据 (`GET /profile/me`)**：聚合返回当前马甲、收到/送出的抱抱总数。
- [x] **随机马甲生成器 (`POST /profile/randomize`)**：基于预设词典（如“焦虑的+修狗”）和随机色块/Emoji生成新身份。
- [x] **隐私与状态偏好 (`PUT /profile/settings`)**：更新用户的状态 Emoji 及隐私设定。
- [x] **夏日存档抓取**：
  - `GET /profile/my-treeholes` (我的树洞发布历史)
  - `GET /profile/my-audits` (我的红黑榜质询/投票历史)
- [x] **抹除痕迹执行器 (`DELETE /profile/wipe`)**：执行模块一中的软删除与脱敏逻辑，最后强制重置该用户的 `community_personas` 为新面孔。