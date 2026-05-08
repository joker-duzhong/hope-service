# AuraKey API 文档

> **版本**：v1.0  
> **Base URL**：`/api/v1/aurakey`  
> **协议**：HTTPS  
> **鉴权**：除特殊说明外，所有接口均需在请求头携带 Bearer Token  
> ```
> Authorization: Bearer <access_token>
> ```

---

## 通用响应结构

### 普通响应

```json
{
  "code": 200,
  "message": "success",
  "data": { ... }
}
```

### 分页响应

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [ ... ],
    "total": 100,
    "page": 1,
    "page_size": 20,
    "total_pages": 5
  }
}
```

### 错误响应

```json
{
  "code": 400,
  "message": "错误描述"
}
```

| HTTP 状态码 | 含义 |
|---|---|
| 400 | 参数错误 / 业务逻辑失败（如算力不足） |
| 401 | 未登录或 Token 失效 |
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

---

## 1. 发现页 / 画廊模块

### 1.1 获取画廊列表

> 允许未登录访问。已登录时会返回当前用户的 `is_liked` 状态。

**GET** `/gallery/list`

**Query 参数**

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| page | int | 1 | 页码，从 1 开始 |
| pageSize | int | 20 | 每页条数，建议 20 |
| categoryId | string (UUID) | - | 画廊分类 ID，不传则返回全部分类作品 |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "thumb_url": "https://cdn.example.com/img/abc_thumb.jpg",
        "aspect_ratio": "16:9",
        "author": {
          "user_id": "550e8400-e29b-41d4-a716-446655440001",
          "nickname": "阿杰",
          "avatar": "https://cdn.example.com/avatar/aj.jpg"
        },
        "like_count": 128,
        "is_liked": false,
        "view_count": 3200
      }
    ],
    "total": 200,
    "page": 1,
    "page_size": 20,
    "total_pages": 10
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string (UUID) | 作品唯一 ID |
| thumb_url | string | 缩略图地址（OSS 压缩版，适合列表展示） |
| aspect_ratio | string | 宽高比，如 `"1:1"`、`"16:9"`，前端用于瀑布流排版 |
| author.user_id | string (UUID) | 作者用户 ID |
| author.nickname | string | 作者昵称 |
| author.avatar | string | 作者头像 URL |
| like_count | int | 点赞总数 |
| is_liked | bool | 当前用户是否已点赞（未登录时恒为 `false`） |
| view_count | int | 浏览量 |

---

### 1.2 获取画廊分类列表

> 允许未登录访问。返回的分类 ID 可用于画廊列表 `categoryId` 参数。

**GET** `/gallery/categories`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440010",
      "name": "风景",
      "sort": 10
    }
  ]
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string (UUID) | 分类 ID |
| name | string | 分类名称 |
| sort | int | 排序权重，数值越大越靠前 |

---

### 1.3 获取画廊详情

> 允许未登录访问。每次请求浏览量 +1。

**GET** `/gallery/{id}`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| id | string (UUID) | 作品 ID |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "thumb_url": "https://cdn.example.com/img/abc_thumb.jpg",
    "image_url": "https://cdn.example.com/img/abc_origin.jpg",
    "aspect_ratio": "16:9",
    "prompt": "a futuristic city at night, neon lights, cyberpunk",
    "model_name": "专业版 v1.0",
    "author": {
      "user_id": "550e8400-e29b-41d4-a716-446655440001",
      "nickname": "阿杰",
      "avatar": "https://cdn.example.com/avatar/aj.jpg"
    },
    "like_count": 128,
    "is_liked": false,
    "view_count": 3201
  }
}
```

**额外字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| image_url | string | 高清原图地址 |
| prompt | string | 生图提示词，前端可用于「一键同款」功能 |
| model_name | string | 生图所用模型名称 |

---

### 1.4 点赞 / 取消点赞

> **需要登录**。重复调用自动切换点赞状态（toggle 模式）。

**POST** `/gallery/{id}/like`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| id | string (UUID) | 作品 ID |

**Request Body**：无

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_liked": true,
    "like_count": 129
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| is_liked | bool | 操作后的最终点赞状态（前端直接替换，无需自己加减） |
| like_count | int | 最新点赞总数 |

---

## 2. 生图创作模块

### 2.1 获取生图配置项

> 允许未登录访问。用于渲染生图页面的模型选择器和比例选择器。

**GET** `/task/options`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "models": [
      {
        "model_id": "pro_1",
        "name": "专业版 v1.0",
        "cost": 10,
        "is_vip_only": false
      }
    ],
    "aspect_ratios": ["1:1", "4:3", "3:4", "16:9", "9:16"]
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| models[].model_id | string | 模型 ID，提交生图任务时使用 |
| models[].name | string | 模型展示名称 |
| models[].cost | int | 每次消耗算力点数 |
| models[].is_vip_only | bool | 是否仅 VIP 可用 |
| aspect_ratios | string[] | 支持的宽高比列表 |

---

### 2.2 提交生图任务

> **需要登录**。调用后立即返回任务 ID，前端凭此轮询状态。算力实时扣除。

**POST** `/task/generate`

