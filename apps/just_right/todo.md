
中文名： 恰好
英文名： JustRight
Slogan： 晚饭吃啥不重要，恰好是你最重要。
    * 含义： 针对解决“周末吃什么”这种日常小烦恼，传达出一种轻松、自在的恋爱状态。

风格： 温馨日常风 (适合奶油色系、带有可爱插画的 UI)

模块一：清单与备忘 (TODO & Memos)
🖥 后端 TODO (API & DB)
 数据库表设计：创建 todo_items 表（字段：id, content, status, creator_uid, couple_id, created_at）。
 数据库表设计：创建 memos 表（字段：id, content, image_urls(JSON/逗号分隔), creator_uid, couple_id, created_at）。
 API：GET /api/todos 获取情侣TODO列表（支持按 status 过滤）。
 API：POST /api/todos 创建TODO。
 API：PUT /api/todos/:id/status 更新TODO完成状态。
 API：DELETE /api/todos/:id 删除TODO。
 API：GET /api/memos 获取备忘录列表（支持分页）。
 API：POST /api/memos 创建备忘录。
 API：POST /api/upload 实现通用图片上传接口（返回OSS图片URL，或本地路径）。
📱 前端 TODO (UI & Logic)
 UI/页面：创建「清单」Tab页，包含顶部双Tab切换（TODO / 备忘录）。
 组件：TODO 列表渲染，点击 Checkbox 切换状态。
 交互：TODO 勾选完成时，添加前端撒花动效（推荐用 canvas-confetti 库）和震动反馈（navigator.vibrate）。
 UI/页面：备忘录瀑布流/列表渲染。
 组件：发布/编辑弹窗，集成图片上传组件调用 /api/upload。
模块二：“Ta的说明书” (User Profile/Manual)
🖥 后端 TODO (API & DB)
 数据库表设计：创建 user_manuals 表 或在现有 User 表扩展（字段：uid, shoe_size, clothes_size, ring_size, diet_preferences, emotional_guide）。建议存为 JSON 字段方便扩展。
 API：GET /api/manual 获取双方的说明书数据。
 API：PUT /api/manual 更新自己的说明书数据。
📱 前端 TODO (UI & Logic)
 UI/页面：创建「说明书」Tab页，左右滑动或点击头像切换查看“我”和“Ta”的说明书。
 组件：分块展示（尺码档案、饮食偏好、情绪指南）。
 交互：为自己的说明书提供编辑模式（表单提交），Ta的说明书仅展示。
模块三：日常决策与礼物池 (Roulette & Wishlist)
🖥 后端 TODO (API & DB)
 数据库表设计：创建 roulette_options 表（字段：id, title, category(吃啥/去哪), couple_id）。
 数据库表设计：创建 wishlist 表（字段：id, title, url, price, image_url, status(未认领/已认领), claimer_uid, couple_id）。
 API：转盘选项 CRUD (GET, POST, DELETE /api/roulette)。
 API：心愿单 CRUD (GET, POST, DELETE /api/wishlist)。
 API：PUT /api/wishlist/:id/claim 更新心愿单认领状态（只能由非创建者调用）。
📱 前端 TODO (UI & Logic)
 UI/页面：心愿单列表渲染，支持点击直接跳转外部链接。
 交互：心愿单认领按钮（点击后状态变为“Ta已暗中准备”，给自己看是隐藏状态，保持惊喜）。
 UI/组件：大转盘 UI。可以使用 CSS 动画或第三方抽奖转盘库（如 lucky-canvas）。
 交互：点击抽奖 -> 请求转盘选项数据 -> 随机生成中奖 index -> 转盘旋转动画 -> 弹出最终结果。
模块四：纪念日与首页互动 (Home & Interactions)
🖥 后端 TODO (API & DB)
 数据库表设计：创建 anniversaries 表（字段：id, title, target_date, is_lunar(是否农历), couple_id）。
 数据库表设计：创建 couple_states 表 或复用缓存（用于记录高频更新的首页状态：心情、留言、白旗，字段：uid, couple_id, mood, note_content, white_flag_status, updated_at）。
 API：纪念日 CRUD (GET, POST, DELETE /api/anniversaries)。
 API：GET /api/couple/state 获取首页整合数据（留言板、双方心情状态、白旗状态）。
 API：PUT /api/couple/state 更新当前的留言/心情/白旗状态。
📱 前端 TODO (UI & Logic)
 UI/页面：首页 (Home) 布局，分为几个卡片区域。
 逻辑：引入 dayjs，计算纪念日的正计时（在一起多少天）和倒计时（距离生日多少天），在顶部卡片展示。
 组件 - 冰箱贴：渲染一个便签UI，点击可直接修改并失焦保存（调用更新状态API）。
 组件 - 状态预警：根据 API 返回的 Ta 的 mood 字段，展示对应的高亮表情（如 🩸 生理期、 😡 生气、 😭 压力大）。
 组件 - 求和按钮：页面角落放一个“举白旗”小图标。
 交互：点击白旗 -> 调接口更新状态 -> 对方刷新页面时弹出全局动画（如一只小狗送花的 GIF）。