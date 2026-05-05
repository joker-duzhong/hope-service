明白。前端在对接时，最怕的就是“接口通了，但字段少了画不出 UI”。

为了让前后端对接最高效，以下我为你梳理了 **C端核心接口的详细返回参数（Data 层）**。

*(注：默认所有接口最外层都有标准的数据结构，如 `{ "code": 200, "msg": "success", "data": { ... } }`，以下仅列出 `data` 内部必须包含的字段。)*

---

### 1. 发现页 / 画廊模块

#### `GET /api/gallery/list` (获取首页画廊列表 - 允许未登录)
*   **分页信息**：`total` (总条数), `page`, `pageSize`
*   **list** (数组形式，每项包含)：
    *   `id`: 作品 ID
    *   `thumbUrl`: 缩略图地址 (带 OSS 压缩后缀，节省流量)
    *   `aspectRatio`: 宽高比 (如 `16:9`，前端瀑布流排版必备)
    *   `author`: { `userId`, `nickname`, `avatar` }
    *   `likeCount`: 点赞总数
    *   `isLiked`: Boolean，当前用户是否已点赞 (**未登录时默认返回 false**)
    *   `viewCount`: 浏览量

#### `GET /api/gallery/{id}` (获取画廊详情 - 允许未登录)
*   `id`: 作品 ID
*   `imageUrl`: 高清原图地址
*   **`prompt`**: 提示词 (前端展示给用户看，也是“一键同款”的入参)
*   `modelName`: 使用的模型名称 (如 "Pro v1.0")
*   `aspectRatio`: 比例参数
*   `author`: { `userId`, `nickname`, `avatar` }
*   `stats`: { `likeCount`, `viewCount` }
*   `isLiked`: Boolean (**未登录为 false**)
POST /api/gallery/{id}/like (点赞 / 取消点赞)
场景：用户双击图片或点击爱心图标触发。

入参：无（通过 URL path 传作品 ID）
出参 (Data)：
    * isLiked: Boolean (点赞操作后的最终状态)
    * likeCount: 最新点赞总数 (前端直接替换，不用自己加减)
逻辑：如果已点赞则取消，未点赞则增加。
---

### 2. 核心创作与扣费模块

#### `POST /api/task/generate` (提交生图任务)
*前端点击生成后调用，需拦截余额不足情况。*
*   `taskId`: 任务唯一标识号 (前端拿这个去轮询)
*   `frozenPoints`: 本次冻结的算力点数 (前端可做提示)
*   `balanceAfter`: 预扣减后的账户余额

#### `GET /api/task/status/{taskId}` (轮询获取任务状态)
*前端拿到 `taskId` 后每隔 2 秒请求一次此接口。*
*   `taskId`: 任务 ID
*   **`status`**: 当前状态 (`pending`:排队中, `processing`:生成中, `success`:成功, `failed`:失败)
*   `progress`: 进度百分比 `0 - 100` (可选，用于前端展示进度条，若大模型没返回进度则根据历史任务时间反推大致的进度，成功前最多 99%)
*   **`imageUrl`**: 生成好的图片地址 (**仅 `status=success` 时返回**)
*   `failedReason`: 失败原因 (如"内容违规"、"超时"，**仅 `status=failed` 时返回**，供前端弹窗提醒)

GET /api/task/options (获取生图配置项字典)
场景：生图页面的初始化接口，动态渲染下拉框或单选按钮。

出参 (Data)：
    * models: 数组 [{ "modelId": "pro_1", "name": "专业版 v1.0", "cost": 10, "isVipOnly": false }]
    * aspectRatios: 数组 ["1:1", "4:3", "3:4", "16:9", "9:16"] (前端直接遍历渲染)
---

### 3. 资产与钱包模块

#### `GET /api/user/profile` (获取我的资产与基础信息)
*   `userId`, `nickname`, `avatar`, `phone`
*   **`balance`**: 当前可用算力 (前端大字展示)
*   `isVip`: Boolean
*   `type`: String 会员类型,包月/包年会员/普通会员（默认 isVip为false ）
*   `vipExpireTime`: 会员到期时间戳 (若 `isVip=false` 则为空)