**Request Body**

```json
{
  "prompt": "a futuristic city at night, neon lights, cyberpunk",
  "model_name": "pro_1",
  "aspect_ratio": "16:9"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| prompt | string | ✅ | 生图提示词，建议英文，效果更好 |
| model_name | string | ✅ | 模型 ID，来自 `/task/options` |
| aspect_ratio | string | ✅ | 宽高比，来自 `/task/options` |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440002",
    "frozen_points": 10,
    "balance_after": 90
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | string (UUID) | 任务 ID，用于后续轮询 |
| frozen_points | int | 本次冻结/扣除的算力点数 |
| balance_after | int | 扣除后的剩余算力（前端可即时更新显示） |

**错误情况**

| code | message | 处理建议 |
|---|---|---|
| 400 | 算力不足 | 跳转充值页面 |

---

### 2.3 轮询任务状态

> **需要登录**。建议每 **2 秒**请求一次，直到 `status` 为 `success` 或 `failed`。

**GET** `/task/status/{task_id}`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| task_id | string (UUID) | 任务 ID（来自 2.2 接口） |

**响应示例（生成中）**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440002",
    "status": "processing",
    "progress": 45,
    "image_url": null,
    "failed_reason": null
  }
}
```

**响应示例（成功）**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440002",
    "status": "success",
    "progress": 100,
    "image_url": "https://cdn.example.com/img/generated_abc.jpg",
    "failed_reason": null
  }
}
```

**响应示例（失败）**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440002",
    "status": "failed",
    "progress": 20,
    "image_url": null,
    "failed_reason": "内容违规，请修改 Prompt 后重试"
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | string (UUID) | 任务 ID |
| status | string | 状态枚举（见下表） |
| progress | int | 进度百分比 0-100，成功时为 100 |
| image_url | string \| null | 生成的图片地址，**仅 `success` 时有值** |
| failed_reason | string \| null | 失败原因，**仅 `failed` 时有值**，可直接 toast 给用户 |

**status 枚举**

| 值 | 含义 | 前端处理 |
|---|---|---|
| `pending` | 排队中 | 继续轮询 |
| `processing` | 生成中 | 继续轮询，展示进度条 |
| `success` | 生成成功 | 停止轮询，展示图片 |
| `failed` | 生成失败 | 停止轮询，展示 failed_reason，算力已自动退回 |

> **注意**：`failed` 时算力会自动全额退回，无需用户操作。

---

## 3. 资产与钱包模块

### 3.1 获取个人资产与基础信息

> **需要登录**。

**GET** `/user/profile`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "user_id": "550e8400-e29b-41d4-a716-446655440001",
    "nickname": "阿杰",
    "avatar": "https://cdn.example.com/avatar/aj.jpg",
    "phone": "138****8888",
    "balance": 90,
    "is_vip": true,
    "type": "包月会员",
    "vip_expire_time": 1748736000
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | string (UUID) | 用户 ID |
| nickname | string | 昵称 |
| avatar | string | 头像 URL |
| phone | string | 手机号（脱敏） |
| balance | int | 当前可用算力点数 |
| is_vip | bool | 是否是 VIP |
| type | string | 会员类型：`"普通会员"` / `"包月会员"` / `"包年会员"` |
| vip_expire_time | int \| null | VIP 到期时间（Unix 时间戳秒），非 VIP 时为 `null` |

---

### 3.2 获取算力账单流水

> **需要登录**。

**GET** `/asset/logs`

**Query 参数**

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| page | int | 1 | 页码 |
| pageSize | int | 20 | 每页条数 |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440010",
        "type": 2,
        "amount": -10,
        "balance_after": 90,
        "description": "生成插画"
      },
      {
        "id": "550e8400-e29b-41d4-a716-446655440011",
        "type": 1,
        "amount": 100,
        "balance_after": 100,
        "description": "充值购买 100点算力包"
      }
    ],
    "total": 50,
    "page": 1,
    "page_size": 20,
    "total_pages": 3
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string (UUID) | 流水记录 ID |
| type | int | 流水类型（见下表） |
| amount | int | 变动数值，正数为增加，负数为扣除 |
| balance_after | int | 变动后余额（方便对账） |
| description | string | 流水描述，可直接展示给用户 |

**type 枚举**

| 值 | 含义 | amount 方向 |
|---|---|---|
| 1 | 充值 | 正数 |
| 2 | 生图消耗 | 负数 |
| 3 | 生图失败退回 | 正数 |
| 4 | 签到奖励 | 正数 |
| 5 | 邀请奖励 | 正数 |

---

## 4. 商品与订单模块

### 4.1 获取商品列表

> 允许未登录访问。用于渲染充值/开会员页面。

**GET** `/store/products`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440020",
      "type": "point_pack",
      "name": "100点算力包",
      "price": 1000,
      "original_price": 1500,
      "point_amount": 100,
      "bonus_amount": 20,
      "tag": "限时特惠",
      "created_at": "2023-11-20T10:00:00Z",
      "updated_at": "2023-11-20T10:00:00Z"
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440021",
      "type": "vip",
      "name": "包月会员",
      "price": 2900,
      "original_price": 5800,
      "point_amount": 0,
      "bonus_amount": 0,
      "tag": "包月会员",
      "created_at": "2023-11-20T10:00:00Z",
      "updated_at": "2023-11-20T10:00:00Z"
    }
  ]
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string (UUID) | 商品 ID，下单时使用 |
| type | string | 商品类型：`"point_pack"` 算力包 / `"vip"` 会员 |
| name | string | 商品展示名称 |
| price | int | 售价，单位**分**（如 1000 = 10元） |
| original_price | int \| null | 划线价，单位分，可为空 |
| point_amount | int | 购买后获得的算力点数（仅 `point_pack` 有效） |
| bonus_amount | int | 赠送的额外算力点数 |
| tag | string \| null | 标签文字，如「限时特惠」「推荐」，可为空 |
| created_at | string | 创建时间 (ISO 8601 格式) |
| updated_at | string | 最近更新时间 (ISO 8601 格式) |

---

### 4.2 创建支付订单

> **需要登录**。调用后发起微信小程序支付。

**POST** `/order/create`

**Request Body**

```json
{
  "product_id": "550e8400-e29b-41d4-a716-446655440020",
  "openid": "oxxxxxxxxxxxxxxxxxxxxxx"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| product_id | string (UUID) | ✅ | 商品 ID |
| openid | string | ✅ | 微信小程序用户 openid（通过 `wx.login` 获取） |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "order_no": "OD17480000000012ab",
    "pay_params": {
      "timeStamp": "1748000000",
      "nonceStr": "abc123xyz",
      "package": "prepay_id=wx01xxxxxxxx",
      "signType": "RSA",
      "paySign": "xxxxxx"
    }
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| order_no | string | 订单号，用于查询订单状态 |
| pay_params | object | 微信小程序调起支付所需参数，直接传给 `wx.requestPayment()` |

> **前端调用示例**：
> ```javascript
> wx.requestPayment({
>   timeStamp: pay_params.timeStamp,
>   nonceStr: pay_params.nonceStr,
>   package: pay_params.package,
>   signType: pay_params.signType,
>   paySign: pay_params.paySign,
>   success: () => { /* 轮询 4.3 查订单状态 */ },
>   fail: () => { /* 用户取消或支付失败 */ }
> })
> ```

---

### 4.3 查询订单状态

> **需要登录**。微信支付回调可能有延迟，建议支付成功后 1-2 秒后查询。

**GET** `/order/status/{order_no}`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| order_no | string | 订单号（来自 4.2 接口） |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "order_no": "OD17480000000012ab",
    "status": "success"
  }
}
```

**status 枚举**

| 值 | 含义 |
|---|---|
| `waiting` | 待支付 |
| `success` | 支付成功，算力/会员已自动发放 |
| `failed` | 支付失败 |

---

## 5. 个人中心模块

### 5.1 获取创作历史

> **需要登录**。

**GET** `/user/history`

**Query 参数**

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| page | int | 1 | 页码 |
| pageSize | int | 20 | 每页条数 |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "items": [
      {
        "task_id": "550e8400-e29b-41d4-a716-446655440002",
        "image_url": "https://cdn.example.com/img/generated_abc.jpg",
        "prompt": "a futuristic city...",
        "status": "success",
        "cost": 10
      }
    ],
    "total": 30,
    "page": 1,
    "page_size": 20,
    "total_pages": 2
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| task_id | string (UUID) | 任务 ID |
| image_url | string \| null | 生成图片 URL，失败时为 null |
| prompt | string | 提示词（最多展示 20 字，已截断） |
| status | string | 任务状态（同 2.3） |
| cost | int | 消耗的算力点数 |

---

### 5.2 发布作品到画廊

> **需要登录**。仅 `status=success` 的任务可发布。发布后作品出现在公开画廊。

**POST** `/user/history/{task_id}/publish`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| task_id | string (UUID) | 任务 ID |

**Request Body**：无

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "status": "published"
  }
}
```

