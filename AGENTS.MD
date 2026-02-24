# AGENTS.MD — Savory Canvas 项目编码指南

> **版本：** 1.0.0
> **最后更新：** 2026-02-24
> **适用对象：** 所有参与本项目开发的 AI Agent 与人类工程师

---

## 1. 项目概览

**Savory Canvas** 是一款面向个人创作者的本地图文生成桌面工具。用户在"灵感对话"中输入文本、上传图片或上传视频（支持混合提交），系统自动完成 **视频转写 → 灵感收敛 → 风格草案确定 → 资产分配 → 图片生成 → 文案生成 → 导出**，最终产出可直接发布的单篇图文帖。

### 1.1 核心业务域

| 领域                      | 说明                                               |
| ------------------------- | -------------------------------------------------- |
| 会话（Session）           | 一次完整创作上下文容器                             |
| 灵感对话（Inspiration）   | 素材输入与风格收敛的一体化对话流程                 |
| 素材（Asset）             | 用户输入或中间产物（文本、图片、视频、转写文本等） |
| 风格模板（StyleProfile）  | 约束画风、背景、氛围、张数等参数                   |
| 生成任务（GenerationJob） | 异步执行链路实例，包含状态机                       |
| 导出任务（ExportTask）    | 长图 / PDF 异步导出流程                            |
| 提供商与模型路由          | 文字模型 / 生图模型的配置与能力校验                |

### 1.2 产品形态

- **部署方式：** Windows 本地桌面应用（Python 打包 exe）
- **架构模式：** 前后端分离单体，通过 HTTP API 通信
- **数据库：** SQLite（本地持久化）
- **唯一契约源：** `docs/OPENAPI.JSON`

---

## 2. 技术栈

### 2.1 后端

| 类别     | 选型                                 |
| -------- | ------------------------------------ |
| 语言     | Python 3.10+                         |
| 框架     | FastAPI 0.115.x                      |
| 服务器   | Uvicorn 0.34.x                       |
| 数据校验 | Pydantic 2.10.x                      |
| 文件上传 | python-multipart 0.0.20              |
| 数据库   | SQLite（标准库 `sqlite3`，原生 SQL） |
| 测试     | pytest 8.3.x                         |

### 2.2 前端

| 类别        | 选型                           |
| ----------- | ------------------------------ |
| 语言        | TypeScript 5.9.x               |
| 框架        | React 19.x                     |
| 构建        | Vite 7.x                       |
| 状态管理    | Zustand 5.x                    |
| HTTP 客户端 | Axios 1.x                      |
| 图标        | Lucide React                   |
| 代码质量    | ESLint 9.x + typescript-eslint |

### 2.3 运行端口

| 服务            | 地址                               |
| --------------- | ---------------------------------- |
| 后端 API        | `http://127.0.0.1:8887`            |
| 前端 Dev Server | `http://localhost:7778`            |
| 静态资源        | `http://127.0.0.1:8887/static/...` |

---

## 3. 目录结构

