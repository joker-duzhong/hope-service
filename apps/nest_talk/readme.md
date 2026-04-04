语筑 迁移功能点清单
项目概述
这是一个成都房价监控与智能推荐系统，目前架构：

后端: FastAPI + SQLAlchemy + OpenAI API
前端: 无独立前端（通过 Swagger UI 操作）
交互方式: 原本计划通过微信公众号对话 → 现需改为前端直接交互
一、后端功能清单
1. AI 智能对话服务
功能点	说明	关键文件
自然语言对话	与用户进行购房咨询对话	ai_service.py
需求提取	从用户输入提取结构化购房需求（预算、面积、区域等）	extract_requirements()
追问生成	当需求不明确时，自动生成追问	ask_clarification()
上下文管理	保持对话历史，支持多轮对话	conversation_history
提取的需求字段:

budget_min/budget_max - 预算范围（万元）
area_min/area_max - 面积范围（㎡）
rooms - 居室数量
regions - 目标区域列表
exclude_top_floor/exclude_ground_floor - 排除顶楼/底楼
floor_min/floor_max - 楼层范围
orientations - 朝向偏好
2. 智能匹配服务
功能点	说明	关键文件
意图识别	判断用户需求是否足够明确	matching.py
房源搜索	根据需求查询数据库	_search_houses()
结果整合	整合 AI 回复和房源列表	process_user_query()
API 端点: POST /chat

响应类型:

clarification - 需求不明确，返回追问
results - 返回匹配的房源列表
3. 房源管理 API
端点	方法	功能
/houses/search	POST	多条件搜索房源
/houses/{id}	GET	获取房源详情
/houses/bargain/list	GET	获取捡漏房源列表
搜索条件 (house.py:62-91):

预算范围、面积范围、居室数
区域列表、楼层筛选、朝向偏好
分页参数
4. 用户偏好管理 API
端点	方法	功能
/user/preferences	POST	创建用户偏好
/user/preferences/{user_id}	GET	获取用户偏好
/user/preferences/{user_id}	PUT	更新用户偏好
/user/preferences/{user_id}	DELETE	删除用户偏好
用户偏好字段 (user.py):


# 预算
budget_min, budget_max      # 万元

# 面积
area_min, area_max          # ㎡

# 居室
rooms_min, rooms_max        # 居室数

# 区域
preferred_regions           # 逗号分隔的区域名

# 楼层
exclude_top_floor           # 排除顶楼
exclude_ground_floor        # 排除底楼
floor_min, floor_max        # 楼层范围

# 朝向
preferred_orientations      # 偏好朝向

# 捡漏设置
bargain_enabled             # 启用捡漏推送
bargain_threshold           # 折扣阈值（如0.9表示90%）

# 通知
notify_endpoint             # 推送接收地址
5. 报表服务 API
端点	方法	功能
/reports/daily	GET	获取每日行情报表图片
/reports/list	GET	列出所有可用报表
参数: region, community, days

6. 数据爬虫服务
功能	说明	关键文件
区域均价抓取	抓取各区域平均房价	beike.py
房源列表抓取	按区域抓取在售房源	scrape_listings_by_region()
小区房源抓取	抓取指定小区的房源	scrape_community_listings()
支持区域:

高新区、天府新区、锦江区、青羊区
武侯区、成华区、金牛区、双流区
温江区、郫都区、龙泉驿区、新都区
7. 数据管道服务
功能	说明	关键文件
数据清洗入库	处理爬虫数据并存入数据库	data_pipeline.py
增量更新	根据 house_id 更新已有房源	upsert_house()
关联处理	自动关联区域、小区、房源	run_full_pipeline()
8. 定时调度服务
功能	说明	关键文件
定时抓取	每日定时执行数据抓取	scheduler.py
配置化	通过环境变量配置执行时间	scraper_cron_hour/minute
9. 捡漏检测服务
功能	说明	关键文件
均价计算	计算小区平均单价	bargain.py
折扣判断	判断房源是否低于均价	detect_bargain_houses()
状态更新	更新房源捡漏标记	update_bargain_status()
个性化推荐	根据用户偏好筛选捡漏房	get_bargain_houses_for_user()
10. 通知推送服务
渠道	说明	关键文件
WxPusher	微信公众号推送	notification.py
Webhook	自定义 Webhook	WebhookChannel
日志	调试用日志输出	LogChannel
通知类型:

捡漏房源提醒 send_bargain_alert()
每日行情报告 send_daily_report()
11. 数据分析服务
功能	说明	关键文件
价格历史	获取区域/小区均价走势	analytics.py
涨跌幅计算	计算环比变化	calculate_price_change()
统计汇总	获取房源统计信息	get_house_statistics()
价格分布	计算价格区间分布	get_price_distribution()
12. 数据可视化服务
功能	说明	关键文件
图表生成	生成价格走势图等	visualization.py
导出文件	保存到 exports 目录	-
二、前端功能清单（需新建）
核心改动: 将原本通过微信公众号的对话交互改为前端直接交互