---

### 5.3 删除历史任务

> **需要登录**。软删除，不影响已发布到画廊的作品。

**DELETE** `/user/history/{task_id}`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| task_id | string (UUID) | 任务 ID |

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": true
}
```

---

## 6. 裂变 / 邀请模块

### 6.1 获取邀请信息

> **需要登录**。用于渲染邀请页面。

**GET** `/user/invite-info`

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "invite_code": "ABC123",
    "invited_count": 5,
    "total_reward_points": 250,
    "rule_text": "每邀请1位新用户注册，双方各得 50 点算力"
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| invite_code | string | 当前用户的专属邀请码（6位大写字母+数字） |
| invited_count | int | 已成功邀请的人数 |
| total_reward_points | int | 通过邀请累计获得的总算力 |
| rule_text | string | 邀请规则说明文字，直接展示给用户 |

---

### 6.2 绑定邀请码

> **需要登录**。新用户注册后填写邀请码。**每个账号只能绑定一次**，且不能绑定自己的邀请码。

**POST** `/user/bind-invite`

**Request Body**

```json
{
  "invite_code": "ABC123"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| invite_code | string | ✅ | 邀请人的邀请码 |

**响应示例（成功）**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_success": true,
    "reward_points": 50
  }
}
```

**响应示例（失败）**

```json
{
  "code": 200,
  "message": "无法绑定或已绑定过",
  "data": {
    "is_success": false,
    "reward_points": 0
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| is_success | bool | 是否绑定成功 |
| reward_points | int | 本次获得的奖励算力（失败时为 0） |

> **注意**：绑定成功后，被邀请者和邀请者各自获得 50 点算力奖励。`is_success=false` 时 `message` 字段说明具体原因。

---

## 7. 签到模块

### 7.1 每日签到

> **需要登录**。每日只能签到一次，重复调用返回 400 错误。

**POST** `/user/sign-in`

**Request Body**：无

**响应示例**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "reward_points": 10,
    "continuous_days": 3
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| reward_points | int | 本次签到获得的算力点数 |
| continuous_days | int | 当前连续签到天数（含今日） |

**错误情况**

| code | message | 处理建议 |
|---|---|---|
| 400 | 今日已签到，明天再来吧 | 展示已签到状态，无需重复请求 |

---

## 8. 系统 Webhook（仅供后端参考）

### 8.1 微信支付回调

> 此接口由微信服务器主动调用，**前端无需关心**。

**POST** `/wechat-notify`

---

## 附录：前端接入流程图

### 生图完整流程

```
1. 调用 GET /task/options
   → 渲染模型选择器 & 比例选择器

2. 用户填写 Prompt，点击「生成」
   → 调用 POST /task/generate
   → 获取 task_id，本地缓存

3. 每隔 2 秒调用 GET /task/status/{task_id}
   → status=pending/processing：更新进度条
   → status=success：展示图片，停止轮询
   → status=failed：提示 failed_reason，停止轮询（算力自动退回）

4. 生成成功后，用户点击「发布到广场」
   → 调用 POST /user/history/{task_id}/publish
```

### 充值完整流程

```
1. 调用 GET /store/products → 渲染商品列表

2. 用户选择商品，点击购买
   → 调用 wx.login() 获取 code
   → 换取 openid（通过已有登录接口）
   → 调用 POST /order/create
   → 获取 order_no 和 pay_params

3. 调用 wx.requestPayment(pay_params)
   → 成功：等待 1.5s 后调用 GET /order/status/{order_no}
   → 失败：提示用户支付取消

4. 查询订单状态
   → status=success：刷新用户资产，提示到账
   → status=waiting：等待 2s 后重试（最多 3 次）
```

> ---
  
  ## 1. Missing Schemas in `schemas.py`
  
  The [admin_router.py](admin_router.py#L12-L17) imports **15 Admin schemas** t
hat don't exist in [schemas.py](schemas.py). These are required for the admin e
ndpoints to function.
  
  ### Missing Schema Implementations:
  
  ```python
  # === Admin Dashboard & Stats ===
  class AdminStatsResponse(BaseModel):
      today_new_users: int
      today_active_users: int
      today_generations: int
      today_revenue: int
      revenue_growth_rate: float
  
  
  # === User Management ===
  class AdminAdjustBalanceRequest(BaseModel):
      user_id: uuid.UUID
      amount: int  # Can be positive or negative
      remark: Optional[str] = None
  
  
  class AdminAdjustBalanceResponse(BaseModel):
      is_success: bool
      balance_after: int
  
  
  class AdminUserStatusUpdate(BaseModel):
      status: str  # "normal" or "banned"
  
  
  class AdminUserListItem(BaseModel):
      user_id: uuid.UUID
      username: str
      email: Optional[str] = None
      is_active: bool
      created_at: datetime
  
  
  class AdminUserDetail(BaseModel):
      user_id: uuid.UUID
      username: str
      email: Optional[str] = None
      phone: Optional[str] = None
      balance: int
      is_active: bool
      is_vip: bool
      vip_type: Optional[str] = None
      vip_expire_time: Optional[int] = None  # timestamp
      invite_code: str
      invited_count: int
      total_reward_points: int
      created_at: datetime
  
  
  # === Refund Management ===
  class AdminRefundRequest(BaseModel):
      remark: Optional[str] = None
  
  
  class AdminRefundResponse(BaseModel):
      is_success: bool
      refund_id: Optional[str] = None
      deducted_points: int  # Points deducted from user
  
  
  # === History/Logs ===
  class AdminHistoryListItem(BaseModel):
      task_id: uuid.UUID
      user_id: uuid.UUID
      image_url: Optional[str] = None
      prompt: str
      status: str
      cost: int
      created_at: datetime
  
  
  # === Gallery Categories ===
  class AdminGalleryCategoryResponse(BaseModel):
      id: uuid.UUID
      name: str
      sort: int
  
      class Config:
          from_attributes = True
  
  
  class AdminGalleryCategoryCreate(BaseModel):
      name: str
      sort: int = 0
  
  
  # === Model Options ===
  class AdminOptionModelResponse(BaseModel):
      id: uuid.UUID
      model_id: str
      name: str
      cost: int
      is_vip_only: bool
      status: str  # "on" or "off"
  
      class Config:
          from_attributes = True
  
  
  class AdminOptionModelCreate(BaseModel):
      model_id: str
      name: str
      cost: int
      is_vip_only: bool = False
      status: str = "on"
  
  
  # === Aspect Ratio Options ===
  class AdminOptionRatioResponse(BaseModel):
      id: uuid.UUID
      ratio: str  # "1:1", "16:9", etc.
      sort: int
      status: str
  
      class Config:
          from_attributes = True
  
  
  class AdminOptionRatioCreate(BaseModel):
      ratio: str
      sort: int = 0
      status: str = "on"
  ```
  
> ---
  
  ## 2. Critical Bugs Identified
  
  ### Bug #1: Potential `frozen_points` Double Refund
  
  **Location:** [services.py](services.py#L240-L260) vs [services.py](services.
py#L173-L195)
  
  **Issue:**
  - In `submit_generate_task` (line 173): `frozen_points=cost` is set when task
 created
  - In `get_task_status` (line 240): If task fails and `frozen_points > 0`, it 
refunds the user
  - **However**: The code correctly sets `task.frozen_points = 0` after refundi
ng, preventing double refunds on subsequent calls
  
  **Verdict:** ✅ **Actually safe** - the refund prevention is implemented cor
rectly.
  
> ---
  
  ### Bug #2: Timezone Inconsistency in `daily_sign_in`
  
  **Location:** [services.py](services.py#L467-L490)
  
  **Issue:**
  ```python
  # Gets today's date in UTC
  today_utc = datetime.now(timezone.utc).date()  # Line 468
  
  # Then checks if already signed in using Date cast
  already = await db.scalar(
      select(AurakeyAssetLog).where(
          AurakeyAssetLog.user_id == user_id,
          AurakeyAssetLog.type == 4,
          cast(AurakeyAssetLog.created_at, Date) == today_utc,  # Line 474
      )
  )
  ```
  
  **Problem:**
  - If server is running in non-UTC timezone (e.g., Asia/Shanghai), `datetime.n
ow(timezone.utc).date()` gets today in UTC
  - `AurakeyAssetLog.created_at` timestamps are likely created in local timezon
e or app timezone
  - When database casts to Date, it may interpret the timestamp differently
  - **Example**: User creates log at 2024-01-15 23:00 UTC+8. `today_utc` would 
be 2024-01-15, but cast in DB might read as 2024-01-16 local
  
  **Fix Required:** Use app's configured timezone consistently or ensure all ti
mestamps use UTC.
  
> ---
  
  ### Bug #3: Incorrect `continuous_days` Calculation
  
  **Location:** [services.py](services.py#L485-L493)
  
  **Issue:**
  ```python
  continuous_days = 0
  check_date = today_utc
  sign_dates = {log_dt.date() for log_dt in sign_logs}
  while check_date in sign_dates:
      continuous_days += 1
      check_date -= timedelta(days=1)
  ```
  
  **Problem:**
  - After signing in today, the new log is committed (line 481)
  - But `sign_logs` was queried BEFORE the new log (line 484) ❌
  - The query returns logs ordered by created_at DESC with limit 365
  - **Result**: The latest log included won't include today's just-created log
  - **Consequence**: Continuous days calculation is off by 1 for the first sign
-in
  
  **Fix Required:** Refresh the sign_logs query AFTER committing today's log, o
r calculate continuous_days differently.
  
> ---
  
  ### Bug #4: `get_task_options` Returns Hardcoded Data
  
  **Location:** [router.py](router.py#L86-L91)
  
  **Issue:**
  ```python
  @router.get("/task/options", response_model=ResponseModel[TaskOptionsResponse
])
  async def get_task_options():
      res = {
          "models": [{"model_id": "pro_1", "name": "专业版 v1.0", "cost": 10,
 "is_vip_only": False}],
          "aspect_ratios": ["1:1", "4:3", "3:4", "16:9", "9:16"]
      }
      return ResponseModel(data=res)
  ```
  
  **Problem:**
  - Hardcoded static data ignores database models:
    - [AurakeyModelOption](models.py#L39-L48) exists with full CRUD via admin A
PI
    - [Aurakeyaspect_ratioOption](models.py#L51-L57) exists with full CRUD via a
dmin API
  - Admin can add/remove models and ratios, but frontend won't see updates
  - Cost information is hardcoded as 10, doesn't match product configuration
  
  **Fix Required:** Query from database and filter by status="on".
  
> ---
  
  ### Bug #5: `refund_order` Hardcoded Default Points
  
  **Location:** [admin_services.py](admin_services.py#L65-L75)
  
  **Issue:**
  ```python
  deducted = 0
  # deduct points assuming product point calculation (in a real system we refer
 to product schema)
  if asset:
      deducted = 100  # ⚠️ Default fallback - HARDCODED!
      asset.balance -= deducted
  ```
  
  **Problem:**
  - When refunding an order, the code always deducts 100 points as default
  - Should use actual product's `point_amount + bonus_amount`
  - If a product sells 500 points, deducting 100 is wrong
  
  **Fix Required:** Query the product and calculate: `deducted = product.point_
amount + product.bonus_amount`
  
> ---
  
  ## 3. Draft Admin API Documentation
  
  Here's the content to add to [api.md](api.md):
  
  ```markdown
> ---
  
  # B端（管理）API 文档
  
  > **访问权限**：需具有 `aurakey_admin` 角色  
  > **前缀**：`/admin`  
  > **完整路径示例**：`GET /api/v1/aurakey/admin/dashboard/stats`
  
> ---
  
  ## 1. 仪表板统计
  
  ### 1.1 获取仪表板统计数据
  
  **GET** `/admin/dashboard/stats`
  
  **权限**: `aurakey_admin`
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "today_new_users": 42,
      "today_active_users": 128,
      "today_generations": 356,
      "today_revenue": 15800,
      "revenue_growth_rate": 12.5
    }
  }
  ```
  
  **字段说明**
  
  | 字段 | 类型 | 说明 |
> |---|---|---|
  | today_new_users | int | 今日新注册用户数 |
  | today_active_users | int | 今日活跃用户数（需登录日志支持）
 |
  | today_generations | int | 今日生图任务总数 |
  | today_revenue | int | 今日收入（分，仅计成功订单） |
  | revenue_growth_rate | float | 相比昨日收入增长率（百分比） |
  
> ---
  
  ## 2. 用户管理
  
  ### 2.1 调整用户余额
  
  **POST** `/admin/user/adjust-balance`
  
  **权限**: `aurakey_admin`
  
  **请求体**
  
  ```json
  {
    "user_id": "550e8400-e29b-41d4-a716-446655440000",
    "amount": 100,
    "remark": "补偿已消耗算力"
  }
  ```
  
  **请求参数**
  
  | 字段 | 类型 | 必需 | 说明 |
> |---|---|---|---|
  | user_id | UUID | ✓ | 用户 ID |
  | amount | int | ✓ | 调整数量（正数加余额，负数扣余额） |
  | remark | string | | 备注说明，会记入流水 |
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "is_success": true,
      "balance_after": 550
    }
  }
  ```
  
> ---
  
  ### 2.2 更新用户状态（封禁/解封）
  
  **PUT** `/admin/user/{user_id}/status`
  
  **权限**: `aurakey_admin`
  
  **Path 参数**
  
  | 参数 | 类型 | 说明 |
> |---|---|---|
  | user_id | UUID | 用户 ID |
  
  **请求体**
  
  ```json
  {
    "status": "banned"
  }
  ```
  
  **请求参数**
  
  | 字段 | 类型 | 可选值 | 说明 |
> |---|---|---|---|
  | status | string | `normal`, `banned` | 目标状态 |
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "currentStatus": "banned"
    }
  }
  ```
  
> ---
  
  ## 3. 订单管理
  
  ### 3.1 退款订单
  
  **POST** `/admin/order/{order_no}/refund`
  
  **权限**: `aurakey_admin`
  
  **Path 参数**
  
  | 参数 | 类型 | 说明 |
> |---|---|---|
  | order_no | string | 订单号 |
  
  **请求体**
  
  ```json
  {
    "remark": "用户投诉，申请退款"
  }
  ```
  
  **请求参数**
  
  | 字段 | 类型 | 必需 | 说明 |
> |---|---|---|---|
  | remark | string | | 退款原因备注 |
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "is_success": true,
      "refund_id": "REFODxxxxxxxxxxxxxxxx",
      "deducted_points": 500
    }
  }
  ```
  
  **字段说明**
  
  | 字段 | 类型 | 说明 |
> |---|---|---|
  | is_success | bool | 退款是否成功 |
  | refund_id | string | 退款流水 ID |
  | deducted_points | int | 扣除的算力数（用户充值时赠送的算力
） |
  
  **注意**：系统会自动将用户充值时获得的额外算力（bonus�
�扣回，以防止反复套现。
  
> ---
  
  ## 4. 配置管理
  
  ### 4.1 图库分类管理
  
  #### 4.1.1 获取所有分类

  **GET** `/gallery/categories`

  **权限**: 无需登录
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "风景",
        "sort": 1
      },
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "name": "人物",
        "sort": 2
      }
    ]
  }
  ```
  
> ---
  
  #### 4.1.2 创建分类
  
  **POST** `/admin/gallery/categories`
  
  **权限**: `aurakey_admin`
  
  **请求体**
  
  ```json
  {
    "name": "抽象艺术",
    "sort": 3
  }
  ```
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "is_success": true
    }
  }
  ```
  
> ---
  
  ### 4.2 生图模型管理
  
  #### 4.2.1 获取所有模型
  
  **GET** `/admin/task/options/models`
  
  **权限**: `aurakey_admin`
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "model_id": "pro_v1",
        "name": "专业版 v1.0",
        "cost": 10,
        "is_vip_only": false,
        "status": "on"
      },
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "model_id": "premium_v2",
        "name": "高级版 v2.0",
        "cost": 20,
        "is_vip_only": true,
        "status": "on"
      }
    ]
  }
  ```
  
> ---
  
  #### 4.2.2 创建模型
  
  **POST** `/admin/task/options/models`
  
  **权限**: `aurakey_admin`
  
  **请求体**
  
  ```json
  {
    "model_id": "ultra_v3",
    "name": "超级版 v3.0",
    "cost": 30,
    "is_vip_only": false,
    "status": "on"
  }
  ```
  
  **请求参数**
  
  | 字段 | 类型 | 必需 | 默认值 | 说明 |
> |---|---|---|---|---|
  | model_id | string | ✓ | | 模型唯一标识（如 `pro_v1`） |
  | name | string | ✓ | | 模型显示名称 |
  | cost | int | ✓ | | 单次使用消耗的算力 |
  | is_vip_only | bool | | false | 是否仅限 VIP 使用 |
  | status | string | | "on" | 模型状态：`on` 可用，`off` 禁用 |
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "is_success": true
    }
  }
  ```
  
> ---
  
  ### 4.3 宽高比管理
  
  #### 4.3.1 获取所有宽高比
  
  **GET** `/admin/task/options/ratios`
  
  **权限**: `aurakey_admin`
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "ratio": "1:1",
        "sort": 1,
        "status": "on"
      },
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "ratio": "16:9",
        "sort": 2,
        "status": "on"
      }
    ]
  }
  ```
  
> ---
  
  #### 4.3.2 创建宽高比
  
  **POST** `/admin/task/options/ratios`
  
  **权限**: `aurakey_admin`
  
  **请求体**
  
  ```json
  {
    "ratio": "3:2",
    "sort": 3,
    "status": "on"
  }
  ```
  
  **请求参数**
  
  | 字段 | 类型 | 必需 | 默认值 | 说明 |
> |---|---|---|---|---|
  | ratio | string | ✓ | | 宽高比（如 `16:9`、`1:1`） |
  | sort | int | | 0 | 排序值（越大越靠前） |
  | status | string | | "on" | 状态：`on` 可用，`off` 禁用 |
  
  **响应**
  
  ```json
  {
    "code": 200,
    "message": "success",
    "data": {
      "is_success": true
    }
  }
  ```
  
> ---
  
  ## 常见操作流程
  
  ### 增加新生图模型流程
  1. 管理员通过 `POST /admin/task/options/models` 创建新模型
  2. 设置 `cost` 为该模型单次消耗的算力
  3. 可通过 `is_vip_only` 限制为会员专用
  4. 用户通过 `/task/options` 获取可用模型列表（自动过滤 `stat
us=on`）
  5. 用户选择模型调用 `POST /task/generate` 生成图片
  
  ### 用户冻结/解冻流程
  1. 检测到违规用户
  2. 调用 `PUT /admin/user/{user_id}/status` 设置为 `banned`
  3. 用户下次登录时应被禁用
  
  ### 退款流程
  1. 用户投诉要求退款
  2. 查询订单信息确认
  3. 调用 `POST /admin/order/{order_no}/refund` 处理退款
  4. 系统自动扣除用户充值时获得的额外算力
  5. 原金额返回给支付渠道（需对接微信/支付宝实现）
  
  ```
  
> ---
  
  ## Summary of Required Actions
  
  **Issue 1:** Add 15 missing Admin schemas to `schemas.py`  
  **Issue 2:** Fix 5 identified bugs:
  - ✅ frozen_points refund (actually safe)
  - ❌ Timezone inconsistency in `daily_sign_in` 
  - ❌ continuous_days calculation off by 1
  - ❌ `get_task_options` hardcoded data
  - ❌ `refund_order` hardcoded 100 points
  
  **Issue 3:** Add admin API documentation section to `api.md`
  
  This should give you a complete plan to address all issues in the AuraKey mod
ule.




wn
---

# B端（管理）API 文档

> **访问权限**：需具有 `aurakey_admin` 角色  
> **前缀**：`/admin`  
> **完整路径示例**：`GET /api/v1/aurakey/admin/dashboard/stats`

---

## 1. 仪表板统计

### 1.1 获取仪表板统计数据

**GET** `/admin/dashboard/stats`

**权限**: `aurakey_admin`

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "today_new_users": 42,
    "today_active_users": 128,
    "today_generations": 356,
    "today_revenue": 15800,
    "revenue_growth_rate": 12.5
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| today_new_users | int | 今日新注册用户数 |
| today_active_users | int | 今日活跃用户数（需登录日志支持） |
| today_generations | int | 今日生图任务总数 |
| today_revenue | int | 今日收入（分，仅计成功订单） |
| revenue_growth_rate | float | 相比昨日收入增长率（百分比） |

---

## 2. 用户管理

### 2.1 调整用户余额

**POST** `/admin/user/adjust-balance`

**权限**: `aurakey_admin`

**请求体**

```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "amount": 100,
  "remark": "补偿已消耗算力"
}
```

**请求参数**

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| user_id | UUID | ✓ | 用户 ID |
| amount | int | ✓ | 调整数量（正数加余额，负数扣余额） |
| remark | string | | 备注说明，会记入流水 |

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_success": true,
    "balance_after": 550
  }
}
```

---

### 2.2 更新用户状态（封禁/解封）

**PUT** `/admin/user/{user_id}/status`

**权限**: `aurakey_admin`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| user_id | UUID | 用户 ID |

**请求体**

```json
{
  "status": "banned"
}
```

**请求参数**

| 字段 | 类型 | 可选值 | 说明 |
|---|---|---|---|
| status | string | `normal`, `banned` | 目标状态 |

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "currentStatus": "banned"
  }
}
```

---

## 3. 订单管理

### 3.1 退款订单

**POST** `/admin/order/{order_no}/refund`

**权限**: `aurakey_admin`

**Path 参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| order_no | string | 订单号 |

**请求体**

```json
{
  "remark": "用户投诉，申请退款"
}
```

**请求参数**

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| remark | string | | 退款原因备注 |

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_success": true,
    "refund_id": "REFODxxxxxxxxxxxxxxxx",
    "deducted_points": 500
  }
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| is_success | bool | 退款是否成功 |
| refund_id | string | 退款流水 ID |
| deducted_points | int | 扣除的算力数（用户充值时赠送的算力） |

**注意**：系统会自动将用户充值时获得的额外算力（bonus）扣回，以防止反复套现。

---

## 4. 配置管理

### 4.1 图库分类管理

#### 4.1.1 获取所有分类

**GET** `/gallery/categories`

**权限**: 无需登录

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "风景",
      "sort": 1
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "name": "人物",
      "sort": 2
    }
  ]
}
```