```
Savory_Canvas/
├── AGENTS.MD                         # 本文件：编码指南
├── pytest.ini                        # pytest 全局配置
├── docs/                             # 文档中心
│   ├── PRD.MD                        #   产品需求文档
│   ├── BACKEND.MD                    #   后端开发说明
│   ├── FRONTED.MD                    #   前端开发说明
│   ├── OPENAPI.JSON                  #   唯一 API 契约源
│   └── plans/                        #   设计文档与迭代计划
│
├── backend/                          # 后端代码根目录
│   ├── __init__.py
│   ├── requirements.txt              #   Python 依赖
│   ├── pytest.ini                    #   后端测试配置
│   ├── error_codes.md                #   错误码速查表
│   ├── migrations/                   #   数据库迁移脚本
│   │   └── 001_init.sql
│   ├── data/                         #   SQLite 数据库文件（运行时生成）
│   ├── storage/                      #   生成文件存储目录（图片、导出文件等）
│   ├── tests/                        #   自动化测试
│   │   ├── conftest.py               #     测试夹具与通用 Fixture
│   │   ├── test_session_asset.py
│   │   ├── test_style_generation.py
│   │   ├── test_generation_flow.py
│   │   ├── test_generation_worker.py
│   │   ├── test_inspiration_flow.py
│   │   ├── test_export_provider_model.py
│   │   └── test_cors.py
│   └── app/                          #   应用主目录
│       ├── __init__.py
│       ├── main.py                   #     FastAPI 应用工厂 & 异常处理
│       ├── core/                     #     核心配置层
│       │   ├── errors.py             #       DomainError 与错误码定义
│       │   ├── settings.py           #       Settings 数据类 & 环境变量
│       │   └── utils.py              #       通用工具函数
│       ├── domain/                   #     领域枚举与常量
│       │   └── enums.py
│       ├── infra/                    #     基础设施层
│       │   ├── db.py                 #       SQLite 数据库连接与迁移引擎
│       │   └── storage.py            #       文件存储抽象
│       ├── schemas/                  #     Pydantic 请求 / 响应模型
│       │   ├── request.py
│       │   └── response.py
│       ├── repositories/             #     数据访问层（原生 SQL）
│       │   ├── session_repo.py
│       │   ├── asset_repo.py
│       │   ├── inspiration_repo.py
│       │   ├── style_repo.py
│       │   ├── job_repo.py
│       │   ├── result_repo.py
│       │   ├── export_repo.py
│       │   ├── provider_repo.py
│       │   └── config_repo.py
│       ├── services/                 #     业务逻辑层
│       │   ├── container.py          #       ServiceContainer 聚合
│       │   ├── session_service.py
│       │   ├── asset_service.py
│       │   ├── inspiration_service.py
│       │   ├── style_service.py
│       │   ├── generation_service.py
│       │   ├── export_service.py
│       │   ├── provider_service.py
│       │   └── model_service.py
│       ├── workers/                  #     异步任务执行层
│       │   ├── transcript_worker.py  #       视频转写
│       │   ├── generation_worker.py  #       图文生成
│       │   └── export_worker.py      #       导出
│       └── api/                      #     HTTP 路由层
│           ├── router.py             #       路由注册总入口
│           ├── deps.py               #       依赖注入
│           └── v1/                   #       v1 版本路由
│               ├── session.py
│               ├── asset.py
│               ├── inspiration.py
│               ├── style.py
│               ├── generation.py
│               ├── export.py
│               ├── provider.py
│               └── model.py
│
└── frontend/                         # 前端代码根目录
    ├── package.json
    ├── vite.config.ts                #   Vite 配置（端口 7778）
    ├── tsconfig.json
    ├── tsconfig.app.json
    ├── tsconfig.node.json
    ├── eslint.config.js
    ├── index.html                    #   HTML 入口
    ├── public/                       #   静态资源
    └── src/
        ├── main.tsx                  #   React 入口
        ├── App.tsx                   #   主布局（三栏）
        ├── App.css                   #   应用级样式
        ├── index.css                 #   全局样式 & 设计系统
        ├── api.ts                    #   Axios 封装 & TypeScript 接口类型
        ├── store.ts                  #   Zustand 全局状态管理
        └── components/               #   UI 组件
            ├── SessionPanel.tsx      #     会话列表
            ├── InspirationPanel.tsx  #     灵感对话面板（中间区）
            ├── GeneratePanel.tsx     #     生成任务与进度
            ├── ResultPanel.tsx       #     结果展示与文案
            ├── ExportPanel.tsx       #     导出功能
            ├── ModelPanel.tsx        #     模型设置弹窗
            ├── StyleChatPanel.tsx    #     风格对话（兼容旧流程）
            ├── StyleManagementDrawer.tsx  # 风格模板管理抽屉
            ├── AssetInputPanel.tsx   #     素材输入（兼容旧流程）
            └── ToastContainer.tsx    #     全局 Toast 通知
```

---

## 4. 架构分层

后端严格遵循**四层架构**，禁止跨层调用：

```
┌────────────────────────────────┐
│  API 层 (api/v1/*.py)          │  输入校验、输出序列化、错误码映射
├────────────────────────────────┤  ↕ 仅调用 Service 层
│  Service 层 (services/*.py)    │  业务编排、状态推进、模型调用、降级
├────────────────────────────────┤  ↕ 仅调用 Repository / Worker / Infra
│  Repository 层 (repositories/) │  SQL 与数据映射，持久化
├────────────────────────────────┤
│  Worker 层 (workers/*.py)      │  长耗时异步任务执行（转写/生成/导出）
├────────────────────────────────┤
│  Infra 层 (infra/*.py)         │  数据库连接、文件存储
└────────────────────────────────┘
```