1. AI 对话模块（核心功能）
功能点	描述	对应后端 API
聊天界面	类似微信的对话界面	POST /chat
消息发送	发送用户购房需求	POST /chat
追问回复	显示 AI 追问，用户继续回答	POST /chat
房源展示	当需求明确时展示匹配房源	POST /chat (返回 houses 字段)
会话管理	使用 session_id 保持上下文	POST /chat (带 session_id)
清除会话	重新开始对话	POST /chat/clear
UI 设计建议:


┌────────────────────────────────────┐
│  🏠 语筑 - 智能房产顾问            │
├────────────────────────────────────┤
│                                    │
│  🤖 AI: 您好！请问您的预算是多少？ │
│                                    │
│  👤 我: 我预算200万左右，想在高新 │
│        区买一套两居室              │
│                                    │
│  🤖 AI: 为您找到 15 套符合条件的  │
│        房源：                      │
│        ┌──────────────────────┐   │
│        │ [房源卡片列表]       │   │
│        └──────────────────────┘   │
│                                    │
├────────────────────────────────────┤
│ [输入框]                    [发送] │
└────────────────────────────────────┘
2. 房源搜索模块
功能点	描述	对应后端 API
高级搜索	表单式多条件搜索	POST /houses/search
房源列表	展示搜索结果列表	POST /houses/search
房源详情	查看房源详细信息	GET /houses/{id}
筛选条件	预算、面积、区域、楼层、朝向	搜索请求参数
分页	支持分页加载	page, page_size
3. 捡漏房源模块
功能点	描述	对应后端 API
捡漏列表	展示高性价比房源	GET /houses/bargain/list
折扣显示	显示折扣率和节省金额	响应中的 discount_rate, save_amount
推荐理由	展示捡漏原因	响应中的 bargain_reason
4. 用户中心模块
功能点	描述	对应后端 API
偏好设置	设置购房偏好	POST/PUT /user/preferences
预算范围	设置最低/最高预算	budget_min/max
区域偏好	选择目标区域	preferred_regions
楼层偏好	楼层筛选条件	exclude_top_floor 等
捡漏设置	捡漏推送开关和阈值	bargain_enabled/threshold
5. 数据报表模块
功能点	描述	对应后端 API
行情图表	展示价格走势图	GET /reports/daily
区域选择	选择查看特定区域	?region=xxx
报表列表	列出历史报表	GET /reports/list
三、数据模型对照
房源

id              - 主键
house_id        - 房源唯一ID（来源平台）
title           - 房源标题
total_price     - 总价（万元）
unit_price      - 单价（元/㎡）
area            - 建筑面积（㎡）
layout          - 户型（如：3室2厅）
rooms           - 居室数
floor           - 所在楼层
total_floors    - 总楼层
orientation     - 朝向
decoration      - 装修情况
region_name     - 区域名称
community_id    - 所属小区ID
source          - 来源平台
url             - 房源链接
image_url       - 房源图片
is_bargain      - 是否捡漏房
bargain_reason  - 捡漏原因
用户画像

user_id         - 用户ID
openid          - 微信OpenID（迁移后可移除）
budget_min/max  - 预算范围
area_min/max    - 面积范围
rooms_min/max   - 居室范围
preferred_regions       - 偏好区域
exclude_top_floor       - 排除顶楼
exclude_ground_floor    - 排除底楼
floor_min/max           - 楼层范围
preferred_orientations  - 偏好朝向
bargain_enabled         - 启用捡漏推送
bargain_threshold       - 捡漏阈值
notify_endpoint         - 推送地址
四、迁移要点
需要修改的部分
原功能	修改方案
微信公众号对话	→ 前端聊天界面（调用 /chat API）
微信用户身份	→ 自建用户系统或第三方登录
WxPusher 推送	→ 前端站内通知 / 浏览器推送
openid 字段	→ 可选保留或改为其他标识
后端建议保留
所有 REST API 端点基本不变
AI 对话逻辑不需要改动
数据爬虫和分析服务不需要改动
需要新建
组件	技术建议
前端应用	Vue 3 / React + TypeScript
UI 组件库	Ant Design / Element Plus
状态管理	Pinia / Zustand
聊天界面	可参考 ChatGPT 界面风格
用户认证	JWT / OAuth（可选）
五、API 端点汇总

# 健康检查
GET  /health

# AI 对话
POST /chat              # 智能对话
POST /chat/clear        # 清除会话

# 房源
POST /houses/search     # 搜索房源
GET  /houses/{id}       # 房源详情
GET  /houses/bargain/list  # 捡漏房源

# 用户
POST /user/preferences  # 创建偏好
GET  /user/preferences/{user_id}   # 获取偏好
PUT  /user/preferences/{user_id}   # 更新偏好
DELETE /user/preferences/{user_id} # 删除偏好

# 报表
GET  /reports/daily     # 每日报表
GET  /reports/list      # 报表列表