---

#### 4.1.2 创建分类

**POST** `/admin/gallery/categories`

**权限**: `aurakey_admin`

**请求体**

```json
{
  "name": "抽象艺术",
  "sort": 3
}
```

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_success": true
  }
}
```

---

### 4.2 生图模型管理

#### 4.2.1 获取所有模型

**GET** `/admin/task/options/models`

**权限**: `aurakey_admin`

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "model_id": "pro_v1",
      "name": "专业版 v1.0",
      "cost": 10,
      "is_vip_only": false,
      "status": "on"
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "model_id": "premium_v2",
      "name": "高级版 v2.0",
      "cost": 20,
      "is_vip_only": true,
      "status": "on"
    }
  ]
}
```

---

#### 4.2.2 创建模型

**POST** `/admin/task/options/models`

**权限**: `aurakey_admin`

**请求体**

```json
{
  "model_id": "ultra_v3",
  "name": "超级版 v3.0",
  "cost": 30,
  "is_vip_only": false,
  "status": "on"
}
```

**请求参数**

| 字段 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| model_id | string | ✓ | | 模型唯一标识（如 `pro_v1`） |
| name | string | ✓ | | 模型显示名称 |
| cost | int | ✓ | | 单次使用消耗的算力 |
| is_vip_only | bool | | false | 是否仅限 VIP 使用 |
| status | string | | "on" | 模型状态：`on` 可用，`off` 禁用 |

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_success": true
  }
}
```

---

### 4.3 宽高比管理

#### 4.3.1 获取所有宽高比

**GET** `/admin/task/options/ratios`

**权限**: `aurakey_admin`

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "ratio": "1:1",
      "sort": 1,
      "status": "on"
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "ratio": "16:9",
      "sort": 2,
      "status": "on"
    }
  ]
}
```