**依赖注入：** 通过 `main.py` 的 `create_app()` 工厂函数手动组装所有 Repository → Service → Worker → API 依赖链，并挂载到 `app.state.services`（`ServiceContainer`）。API 路由通过 `api/deps.py` 中的依赖函数获取服务实例。

---

## 5. 编码规范（必须遵守）

### 5.1 硬性约束

| 规则           | 限制                                               |
| -------------- | -------------------------------------------------- |
| 单函数行数     | **< 200 行**                                       |
| 单文件行数     | **< 1000 行**                                      |
| 单文件夹文件数 | **≤ 10 个**（不含 `__pycache__` 等缓存）           |
| 文件编码       | **UTF-8**（无 BOM）                                |
| 超限处理       | **必须拆分文件或模块，禁止删除健壮性代码来凑行数** |

### 5.2 后端 Python 规范

1. **类型注解：** 所有公开函数必须有完整的类型签名（参数 + 返回值）。
2. **`from __future__ import annotations`：** 所有 `.py` 文件第一行。
3. **命名风格：**
   - 文件名：`snake_case.py`
   - 类名：`PascalCase`
   - 函数/变量：`snake_case`
   - 常量：`UPPER_SNAKE_CASE`
4. **错误处理：** 业务异常一律抛出 `DomainError`，由全局异常处理器统一转换为标准 JSON 响应。禁止在 API 层 `try/except` 后返回自定义格式。
5. **数据库：** Repository 层全部使用原生 SQL + 参数化查询，禁止字符串拼接。对话状态和消息必须持久化到 SQLite，**禁止纯内存态**。
6. **异步策略：** 长耗时操作（视频转写、图文生成、导出）必须放到 Worker 层，通过后台线程 + `asyncio.run` 执行。API 接口立即返回任务 ID 供前端轮询，**禁止同步阻塞返回完整结果**。
7. **模块边界：**
   - API 层不写业务算法，只做输入校验 + 输出序列化 + 错误码映射。
   - Service 层不写 SQL。
   - Repository 层不引用 Service 层。

### 5.3 前端 TypeScript 规范

1. **严格模式：** `tsconfig.app.json` 开启 `strict: true`。
2. **组件风格：** 函数式组件 + Hooks，禁止 Class 组件。
3. **状态管理：** 全局状态集中在 `store.ts`（Zustand），组件内部仅管理 UI 交互状态。
4. **API 调用：** 统一通过 `api.ts` 封装的函数，禁止组件内直接 `axios.get(...)`。
5. **类型定义：** 所有后端响应体在 `api.ts` 中定义对应的 TypeScript `interface`，与 `docs/OPENAPI.JSON` 对齐。
6. **中文文案：** 所有面向用户的字段和 UI 文案必须为中文。
7. **防重复提交：** 所有提交按钮必须有 loading 态禁用 + 防抖。
8. **代码质量：** 提交前必须通过 `npm run lint` 和 `npm run build`。

### 5.4 文件拆分策略

当文件即将超限时，按以下优先级拆分：

1. **按领域拆分：** 将不同业务域的代码拆到独立文件（如 `inspiration_service.py` 独立于 `style_service.py`）。
2. **按职责拆分：** 将辅助函数提取到 `_helpers.py` 或 `_utils.py`。
3. **按数据流拆分：** 将请求 / 响应模型分到 `schemas/request.py` 与 `schemas/response.py`。
4. **当文件夹满 10 个文件时：** 按子领域创建子文件夹（如 `repositories/v1/`）。

---

## 6. API 契约

### 6.1 唯一来源

- **`docs/OPENAPI.JSON`** 是前后端唯一的接口契约。
- 新增或修改字段 **必须先更新文档，再落代码**。
- 状态枚举禁止私扩。

### 6.2 接口全景