#### `GET /api/asset/logs` (获取算力账单流水)
*   **分页信息**：`total`, `page`
*   **list** (数组)：
    *   `id`: 流水 ID
    *   `type`: 流水类型标识 (`1`:充值, `2`:生图消耗, `3`:生图失败退回, `4`:签到， 5 邀请奖励)
    *   `amount`: 变动数值 (如 `+100` 或 `-10`)
    *   `balanceAfter`: 变动后的余额 (方便对账)
    *   `description`: 中文描述 (如 "微信充值", "生成插画", "新用户注册赠送"， 等等)

---

### 4. 商品与订单模块

#### `GET /api/store/products` (获取充值/会员商品列表)
*返回数组，供前端渲染充值面板。*
*   `id`: 商品 ID
*   `type`: 商品类型 (`point_pack`: 算力包, `vip`: 会员)
*   `name`: 商品名称 (如 "基础算力包")
*   **`price`**: 实际支付金额 (单位：分，如 `990` 代表 9.9元)
*   `originalPrice`: 划线价 (单位：分，用于前端显示打折感)
*   `pointAmount`: 包含的基础算力点数 (如 `100`)
*   `bonusAmount`: 额外赠送点数 (如 `20`，前端可标红提示“赠20点”)
*   `tag`: 角标提示 (如 "热销", "超值")

#### `POST /api/order/create` (创建支付订单)
*   `orderNo`: 系统内部订单号
*   **`payParams`**: (对象，直接透传给微信小程序 `wx.requestPayment` 使用)
    *   `timeStamp`: 时间戳
    *   `nonceStr`: 随机字符串
    *   `package`: 统一下单接口返回的 prepay_id 参数值 (如 `prepay_id=wx...`)
    *   `signType`: 签名算法 (通常是 `RSA`)
    *   `paySign`: 签名

#### `GET /api/order/status/{orderNo}` (前端查单 - 支付后轮询)
*   `orderNo`: 订单号
*   `status`: 订单状态 (`waiting`:待支付, `success`:支付成功并发放算力, `failed`:支付失败)

---

### 5. 个人中心与裂变模块

#### `GET /api/user/history` (获取我的历史作品)
*   **分页信息**：`total`, `page`
*   **list** (数组)：
    *   `taskId`: 任务 ID
    *   `imageUrl`: 生成的图片地址
    *   `prompt`: 提示词概要 (前端截断显示)
    *   `status`: 任务最终状态 (`success`, `failed`)
    *   `cost`: 本次消耗的算力

POST /api/user/history/{taskId}/publish (发布作品到公开画廊)
场景：用户在“我的历史作品”中，觉得某张图很好看，点击“公开分享”。

出参 (Data)：
    * status: pending (审核中) 或 published (已发布)。建议新站做一层人工审核，或对接微信内容安全API。
DELETE /api/user/history/{taskId} (删除历史作品)
场景：用户生成的图太丑或涉及隐私，需要删除。（前端在历史列表加一个垃圾桶图标）

出参 (Data)：Boolean (成功与否)
逻辑：软删除，仅改变状态，OSS文件不一定要立刻删。

GET /api/user/likes (获取我的喜欢列表)
场景：个人中心 -> 我的点赞，用户想要找回以前点赞过的优秀作品抄提示词。

出参：数据结构与 GET /api/gallery/list 完全一致，只返回当前用户点赞过的数据。

#### `GET /api/user/invite-info` (获取邀请裂变信息)
*   `inviteCode`: 我的专属邀请码 (如 `A8F9K2`)
*   `invitedCount`: 已成功邀请的新用户数量
*   `totalRewardPoints`: 累计通过邀请赚取的算力总数
*   `ruleText`: 规则文案 (如 "每邀请1位新用户注册，双方各得 50 点算力")

