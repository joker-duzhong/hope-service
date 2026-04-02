# Trade Copilot API 文档

本文档是 `trade_copilot` 模块的 API 接口说明，主要供前端开发和 AI Assistant 参考使用。

所有接口均带有前缀路径（由 FastAPI 注册时决定，通常可能是 `/api/trade_copilot` 或类似），且需要传递用户鉴权相关信息（Headers: Authorization Bearer token）。

所有接口返回格式统一为 `ResponseModel`:
```json
{
  "code": 200,
  "message": "success",
  "data": { ... } // 具体数据内容
}
```

---

## 1. 基础行情与风控

### 1.1 大盘红绿灯状态
**获取大盘（上证/深证）当前的防守状态。**
*   **接口：** `GET /market/status`
*   **功能：** 获取上证和深证是否跌破 20日线的状态标识 (`red` 线之上 / `green` 跌破)，以及简要的原因描述。

### 1.2 市场温度计与板块轮动
**获取全市场赚钱效应打分和情绪周期**
*   **接口：** `GET /market/thermometer`
*   **功能：** 获取涨跌停家数、全市场赚钱效应打分(0-100)、情绪周期(冰点/分歧/高潮 等)及当天领涨的五大行业板块。常用于前端的“驾驶舱/Dashboard”顶部展示。

### 1.3 ST 股票黑名单
**获取全市场 ST 股票代码列表**
*   **接口：** `GET /market/st-list`
*   **功能：** 在添加观察池或买入时进行风险阻断。

---

## 2. 实盘持仓管理 (Positions)

### 2.1 新增买入持仓
*   **接口：** `POST /positions`
*   **参数：** 股票代码、名称、买入日期、成本价、买入数量等。
*   **说明：** 买入时会自动在流水记录生成一笔初始买入流水(buy)。

### 2.2 查询持仓列表
*   **接口：** `GET /positions`
*   **参数：** `status` (可选): `holding` (当前持仓) 或 `closed` (已清仓)。

### 2.3 更新持仓 (如标记卖出记录)
*   **接口：** `PUT /positions/{position_id}`
*   **参数：** 持仓 ID。可用于修改状态(`status`)，或更新盘后最高价。

### 2.4 删除持仓记录
*   **接口：** `DELETE /positions/{position_id}`
*   **说明：** 逻辑删除该持仓。

---

## 3. 交易流水记录 (BS点流水)
**V2.0新增，每笔具体买入/卖出的动作记录。**

### 3.1 添加交易流水
*   **接口：** `POST /positions/{position_id}/transactions`
*   **功能：** 记录加仓或减仓，包含字段：操作类型(buy/sell)，交易价格，交易数量。

### 3.2 查看某持仓下流水
*   **接口：** `GET /positions/{position_id}/transactions`

---

## 4. 观察池/狙击雷达 (Watchlist)

### 4.1 加入观察池
*   **接口：** `POST /watchlist`
*   **说明：** 如果这只股票处于 ST 名单中，接口将直接报错拒绝。

### 4.2 查询/修改/删除观察池
*   **列表查询：** `GET /watchlist`
*   **更新记录：** `PUT /watchlist/{watchlist_id}`
*   **删除记录：** `DELETE /watchlist/{watchlist_id}`

---

## 5. 交易日记反馈 (Trading Journal)
**V2.0新增，每天记录自己的情绪评分与主观犯错，做到知行合一。**

*   **创建日志：** `POST /journals`
*   **日志列表：** `GET /journals`
*   **修改日志：** `PUT /journals/{journal_id}`

---

## 6. 仓位资金管理 (Capital Settings)
**V2.0新增，设置总可用本金，可搭配板块分数自动计算凯利仓位。**

*   **获取资金设置：** `GET /settings/capital`
*   **更新资金设置：** `PUT /settings/capital` 

---

## 7. 策略管理引擎 (Trade Strategy)
**V2.0新增，动态配置个人的止盈和止损网格比例。**

*   **创建策略：** `POST /strategies`
*   **策略列表：** `GET /strategies`
*   **修改策略：** `PUT /strategies/{strategy_id}`
*   **废弃策略：** `DELETE /strategies/{strategy_id}`

---

## 8. 工具调试

*   **飞书 Webhook 调试**
    *   `POST /feishu-test`
    *   **参数：** 标题 `title`、内容 `msg`。
    *   **功能：** 可直接测试后端的飞书机器人推送链路。