| 模块         | 端点                                    | 方法               |
| ------------ | --------------------------------------- | ------------------ |
| **会话**     | `/api/v1/sessions`                      | POST, GET          |
|              | `/api/v1/sessions/{session_id}`         | GET, PATCH, DELETE |
| **灵感对话** | `/api/v1/inspirations/{session_id}`     | GET                |
|              | `/api/v1/inspirations/messages`         | POST (multipart)   |
| **素材**     | `/api/v1/assets/text`                   | POST               |
|              | `/api/v1/assets/video`                  | POST               |
|              | `/api/v1/assets/{asset_id}/transcript`  | GET                |
| **风格模板** | `/api/v1/styles`                        | POST, GET          |
|              | `/api/v1/styles/{style_id}`             | PATCH, DELETE      |
|              | `/api/v1/styles/chat`                   | POST（兼容旧流程） |
| **生成**     | `/api/v1/jobs/generate`                 | POST               |
|              | `/api/v1/jobs/{job_id}`                 | GET                |
|              | `/api/v1/jobs/{job_id}/results`         | GET                |
|              | `/api/v1/jobs/{job_id}/stages`          | GET                |
|              | `/api/v1/jobs/{job_id}/asset-breakdown` | GET                |
|              | `/api/v1/jobs/{job_id}/cancel`          | POST               |
| **导出**     | `/api/v1/exports`                       | POST               |
|              | `/api/v1/exports/{export_id}`           | GET                |
| **提供商**   | `/api/v1/providers`                     | GET, POST          |
|              | `/api/v1/providers/{provider_id}`       | PATCH, DELETE      |
| **模型**     | `/api/v1/models`                        | GET                |
|              | `/api/v1/config/model-routing`          | GET, POST          |

### 6.3 灵感对话 `action` 枚举

| 值                  | 语义                         |
| ------------------- | ---------------------------- |
| `continue`          | 继续对话                     |
| `confirm_prompt`    | 确认提示词，进入资产确认阶段 |
| `confirm_assets`    | 确认资产，锁定草案           |
| `revise_assets`     | 修改资产                     |
| `use_style_profile` | 使用已有风格模板             |
| `save_style`        | 保存草案为风格模板           |
| `skip_save`         | 跳过保存，直接锁定           |

### 6.4 灵感阶段推进序列

```
style_collecting → prompt_revision → asset_confirming → locked
```

- `confirm_prompt` 后进入 `asset_confirming`。
- 必须 `confirm_assets` 后才允许锁定。
- 锁定后不再允许修改草案参数。

---

## 7. 错误码规范

### 7.1 业务错误码

| 错误码   | 含义               | 默认中文消息                                   | HTTP 状态码          |
| -------- | ------------------ | ---------------------------------------------- | -------------------- |
| `E-1001` | 视频转写失败       | 视频转写失败                                   | 500                  |
| `E-1002` | 灵感对话结构异常   | 风格对话返回结构异常                           | 200（支持 fallback） |
| `E-1003` | 资产不足           | 素材不足，无法生成                             | 400                  |
| `E-1004` | 生图部分失败       | 部分图片生成失败                               | — (任务内部状态)     |
| `E-1005` | 导出失败           | 导出流程异常                                   | 500                  |
| `E-1006` | 模型配置缺失       | 请先完成模型设置                               | 400                  |
| `E-1010` | 模型不支持图片解析 | 当前模型不支持图片解析，请切换为视觉模型后重试 | 400                  |
| `E-1099` | 未知内部错误       | 系统内部错误                                   | 500                  |

### 7.2 Not Found 错误码

| 错误码   | 实体     |
| -------- | -------- |
| `E-2001` | 会话     |
| `E-2002` | 素材     |
| `E-2003` | 风格     |
| `E-2004` | 任务     |
| `E-2005` | 导出任务 |
| `E-2006` | 提供商   |

### 7.3 错误响应体标准格式

```json
{
  "code": "E-1006",
  "message": "请先完成模型设置",
  "details": {}
}
```

---

## 8. 任务状态机

### 8.1 状态枚举

```
queued → running → success
                 → partial_success
                 → failed
       → canceled
```

### 8.2 阶段顺序

```
asset_extract → asset_allocate → prompt_generate → image_generate → copy_generate → finalize
```

- `image_generate` 阶段必须产出真实图片文件（`.png` / `.jpg`），**禁止 `.txt` 占位**。
- `progress_percent`：0–100。
- `stage_message`：前端可直接显示的中文进度文案。

---

## 9. 数据模型概览

### 9.1 核心表