POST /api/user/bind-invite (绑定邀请人)
场景：新用户通过分享卡片或扫码进入小程序，前端在静默登录/注册后，提取 URL 参数上的 inviteCode 调用此接口。

入参 (Body)：
    * inviteCode: 邀请人的专属邀请码
出参 (Data)：
    * isSuccess: Boolean (绑定是否成功)
    * rewardPoints: 本次获得的算力奖励数量
⚠️ 后端防刷核心逻辑 (必须做)：
    1. 不能填自己的邀请码（防自刷）。
    2. 检查当前用户是否已经绑定过别人（每人仅限被邀请1次）。
    3. 开启数据库事务：同时给双方账户 balance 增加算力，并在 asset_logs 写入类型为 5（邀请奖励）的流水记录。


POST /api/user/sign-in (每日签到领取算力)
场景：首页弹窗或个人中心的“签到领算力”按钮。

出参 (Data)：
    * rewardPoints: 本次签到获得的算力
    * continuousDays: 连续签到天数 (用于后期做连续7天大奖)
⚠️ 后端核心逻辑：需要利用 Redis (SetNX) 或数据库唯一索引防止并发连点造成的重复发放。


## 阶段二 B 端（管理后台） 

以下是为你量身梳理的 B 端接口返回详细信息（聚焦 `data` 层），同样保持极简但绝对够前端直接画 UI。

---

### 1. 业务大盘与统计 (Dashboard)

#### `GET /admin/dashboard/stats` (获取大盘核心指标)
*前端渲染页面顶部的 4 个数据卡片*
*   `todayNewUsers`: 今日新增用户数
*   `todayActiveUsers`: 今日活跃用户数 (DAU)
*   `todayGenerations`: 今日生成图片总数
*   `todayRevenue`: 今日充值总金额 (单位：分)
*   *建议追加同比数据，方便前端做绿色/红色的涨跌幅箭头*：
    *   `revenueGrowthRate`: 较昨日涨跌幅 (如 `12.5` 代表 +12.5%, `-5.0` 代表 -5%)

#### `GET /admin/dashboard/trend` (获取算力/营收趋势图)
*前端渲染 ECharts 趋势折线图*
*   `xAxis`: 数组 (如 `["10-01", "10-02", "10-03"...]`)
*   `series`: (对象，包含不同维度的数据数组，需与 xAxis 一一对应)
    *   `costPoints`: 每日总算力消耗数组 (如 `[500, 1200, 800...]`)
    *   `revenue`: 每日营收金额数组 (如 `[990, 1980, 0...]`)

---

### 2. 用户与资产管理 (User Mgt)

#### `GET /admin/user/list` (分页获取用户列表)
*   **分页**：`total`, `page`, `pageSize`
*   **list** (数组)：
    *   `userId`, `nickname`, `avatar`, `phone`
    *   `balance`: 当前剩余可用算力
    *   `isVip`: Boolean
    *   `status`: 状态 (`normal`: 正常, `banned`: 已封禁)
    *   `registerTime`: 注册时间

#### `GET /admin/user/{userId}/detail` (获取用户详情)
*   `profile`: 基础信息 (同上)
*   `stats`: 统计信息 (如 `{ totalCostPoints: 累计消耗算力, totalPayAmount: 累计充值金额 }`)
*   *(注：用户的订单记录和算力明细，前端通常复用下方的“订单列表”和“资产流水”接口，传入 userId 即可，不需要包在一个接口里，否则太臃肿。)*

#### `POST /admin/user/adjust-balance` (人工干预用户算力)
*入参需包含 userId, amount(+增/-减), remark(操作原因)*
*   `isSuccess`: Boolean
*   `balanceAfter`: 操作后的最终余额

#### `PUT /admin/user/{userId}/status` (封禁/解封用户)
*   `currentStatus`: 操作后的状态 (`normal` 或 `banned`)

---

### 3. 画廊与内容运营 (Gallery Mgt)

