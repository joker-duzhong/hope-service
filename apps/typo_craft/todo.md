明白！你的业务设计非常贴合实际的商业 AI 绘图产品架构（如 Midjourney、智谱等提供商的标准流程）：**主线程处理 Prompt + 提交三方任务，前端轮询驱动状态机推进，重型后台任务仅处理异步的附加价值（如打标签）。**

基于这些最新的调整，并且去掉了不再需要的翻译和系统底座部分，以下是为你量身定制的**最新 TypoCraft (言图) 核心业务开发 Todo List**。

---

### 📦 第一阶段：数据库建模 (`models.py`)
> *约束：全部继承基础 `CoreModel`，带有 `typo_craft_` 前缀，严格按照 SQLAlchemy 2.0 规范。*

*   [ ] **1.1 创建 `typo_craft_projects` 表 (App 视觉方案项目)**
    *   **业务目标**: 存储“系列插图”功能中的全局画风基调。
    *   **核心字段**: 
        *   `user_id` (所属用户)
        *   `project_name` (App或项目名称)
        *   `scene_desc` (应用场景描述)
        *   `base_style_prompt` (Agent 1 生成的全局画风锚点，用于后续拼接)
*   [ ] **1.2 创建 `typo_craft_assets` 表 (统一图片资产库)**
    *   **业务目标**: 统一管理“单张海报”和“项目系列插图”的所有生成记录与状态。
    *   **核心字段**: 
        *   `user_id` (所属用户)
        *   `project_id` (外键关联 projects，如果是单张海报则为 `NULL`)
        *   `asset_type` (枚举: `POSTER` 海报, `UI_ILLUSTRATION` 系列插图)
        *   `status` (枚举: `PENDING` 执行中, `SUCCESS` 成功, `FAILED` 失败, `REJECTED` 审核违规)
        *   `provider_task_id` (第三方绘图服务商返回的原始任务ID，用于轮询凭证)
        *   `image_url` (成功后填入的图片地址)
        *   `user_prompt` (用户的原始需求 / 具体场景)
        *   `final_ai_prompt` (发给画图模型的最终指令：海报为Agent生成的提示词；插图为 base_style + 场景拼接的提示词)
        *   `tags` (JSONB类型，初始为空，由自动任务后续打标)
        *   `is_public` (布尔值，是否展示在发现广场)

---

### 🛡️ 第二阶段：数据校验与序列化 (`schemas.py`)
> *约束：使用 `Pydantic V2`，输入输出严格分离。*

*   [ ] **2.1 编写 `Project` 相关的 In/Out Schema**
    *   `ProjectCreateIn`: `{ project_name, scene_desc }`
    *   `ProjectOut`: 包含 `id`, `base_style_prompt` 等。
*   [ ] **2.2 编写 `Asset` 生成与查询 Schema**
    *   `AssetGeneratePosterIn`: `{ prompt (用户想要的海报描述), aspect_ratio }`
    *   `AssetGenerateUIIn`: `{ project_id (必填), scene_prompt (当前所需页面的描述), aspect_ratio }`
    *   `AssetStatusOut`: `{ id, status, image_url, final_ai_prompt }` (供轮询返回)
*   [ ] **2.3 编写审核/状态修改 Schema**
    *   `AssetStatusUpdateIn`: `{ status (如修改为REJECTED), is_public }`

---

### 🧠 第三阶段：核心逻辑服务与三方请求 (`services.py` & `ai_clients.py`)
> *约束：封装 LLM 与画图服务商的请求逻辑，数据库操作必须按序执行，严禁在 Controller 裸写。*

*   [ ] **3.1 AI 客户端封装 (`ai_clients.py`)**
    *   **逻辑**: 使用 `httpx.AsyncClient` 封装对大模型（生成 Prompt）和画图服务商的调用。
    *   实现 `async def submit_image_generation(...) -> str`: 提交画图，返回 `provider_task_id`。
    *   实现 `async def check_image_status(provider_task_id: str) -> dict`: 请求服务商轮询接口，返回 `{ status, url }`。