---

#### 4.3.2 创建宽高比

**POST** `/admin/task/options/ratios`

**权限**: `aurakey_admin`

**请求体**

```json
{
  "ratio": "3:2",
  "sort": 3,
  "status": "on"
}
```

**请求参数**

| 字段 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| ratio | string | ✓ | | 宽高比（如 `16:9`、`1:1`） |
| sort | int | | 0 | 排序值（越大越靠前） |
| status | string | | "on" | 状态：`on` 可用，`off` 禁用 |

**响应**

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "is_success": true
  }
}
```

---

## 常见操作流程

### 增加新生图模型流程
1. 管理员通过 `POST /admin/task/options/models` 创建新模型
2. 设置 `cost` 为该模型单次消耗的算力
3. 可通过 `is_vip_only` 限制为会员专用
4. 用户通过 `/task/options` 获取可用模型列表（自动过滤 `status=on`）
5. 用户选择模型调用 `POST /task/generate` 生成图片

### 用户冻结/解冻流程
1. 检测到违规用户
2. 调用 `PUT /admin/user/{user_id}/status` 设置为 `banned`
3. 用户下次登录时应被禁用

### 退款流程
1. 用户投诉要求退款
2. 查询订单信息确认
3. 调用 `POST /admin/order/{order_no}/refund` 处理退款
4. 系统自动扣除用户充值时获得的额外算力
5. 原金额返回给支付渠道（需对接微信/支付宝实现）

```

---

## Summary of Required Actions

**Issue 1:** Add 15 missing Admin schemas to `schemas.py`  
**Issue 2:** Fix 5 identified bugs:
- ✅ frozen_points refund (actually safe)
- ❌ Timezone inconsistency in `daily_sign_in` 
- ❌ continuous_days calculation off by 1
- ❌ `get_task_options` hardcoded data
- ❌ `refund_order` hardcoded 100 points

**Issue 3:** Add admin API documentation section to `api.md`

This should give you a complete plan to address all issues in the AuraKey modu