#### `GET /admin/content/history-list` (创作历史总库监控)
*前端以图文瀑布流或带图表格展示，用于巡查*
*   **分页**：`total`, `page`, `pageSize`
*   **list** (数组)：
    *   `taskId`, `imageUrl`, `prompt`, `modelName`
    *   `author`: `{ userId, nickname }` (点击可跳转用户详情)
    *   `status`: 生成状态 (`success`, `failed`... 主要是看成功的)
    *   `isPromoted`: Boolean (标记该图是否已经被运营推到了首页画廊)
    *   `createTime`: 生成时间

#### `GET /admin/gallery/list` (管理首页画廊)
*   **分页**：`total`, `page`, `pageSize`
*   **list** (数组)：
    *   `galleryId`: 画廊记录ID
    *   `imageUrl`, `prompt`
    *   `authorName`: 作者昵称 (如果是官方马甲则显示官方名)
    *   `sortWeight`: 排序权重 (数字越大越靠前，默认 0)
    *   `categoryId`: 所属分类ID
    *   `likeCount`, `viewCount`: 真实互动数据

#### `PUT /admin/gallery/{id}/weight` (修改首页排序权重 - 置顶)
*   `isSuccess`: Boolean

#### `GET /admin/gallery/categories` (获取画廊分类列表)
*   **list** (数组)：
    *   `id`: 分类ID
    *   `name`: 分类名称 (如 "二次元", "写实", "头像")
    *   `sort`: 排序值

---

### 4. 商品与定价管理 (Product Mgt)

#### `GET /admin/product/list` (商品配置列表)
*   **分页/全量** (商品一般不多，可不分页直接返回 `list` 数组)：
    *   `id`, `type` (`point_pack`, `vip`), `name`
    *   `price`: 实际售价 (分)
    *   `originalPrice`: 划线价 (分)
    *   `pointAmount`: 基础算力
    *   `bonusAmount`: 赠送算力
    *   `status`: 状态 (`on`: 上架, `off`: 下架)
    *   `updateTime`: 最后修改时间

*(POST 新增、PUT 修改、DELETE 删除接口通常只返回 `{ isSuccess: true }` 即可)*

---

### 5. 财务与订单管理 (Order Mgt)

#### `GET /admin/order/list` (订单明细列表)
*   **分页**：`total`, `page`, `pageSize`
*   **list** (数组)：
    *   `orderNo`: 系统订单号
    *   `thirdTradeNo`: 微信支付流水号 (退款对账用)
    *   `user`: `{ userId, nickname, phone }`
    *   `productName`: 购买的商品名称
    *   `payAmount`: 实际支付金额 (分)
    *   `status`: `waiting`(待支付), `success`(已支付), `refunded`(已退款)
    *   `createTime`: 下单时间
    *   `payTime`: 支付完成时间

#### `POST /admin/order/{orderNo}/refund` (订单退款)
*入参最好带上操作原因 remark*
*   `isSuccess`: Boolean
*   `refundId`: 退款流水号
*   `deductedPoints`: 本次退款系统自动扣除的算力数量 (前端展示给运营看：“退款成功，并已扣除该用户 100 点算力”)

---

### 6. 核心系统配置 (Config Mgt)

#### `GET /admin/system/config` (获取动态参数配置)
*前端常用于渲染一个巨大的 Form 表单*
*   `registerRewardPoints`: 新用户注册赠送算力数量 (Number，如 20)
*   `inviteRewardPoints`: 邀请一人奖励算力数量 (Number，如 50)
*   `defaultGenerateCost`: 默认文生图消耗单价 (Number，如 10)
*   `currentModelVersion`: 当前底层大模型版本号/标识 (String，如 "midjourney-v6" 或 "dall-e-3")
*   *(扩展预留字段)* `isMaintenance`: Boolean (是否开启全站系统维护模式，限制生图)

#### `PUT /admin/system/config` (保存动态参数)
*前端把上述大 Form 表单的数据一把梭提交*
*   `isSuccess`: Boolean

---
 

 