*   [ ] **3.2 创建 App 项目与锚点提取 (`services.py`)**
    *   **逻辑 `create_project`**: 接收前端需求 -> 请求大模型 Agent 1 -> 获取 `base_style_prompt` -> 写入 `projects` 表 -> 返回给前端。
*   [ ] **3.3 生成任务提交逻辑 (`services.py`)**
    *   **逻辑 `submit_generation`**:
        1.  如果是 `POSTER`: 用海报 Prompt Agent 处理用户输入得到 `final_ai_prompt`。
        2.  如果是 `UI_ILLUSTRATION`: 取出 project 的 `base_style_prompt`，使用 Agent 2 将其与当前 `scene_prompt` 融合得到 `final_ai_prompt`。
        3.  拿着 `final_ai_prompt` 提交给绘图服务商 API，获取 `provider_task_id`。
        4.  在 `assets` 表中建立占位记录，写入 `provider_task_id`，`status` 设置为 `PENDING`。
        5.  返回数据库的 `asset_id`。
*   [ ] **3.4 状态轮询与落库更新 (`services.py`)**
    *   **逻辑 `sync_asset_status`**:
        1.  前端根据 `asset_id` 发起查询。后端查库，如果状态已经是 `SUCCESS/FAILED`，直接返回。
        2.  如果库里还是 `PENDING`，则去查三方服务商接口。
        3.  如果三方返回成功，将 `image_url` 更新到库中，`status` 改为 `SUCCESS`；三方失败则改库为 `FAILED`。
        4.  返回最新状态给前端。
*   [ ] **3.5 后台管理：手动修改状态 (`services.py`)**
    *   **逻辑 `admin_update_status`**: 传入 `asset_id`，强制覆写 `status`（用于隐藏违规图片或人工兜底）。

---

### 🌐 第四阶段：API 控制器与路由 (`router.py`)
> *约束：只负责参数接收、鉴权、调用 Service 和包装 ResponseModel。*

*   [ ] **4.1 项目(App)管理接口**
    *   `POST /projects` -> 接收概念，生成全局风格并建档。
    *   `GET /projects` -> 获取当前用户的所有 App 视觉项目列表。
*   [ ] **4.2 生成提交接口 (入口)**
    *   `POST /generate/poster` -> 返回 `{ task_id: asset_id }`。
    *   `POST /generate/illustration` -> 依赖 `project_id`，返回 `{ task_id: asset_id }`。
*   [ ] **4.3 轮询查询接口 (出口)**
    *   `GET /assets/{asset_id}/status` -> 前端每隔2-3秒调用一次，触发后端检查更新。
*   [ ] **4.4 广场与审核接口**
    *   `GET /feed` -> 瀑布流查询 `status='SUCCESS'` 且 `is_public=True` 且未违规的数据。
    *   `PATCH /assets/{asset_id}` -> 修改可见性或人工强制修改状态。

---

### ⚙️ 第五阶段：纯后台自动打标任务 (`tasks.py`)
> *约束：利用 Celery Beat / 定时任务，每分钟或每 5 分钟扫表一次，解耦主业务。*

*   [ ] **5.1 编写自动打标任务 `auto_tag_successful_assets`**
    *   **逻辑**:
        1. 查 `assets` 表中 `status == 'SUCCESS'` 且 `tags IS NULL` 的记录，限制每次处理 20-50 条（防 OOM 阻塞）。
        2. 遍历这些记录，提取 `final_ai_prompt`，批量或逐个发送给 LLM（打标 Agent）。
        3. LLM 结构化返回 JSON 标签数组。
        4. 更新 `assets` 表，存入 `tags` 字段（如 `["极简风", "3D", "断网页面"]`）。

---

### 🤖 附录：自动化任务 - AI 打标 Agent 提示词。 

针对 **第五阶段** 的后台自动打标任务，你需要使用这个结构化的 System Prompt 调用大模型：
 ./prompts.py/UI_Illustrator