| 表名                   | 主键         | 关键字段                                                               |
| ---------------------- | ------------ | ---------------------------------------------------------------------- |
| `sessions`             | `id`         | `title`, `content_mode`, `created_at`, `updated_at`                    |
| `assets`               | `id`         | `session_id`(FK), `asset_type`, `content`, `file_path`, `status`       |
| `inspiration_state`    | `session_id` | `stage`, `draft_payload`(JSON), `locked`                               |
| `inspiration_messages` | `id`         | `session_id`(FK), `role`, `content`, `options`(JSON), `fallback_used`  |
| `style_profiles`       | `id`         | `session_id`(FK, 可空), `name`, `style_payload`(JSON), `is_builtin`    |
| `generation_jobs`      | `id`         | `session_id`(FK), `style_profile_id`(FK), `status`, `progress_percent` |
| `image_results`        | `id`         | `job_id`(FK), `image_index`, `prompt_text`, `image_path`               |
| `copy_results`         | `id`         | `job_id`(FK), `title`, `intro`, `guide_sections`(JSON), `ending`       |
| `export_tasks`         | `id`         | `session_id`(FK), `job_id`(FK), `export_format`, `status`              |
| `provider_configs`     | `id`         | `name`, `base_url`, `api_key_masked`, `api_protocol`                   |
| `model_routing_config` | `id`         | `image_model_*`, `text_model_*`                                        |

### 9.2 `content_mode` 枚举

| 值            | 含义            |
| ------------- | --------------- |
| `food`        | 美食主题        |
| `scenic`      | 景点主题        |
| `food_scenic` | 美食 + 景点混合 |

### 9.3 `api_protocol` 枚举

| 值                 | 含义                        |
| ------------------ | --------------------------- |
| `responses`        | OpenAI Responses API        |
| `chat_completions` | OpenAI Chat Completions API |

---

## 10. 开发工作流

### 10.1 环境准备

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd frontend
npm install
```

### 10.2 启动服务

```bash
# 后端（项目根目录执行）
uvicorn backend.app.main:app --host 127.0.0.1 --port 8887 --reload

# 前端
cd frontend
npm run dev          # 启动开发服务器 http://localhost:7778
```

### 10.3 运行测试

```bash
# 后端测试（项目根目录执行）
pytest backend/tests -q

# 前端检查
cd frontend
npm run lint         # ESLint 检查
npm run build        # TypeScript 编译 + 构建
```

### 10.4 环境变量

| 变量                            | 默认值                          | 说明                  |
| ------------------------------- | ------------------------------- | --------------------- |
| `SAVORY_CANVAS_DB_PATH`         | `backend/data/savory_canvas.db` | SQLite 数据库路径     |
| `SAVORY_CANVAS_STORAGE_DIR`     | `backend/storage`               | 文件存储目录          |
| `SAVORY_CANVAS_PUBLIC_BASE_URL` | `http://127.0.0.1:8887`         | 静态资源公开 URL 前缀 |

---

## 11. 测试策略

### 11.1 后端测试

- **测试类型：** 以 API 集成测试为主，覆盖契约链路。
- **测试框架：** pytest + FastAPI `TestClient`。
- **Fixture 管理：** 统一在 `tests/conftest.py` 中定义测试客户端、数据库初始化、通用 helper。
- **自定义标记：** `@pytest.mark.image_pipeline_real` — 使用真实生图管线逻辑。

### 11.2 测试覆盖清单

| 测试文件                        | 覆盖范围                      |
| ------------------------------- | ----------------------------- |
| `test_session_asset.py`         | 会话 CRUD、素材管理           |
| `test_style_generation.py`      | 风格模板 CRUD、风格对话、降级 |
| `test_generation_flow.py`       | 生成链路、状态机、进度轮询    |
| `test_generation_worker.py`     | Worker 逻辑、图片生成管线     |
| `test_inspiration_flow.py`      | 灵感对话全流程、阶段推进      |
| `test_export_provider_model.py` | 导出、提供商管理、模型路由    |
| `test_cors.py`                  | CORS 跨域配置                 |

### 11.3 验收基线

- `pytest backend/tests -q` **全绿** 方可提交。
- `npm run lint` + `npm run build` **零错误** 方可提交。

---

## 12. 模型能力约束

### 12.1 模型路由

| 路由                         | 能力要求                    |
| ---------------------------- | --------------------------- |
| `image_model`                | 必须具备 `image_generation` |
| `text_model`                 | 必须具备 `text_generation`  |
| `text_model`（含图片输入时） | 还必须具备 `vision`         |

### 12.2 降级策略

- 灵感对话中模型返回结构异常 → `fallback_used=true`，返回 200 + 降级选项。
- 全局唯一 `text_model`，不支持任务级覆盖。

---

## 13. 前端页面布局

```
┌──────────────┬────────────────────────────┬──────────────────┐
│              │                            │  GeneratePanel   │
│ SessionPanel │                            ├──────────────────┤
│              │   InspirationPanel          │  ResultPanel     │
│   (会话列表)  │   (灵感对话 - 中间大面板)    │  (结果预览)      │
│              │                            ├──────────────────┤
│  [模型设置]   │                            │  ExportPanel     │
│              │                            │  (导出)          │
└──────────────┴────────────────────────────┴──────────────────┘
```

