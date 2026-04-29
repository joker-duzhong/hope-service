# 微信小程序登录接入文档

## 一、配置说明

### 1. WECHAT_APPS 配置格式

**现有配置可以通用**，支持公众号和小程序混合配置：

```bash
# .env 文件
WECHAT_APPS=miniapp_appid:miniapp_secret::,mp_appid:mp_secret:token:aeskey
```

**格式说明**：
- 小程序：`appid:secret::` （token 和 aeskey 留空）
- 公众号：`appid:secret:token:aeskey` （完整配置）
- 多个应用用逗号分隔

**示例**：
```bash
WECHAT_APPS=wx1234567890abcdef:secret123::,wx9876543210fedcba:secret456:mytoken:myaeskey
```

## 二、接口说明

### 1. 小程序登录

**接口**：`POST /api/v1/auth/miniapp/login`

**请求体**：
```json
{
  "appid": "wx1234567890abcdef",
  "code": "081xYz000..."
}
```

**响应**：
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "access_token": "eyJhbGc...",
    "refresh_token": "eyJhbGc...",
    "token_type": "bearer"
  }
}
```

**前端调用示例**：
```javascript
// 1. 获取登录凭证
wx.login({
  success: async (res) => {
    if (res.code) {
      // 2. 发送到后端
      const response = await wx.request({
        url: 'https://your-api.com/api/v1/auth/miniapp/login',
        method: 'POST',
        data: {
          appid: 'wx1234567890abcdef',
          code: res.code
        }
      });
      
      // 3. 保存 token
      wx.setStorageSync('access_token', response.data.data.access_token);
    }
  }
});
```

### 2. 获取并绑定手机号

**接口**：`POST /api/v1/auth/miniapp/phone`

**请求头**：
```
Authorization: Bearer {access_token}
```

**请求体**：
```json
{
  "appid": "wx1234567890abcdef",
  "code": "getPhoneNumber 返回的 code"
}
```

**响应**：
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": "uuid",
    "phone": "13800138000",
    "nickname": "用户昵称",
    "avatar": "头像URL",
    ...
  }
}
```

**前端调用示例**：
```javascript
// 在 button 组件中使用
<button open-type="getPhoneNumber" bindgetphonenumber="getPhoneNumber">
  获取手机号
</button>

// 处理函数
async getPhoneNumber(e) {
  if (e.detail.code) {
    const token = wx.getStorageSync('access_token');
    const response = await wx.request({
      url: 'https://your-api.com/api/v1/auth/miniapp/phone',
      method: 'POST',
      header: {
        'Authorization': `Bearer ${token}`
      },
      data: {
        appid: 'wx1234567890abcdef',
        code: e.detail.code
      }
    });
    
    console.log('手机号绑定成功', response.data.data.phone);
  }
}
```

## 三、与公众号登录的对比

| 特性 | 公众号登录 | 小程序登录 |
|------|-----------|-----------|
| 接口路径 | `/auth/wechat/login` | `/auth/miniapp/login` |
| 授权方式 | OAuth2 网页授权 | wx.login() |
| 获取用户信息 | 自动获取昵称头像 | 需用户主动授权 |
| 获取手机号 | 需短信验证码 | 微信官方接口（免验证码） |
| 配置要求 | appid + secret + token + aeskey | appid + secret |
| unionid | 支持 | 支持 |

## 四、用户数据模型

两种登录方式共用同一个用户表，通过以下字段关联：

- `openid`：微信用户唯一标识（公众号和小程序各自独立）
- `unionid`：同一微信开放平台下的唯一标识（可关联公众号和小程序）
- `phone`：手机号（可选绑定）

**自动注册逻辑**：
1. 首次登录自动创建用户
2. 如果有 unionid，可关联已有账号
3. 手机号绑定后可用于多端登录

## 五、常见问题

### 1. 小程序和公众号能共用同一个账号吗？

可以，前提是：
- 两个应用在同一个微信开放平台下
- 用户授权后能获取到 unionid
- 系统会自动通过 unionid 关联账号

### 2. 获取手机号需要什么权限？

需要在微信小程序后台开通"手机号快速验证组件"权限。

### 3. 为什么小程序获取手机号不需要验证码？

因为使用的是微信官方的 `getPhoneNumber` 接口，微信已经验证过用户身份，所以无需短信验证码。

### 4. 如何测试？

1. 在微信开发者工具中测试
2. 确保 `.env` 中配置了正确的 appid 和 secret
3. 使用真实的小程序 appid（测试号可能无法获取手机号）

## 六、安全建议

1. **不要在前端暴露 secret**：secret 只能在后端使用
2. **code 只能使用一次**：每次登录都需要重新获取 code
3. **token 过期处理**：access_token 默认 24 小时过期，需要用 refresh_token 刷新
4. **HTTPS 传输**：生产环境必须使用 HTTPS
