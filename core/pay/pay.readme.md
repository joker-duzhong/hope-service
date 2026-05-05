# 💰 Core 支付模块 (core/pay)

本模块是对外部第三方支付网关（微信支付、支付宝支付）的基础封装层。它隶属于系统的 `core` 底座组件，为上层的业务 `apps` 提供纯净、异步、易用的统一抽象层。

## ⚙️ 必填配置项一览 (`core/config.py` 中的 `Settings` 需补充)

在应用根目标的 `.env` 或 `core/config.py` 中，需要加入以下这些核心参数，建议将其维护在 Pydantic Settings 中通过依赖注入：

### 🟢 微信支付 (Wechat Pay)
* `WECHAT_PAY_APP_ID`: 你的微信小程序 AppID (如果是服务号和 APP 需配置主客 AppID)
* `WECHAT_PAY_MCH_ID`: 微信支付商户号
* `WECHAT_PAY_API_V3_KEY`: API v3 秘钥
* `WECHAT_PAY_CERT_SN`: 微信商户证书序列号 (十分重要，需在商户后台查看)
* `WECHAT_PAY_PRIVATE_KEY`: 微信商户 API 私钥纯文本内容 (因含有换行，可直接写成多行或单行带 \n)
* `WECHAT_PAY_NOTIFY_URL`: 系统默认接收微信回调的 webhook 地址（例如 `https://api.yourdomain.com/v1/trade/wechat-notify`）

### 🔵 支付宝 (AliPay)
* `ALIPAY_APP_ID`: 支付宝应用编号 AppID
* `ALIPAY_PRIVATE_KEY`: 应用私钥 (如果是文件可配路径，字符串则直接配置)
* `ALIPAY_PUBLIC_KEY`: 支付宝公钥 (用于验证回调和请求包的安全)
* `ALIPAY_GATEWAY`: `https://openapi.alipay.com/gateway.do` (如果是测试沙箱，则使用沙箱网关)
* `ALIPAY_NOTIFY_URL`: 系统默认接收支付宝回调的 webhook 地址

---

## ⚡ 当前支持的支付通道

本模块严格执行异步规范（`async def`），以确保核心 I/O 调用绝不阻塞 FastAPI 事件循环主进程。

### 1. 微信支付 `WechatPayClient`
* ✅ **小程序支付 (JSAPI)**: 直接下发供前端 wx.requestPayment 唤起收银台的五参或六参模型。
* ✅ **扫码支付 (Native)**: 返回 `code_url`，供前端自己生成供用户扫码的二维码。
* ❌ *预留接入 (NotImplemented):* 外部 H5、独立 APP、服务号 JSAPI。

### 2. 支付宝支付 `AliPayClient`
* ✅ **手机网站支付 (Wap/H5)**: 返回拼接好的重定向跳转 URL，前端将页面 `window.location.href` 重定向或放到 `iframe` 内唤起支付。
* ✅ **PC 网页支付 (Page)**: 返回拼接好的重定向跳转 URL，供 PC 浏览器直接跳转到支付宝收银台。
* ✅ **扫码支付 (Precreate)**: 返回 `qr_code` 在线链接，前端生成用户扫码即付的二维码。
* ❌ *预留接入 (NotImplemented):* 独立 APP 客户端支付。

---

## 🛠️ 食用方法

由于 `core` 仅负责通用工具集成，使用时应当在你各自的 `apps.your_app.services.py` 内部引入并使用，千万不要在底座包含具体的业务订单逻辑，且请不要违规污染 Router 层。

```python
from core.pay.schemas import WechatPayMiniRequest, AliPayWapRequest
from core.pay.wechat_pay import WechatPayClient
from core.pay.ali_pay import AliPayClient

async def create_trade_order(user_id: int, item_id: int):
    # 1. 你的创建本地业务订单逻辑...
    order_no = "ORD123456"
    amount_in_cents = 1000 # 10元
    
    # 2. 调用支付基座封装
    wechat_client = WechatPayClient()
    req = WechatPayMiniRequest(
        order_id=order_no,
        amount=amount_in_cents, 
        subject="测试商品",
        openid="oUpF8uMuAJO_M2pxb1Q9zNjWeS6o"
    )
    
    # 3. 得到响应 (不会阻塞事件循环)
    response = await wechat_client.create_mini_program_order(req)
    if response.success:
        return response.pay_data # 丢给前端小程序直接拉起
```

## 🚨 开发预警 (遵循大纲规范)

1. **金额单位统一**：无论支付宝微信，咱们后端进单的 request 对象金额字段统一都叫 `amount`，**统一以“分”(整型/int) 结算！** 支付宝接口要求的“元”会在 `AliPayClient` 底层自动被 `/100` 处理转换，请业务方不要自作主张提前转换从而引发记账精度 Bug。
2. **底层安全与异步策略**：当前支付底层不仅已经完全接入真正的原生库 (`pycryptodome`) 进行纯计算 RSA 加密与验签（移除了所有伪代码），还全面使用了 `httpx.AsyncClient` 实现了非阻塞的网络 I/O。无论后期接入何种新支付接口，请务必维持原生的异步非阻塞特性。千万别使用 `requests` 阻挡总部的 FastAPI 引擎主事件循环！
 