- **左侧：** 会话列表 + 模型设置入口。
- **中间：** 灵感对话统一面板（替代旧的素材区 + 风格配置区双面板）。
- **右侧：** 任务进度 → 结果预览 → 导出。

---

## 14. 关键设计决策

| 决策                                                          | 理由                                            |
| ------------------------------------------------------------- | ----------------------------------------------- |
| 采用分层模块：API / Services / Repositories / Workers / Infra | 满足低耦合与职责边界要求                        |
| SQLite + 原生 SQL 仓储                                        | 轻量、可控、符合单体桌面应用约束                |
| Worker 采用后台线程 + `asyncio.run`                           | 兼容 FastAPI 同步路由线程池，避免无事件循环报错 |
| 模型调用以 Provider / Model 配置校验 + 本地模拟返回           | 在离线环境下仍可验证链路                        |
| 测试以 API 集成测试为主                                       | 覆盖契约链路，直接验证状态机与错误码            |
| 手动依赖注入（工厂函数）                                      | 无需引入 DI 框架，依赖关系清晰可追溯            |
| 灵感对话替代旧双面板                                          | 统一输入入口，减少用户认知负担                  |

---

## 15. 常见陷阱与注意事项

### 15.1 后端

1. **事件循环：** Worker 直接使用 `asyncio.create_task` 在同步上下文中会触发 `RuntimeError: no running event loop`。解决方案是 Worker 调度改为后台线程 + `asyncio.run`。
2. **外键级联：** Provider 删除时 `model_routing_config` 存在外键约束。Migration 中已设置 `ON DELETE CASCADE`，新增外键关系时需注意同步。
3. **取消竞态：** Worker 启动时需增加可取消窗口，在各阶段间轮询取消状态。
4. **SQLite 并发锁：** 写操作可能出现短暂锁冲突，需可重试。
5. **测试导入：** 测试文件中使用 `from conftest import ...` 而非相对导入方式。
6. **图片路径：** `images[].image_url` 必须返回前端可直接访问的 `http://...` 完整地址，不能返回本地文件系统路径。

### 15.2 前端

1. **单选点即发：** `options.max == 1` 时点击选项必须立即触发请求，不需要额外确认按钮。历史上出现过"点击无反应"的 bug。
2. **灵感阶段顺序：** 必须严格按 `style_collecting → prompt_revision → asset_confirming → locked` 推进，不可跳阶段。
3. **CORS：** 后端仅允许 `http://localhost:7778` 和 `http://127.0.0.1:7778`，前端 dev server 必须保持端口一致。
4. **轮询周期：** 任务进度建议 1.5 秒轮询一次。
5. **多图逐帧回显：** 前端拿到部分结果后应立即渲染，不等待所有图片全部完成。

---

## 16. 文档索引

| 文档       | 路径                     | 用途                           |
| ---------- | ------------------------ | ------------------------------ |
| AGENTS.MD  | `./AGENTS.MD`            | 项目编码指南（本文件）         |
| PRD        | `docs/PRD.MD`            | 产品需求文档                   |
| 后端说明   | `docs/BACKEND.MD`        | 后端开发细节与交付清单         |
| 前端说明   | `docs/FRONTED.MD`        | 前端开发细节与交付清单         |
| API 契约   | `docs/OPENAPI.JSON`      | 唯一接口规范源                 |
| 错误码表   | `backend/error_codes.md` | 错误码速查                     |
| 设计与计划 | `docs/plans/*.md`        | 各迭代周期的设计/实施/修复计划 |

---

## 17. 提交检查清单

每次代码变更前，请逐项确认：

- [ ] 单函数行数 < 200
- [ ] 单文件行数 < 1000
- [ ] 单文件夹文件数 ≤ 10
- [ ] 所有文件 UTF-8 编码
- [ ] 新增 / 修改接口已同步更新 `docs/OPENAPI.JSON`
- [ ] 后端测试 `pytest backend/tests -q` 全绿
- [ ] 前端 `npm run lint` 零错误
- [ ] 前端 `npm run build` 零错误
- [ ] 用户可见文案均为中文
- [ ] 长耗时接口采用异步任务模式
- [ ] 错误码使用标准 `DomainError`，不自定义响应格式
