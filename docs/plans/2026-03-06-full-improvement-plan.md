# Savory Canvas 全面改进计划

> 最后更新：2026-03-06  
> 审查范围：后端全量 Python 代码 + 前端全量 TypeScript/React 代码 + CSS + 文档  
> 优先级：P0 = Agent 架构升级 | P1 = 功能缺陷修复 | P2 = 体验与稳定性 | P3 = 代码质量

---

## 📋 给工程师的提示词

> 以下内容可直接转发给你的工程师，他可以作为开发指引来使用。

```
你好，以下是代码审查后整理的改进计划。请按 P0 → P1 → P2 → P3 的优先级顺序执行。今晚全部完成。

核心原则：
1. 每个任务都有明确的"文件路径 + 行号 + 期望修改"，请严格按照描述执行
2. 修改后端代码时，确保现有测试全通过（pytest），再新增覆盖新逻辑的测试
3. 修改前端代码时，确保 npm run build 无 TypeScript 错误，手动走一遍完整流程
4. 涉及 API 契约变更的改动，先更新 docs/OPENAPI.JSON，再改代码
5. 注意 AGENTS.MD 中"单文件 < 1000 行"的规则，拆分巨型文件时保持 mixin 继承关系不变
6. 前端交互 bug 修复后，请在对应组件中用注释标注修复点，方便 review

每次提交前请自查：
□ pytest 全部通过
□ npm run build 无错误
□ 主流程可走通（创建会话 → 灵感对话 → 锁定 → 生成 → 查看结果 → 导出 PDF）
□ 无新增 lint 警告
```

---

## P0：Agent 架构升级

**目标：** 将"固定状态机 + 硬编码 Prompt + 手动推进"改造为"自主推理 Agent + 工具调用 + 动态决策"。

**推荐技术栈：** LangGraph + LangChain

### P0-01 引入 LangChain 基础设施
- 安装依赖：`pip install langchain langchain-openai langgraph`
- 新建 `backend/app/agent/llm_provider.py`，包装现有 `ModelService` 的路由配置为 `ChatOpenAI` 实例
- 验证：确保 LangChain 能通过现有 Provider 配置成功调用模型

### P0-02 封装现有逻辑为 LangChain Tools
- 新建 `backend/app/agent/tools/` 目录
- 将以下现有功能封装为 `@tool`：
  - `suggest_painting_style` ← 从 `StyleService` 提取
  - `extract_assets` ← 从 `InspirationFlowMixin._extract_asset_candidates` 提取
  - `generate_style_prompt` ← 从 `InspirationFlowMixin._build_style_payload` 提取
  - `allocate_assets_to_images` ← 从 `InspirationFlowMixin._build_allocation_plan` 提取
  - `generate_images` ← 从 `GenerationWorker` 提取
  - `generate_copy` ← 从 `GenerationWorker._generate_copy` 提取
- 验证：每个 Tool 可独立调用并返回正确结果

### P0-03 用 LangGraph 构建 Agent 工作流
- 新建 `backend/app/agent/creative_agent.py`
- 用 `StateGraph` 定义 Agent 状态和推理节点
- 实现 `agent_node`（核心推理）和 `tool_node`（工具执行）
- 实现 `should_continue` 条件边，让 Agent 自主选择下一步
- 验证：Agent 可自主完成灵感对话全流程

### P0-04 集成 Memory 系统
- 使用 `SQLChatMessageHistory` 对接现有 SQLite 数据库
- 用 `RunnableWithMessageHistory` 包装 Agent，实现跨轮次记忆
- 验证：Agent 可在多轮对话中保持上下文连贯

### P0-05 替换 API 层并适配前端
- 修改 `backend/app/api/v1/inspiration.py`，灵感对话 API 改为调用 Agent
- 保留现有固定流程作为 fallback（当 Agent 超时或异常时回退）
- 前端适配 Agent 响应格式（可能需要修改 `InspirationPanel` 的选项渲染逻辑）
- 验证：前端完整流程走通，包括风格选择、提示词确认、分图确认、生成

### P0-06 前端适配 Agent 模式
- 灵感对话面板的阶段指示器需要从硬编码四阶段改为动态阶段
- 选项按钮的渲染逻辑需要适配 Agent 返回的动态选项
- 可能需要增加"Agent 正在思考"的可视化（类似 ChatGPT 的 thinking indicator）
- 考虑增加"查看 Agent 推理过程"的调试面板（展示 Tool 调用链）

**注意事项：**
1. 保留 `--legacy` 启动参数切换回固定流程
2. Agent 模式下 LLM 调用次数增加，需做好 token 预算控制
3. 在关键节点（如确认提示词、确认分图、锁定方案）仍需用户手动确认

---

## P1：功能缺陷修复

### P1-01 [后端] SQLite 缺少 WAL 模式导致并发阻塞
- **文件：** `backend/app/infra/db.py:91-95`
- **现象：** Worker 后台线程写入进度时，主线程的 API 读请求可能被锁阻塞（返回 500 或超时）。多图生成期间前端轮询 API 响应变慢。
- **修复：**
  ```python
  def _connect(self) -> sqlite3.Connection:
      connection = sqlite3.connect(str(self._db_path), check_same_thread=False)
      connection.row_factory = sqlite3.Row
      connection.execute("PRAGMA foreign_keys = ON")
      connection.execute("PRAGMA journal_mode = WAL")       # 新增
      connection.execute("PRAGMA busy_timeout = 5000")       # 新增
      connection.execute("PRAGMA synchronous = NORMAL")      # 新增（WAL 模式下安全）
      return connection
  ```
- **验证：** 启动后端后执行 `sqlite3 test.db "PRAGMA journal_mode;"` 应返回 `wal`

### P1-02 [后端] `long_image` 导出格式实际输出的是纯文本
- **文件：** `backend/app/workers/export_worker.py:58-63`
- **现象：** 前端 `ExportTaskCreateRequest` 定义了 `Literal["long_image", "pdf"]`，但 `export_format == "long_image"` 走 `else` 分支导出 `.txt` 文件。PRD FR-026 要求"支持导出长图"，但功能未实现。
- **修复方案（二选一）：**
  - A）实现真正的长图拼接导出（用 Pillow 将多图纵向拼接并叠加文案）
  - B）如果暂不支持，从 `ExportTaskCreateRequest.export_format` 中移除 `"long_image"` 选项，避免用户触发虚假功能
- **如选方案 A，参考实现：**
  ```python
  elif task["export_format"] == "long_image":
      file_content = self._build_long_image_bytes(images=images, copy_result=copy_result)
      extension = "png"
  ```

### P1-03 [后端] `image_usages` 参数被完全忽略
- **文件：** `backend/app/services/inspiration_service.py:84`
- **代码：** `_ = image_usages`
- **现象：** API 接口定义了 `image_usages` 参数（`style_reference` / `content_asset`），前端也传了对应值，但后端直接丢弃。用户上传的"风格参考图"和"内容素材"被混为一谈。
- **修复：** 在 `send_message` 中根据 `image_usages` 对上传的图片分类处理。风格参考图应存储时打 tag，在生成流水线中作为风格参考使用（`_collect_style_reference_paths` 已有对应逻辑）。
- **关联修改：**
  - `inspiration_service.py` 的 `send_message` 方法
  - `_save_attachments` 方法
  - 可能需要在 asset 表中增加 `usage_type` 字段（先检查 `001_init.sql` 中是否已有）

### P1-04 [后端] 危险的进程杀死逻辑
- **文件：** `backend/app/main.py:57-133`
- **现象：** `_kill_port_listeners` 使用 `taskkill /T /F` 强杀占端口的进程，只保护当前 PID 和 PPID。多服务共存环境可能误杀用户的其他进程（数据库、Web 服务等）。
- **修复：** 
  1. 将 `_maybe_cleanup_old_backend_processes` 和 `_kill_port_listeners` 从 `main.py` 中移除
  2. 新建 `scripts/start_dev.ps1` 把清理逻辑放到开发启动脚本中
  3. 如果必须保留，至少增加进程名校验（只杀 python/uvicorn 进程）

### P1-05 [前端] 新建会话时 `content_mode` 被硬编码为 `'food'`
- **文件：** `frontend/src/components/SessionPanel.tsx:83`
- **代码：** `createSession(newTitle, 'food');`
- **现象：** 用户新建会话时，总是默认为"美食"模式。PRD 定义了三种模式（`food` / `scenic` / `food_scenic`），但创建弹窗中没有选择控件。
- **修复：** 在新建会话弹窗（`showCreateModal` 的 Modal 内容，约 146-168 行）中增加一个 `<select>` 让用户选择内容模式。

### P1-06 [前端] 导出面板只提供 PDF 按钮，缺少 `long_image` 选项
- **文件：** `frontend/src/components/ExportPanel.tsx:91`
- **代码：** `export_format: 'pdf'` 被硬编码
- **现象：** 即使后端支持 `long_image`（修了 P1-02 之后），前端也没有入口触发。
- **修复：** 增加"导出长图"按钮，或一个格式选择下拉，调用时传 `export_format: 'long_image'`
- **依赖：** P1-02 必须先完成

---

## P2：体验与稳定性

### P2-01 [后端] CORS 配置矛盾
- **文件：** `backend/app/main.py:237-244`
- **现象：** `allow_origins` 限制 7778 端口，但 `allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"` 允许任意本地端口。regex 使 origins 限制形同虚设。
- **修复：** 只保留 `allow_origin_regex`，删除 `allow_origins` 列表（或只保留显式列表，删除 regex）。

### P2-02 [后端] 每次 `transaction()` 都创建和关闭连接
- **文件：** `backend/app/infra/db.py:97-108`
- **现象：** 频繁 `sqlite3.connect()` + `close()`，在高频轮询场景（1.5 秒一次的进度查询）下有不必要的开销。
- **修复方案：** 使用线程本地连接（`threading.local()`）复用连接，区分 `read()` 和 `transaction()` 方法。读操作不需要写锁。

### P2-03 [后端] `_looks_like_internal_parameter_dump` 误判范围过大
- **文件：** `backend/app/services/inspiration_service.py:660-665`
- **代码：** `return "json" in lowered and "{" in text and "}" in text`
- **现象：** 任何包含 "json" 和大括号的合法提示词都会被误判为参数泄露，导致提示词被否决→重试→浪费 token。
- **修复：** 删除最后一行的宽泛 json 检测，只在 markers 命中时返回 True。

### P2-04 [后端] `_validate_split_prompt_format` 校验不完整
- **文件：** `backend/app/services/inspiration_service.py:650-658`
- **现象：** 只硬编码了"生成两张""三张""四张"，如果模型输出"生成五张"…"生成十张"不会被拦截。
- **修复：** 使用正则匹配：
  ```python
  import re
  multi_pattern = re.compile(r"生成[两二三四五六七八九十\d]+张")
  if multi_pattern.search(compact) or "一次生成多张" in compact:
      return False
  ```

### P2-05 [后端] `_extract_cli_port` 默认端口错误
- **文件：** `backend/app/main.py:74`
- **现象：** 项目使用 8887 端口，但默认值是 8000。可能误杀其他服务。
- **修复：** 如果 P1-04 保留了此逻辑，将默认端口改为 `8887`；否则随 P1-04 一起移除。

### P2-06 [前端] `SessionPanel` 创建会话后不触发 `setActiveSessionId`
- **文件：** `frontend/src/store.ts:162-177`
- **现象：** `createSession` 直接 `set` 了 `activeSessionId` 而没走 `setActiveSessionId` 的完整初始化逻辑。
- **修复：** `createSession` 成功后显式调用 `get().setActiveSessionId(newSession.id)`。

### P2-07 [前端] `fetchSessions` 首次加载时自动选中首个会话但不触发完整初始化
- **文件：** `frontend/src/store.ts:154-156`
- **代码：** `set({ activeSessionId: data.items[0].id });`
- **现象：** 首次打开应用时，历史会话和任务状态不会恢复到右侧面板。
- **修复：** 改为 `get().setActiveSessionId(data.items[0].id);`

### P2-08 [前端] `copyToClipboard` 失败时没有错误提示
- **文件：** `frontend/src/components/ResultPanel.tsx:71-80`
- **修复：** catch 中增加 `addToast('复制失败，请手动选择复制', 'error');`

### P2-09 [前端] 灵感对话滚动到底部的时机过于激进
- **文件：** `frontend/src/components/InspirationPanel.tsx:204-206`
- **现象：** 每次 `draft` 改变都触发滚动到底部，打断用户阅读历史消息。
- **修复：** 只在 `messages.length` 变化时滚动，或检查用户是否已滚动到底部附近再决定。

### P2-10 [前端] Modal 缺少 Escape 关闭支持
- **文件：** `SessionPanel.tsx:146-209`、`ModelPanel.tsx:288-336`
- **修复：** 为每个 Modal 添加 `useEffect` 监听 `keydown` 的 Escape 事件。

---

## P3：代码质量

### P3-01 [后端] 引入领域模型类，消灭 `dict[str, Any]`
- **范围：** 全量后端代码
- **方案：** 在 `backend/app/domain/models.py` 中定义 `@dataclass` 模型类（`InspirationState`, `Session`, `GenerationJob`, `StyleProfile`, `Asset` 等），Repository 层返回实例。
- **注意：** 先从 `Session` 和 `GenerationJob` 开始，逐步替换。

### P3-02 [后端] 拆分 4 个接近行数上限的文件

| 文件 | 行数 | 拆分方案 |
| --- | --- | --- |
| `generation_worker.py` | 995 | 将图片生成重试逻辑抽到 `workers/generation/image_gen_mixin.py` |
| `pipeline_mixin.py` | 984 | 拆为 `http_client.py`、`base64_utils.py`、`error_classifier.py` |
| `flow_mixin.py` | 923 | 拆为 `asset_extraction.py`、`allocation_builder.py` |
| `style_service.py` | 906 | 拆为 `protocol_adapter.py`、`model_caller.py` |

### P3-03 [后端] 统一 HTTP 客户端
- **现状：** `style_service.py` 和 `pipeline_mixin.py` 各自实现了 `_post_json`，逻辑大量重复。
- **方案：** 新建 `backend/app/infra/http_client.py`，抽出统一的 `post_json()` 函数。

### P3-04 [后端] Prompt 模板外部化
- **方案：** 新建 `backend/prompts/` 目录，每个提示词一个 `.txt` 文件，通过 `prompt_loader.py` 的 `@lru_cache` 按名称加载。

### P3-05 [后端] 日志配置统一
- **方案：** 新建 `backend/app/core/logging_config.py`，统一日志格式，添加 `X-Request-Id` 中间件。

### P3-06 [后端] `_with_public_file_url` 直接修改入参 dict
- **文件：** `backend/app/services/export_service.py:58-74`
- **修复：** 改为 `return {**task, "file_url": ...}`，不修改原对象。

### P3-07 [后端] `conftest.py` 中对特定测试名称硬编码
- **文件：** `backend/tests/conftest.py:125`
- **修复：** 改用 pytest marker `@pytest.mark.real_copy_model`。

### P3-08 [后端] `_text_protocol_overrides` 死代码
- **文件：** `backend/app/workers/generation_worker.py:74`
- **修复：** 删除 `self._text_protocol_overrides: dict[str, str] = {}`

### P3-09 [后端] `Storage.save_*` 路径约定不一致
- **文件：** `backend/app/infra/storage.py`
- **修复：** 统一返回相对于 `base_dir` 的路径。

### P3-10 [后端] Provider API Key 明文存储
- **文件：** `backend/migrations/001_init.sql:157` 和 `provider_service.py:8-11`
- **修复：** 至少简单加密存储（`Fernet` 对称加密），mask 函数改为只保留尾 4 位。

### P3-11 [后端] `export_worker.py` 硬编码 Windows 字体路径
- **文件：** `backend/app/workers/export_worker.py:253-258`
- **修复：** 支持从配置中读取字体路径，增加跨平台 fallback。

### P3-12 [前端] `InspirationPanel` 超过 800 行
- **文件：** `frontend/src/components/InspirationPanel.tsx` (814 行)
- **修复：** 拆分为 `ChatAttachment.tsx`、`ChatMessage.tsx`、`CandidateEditor.tsx`、`ChatInput.tsx`。

### P3-13 [前端] 类型安全改进
- **修复：** 封装 `getErrorMessage(err: unknown): string` 工具函数，`draft.style_payload` 类型定义改为 `api.StylePayload | null`。

### P3-14 [前端] `SessionUpdateRequest` 不支持修改 `content_mode`
- **文件：** `backend/app/schemas/request.py:13-14`
- **修复：** 给 `SessionUpdateRequest` 增加 `content_mode: Literal[...] | None = None` 可选字段。

---

## 📊 任务统计

| 优先级 | 后端 | 前端 | 合计 |
| --- | --- | --- | --- |
| P0（Agent 架构升级） | 5 | 1 | 6 |
| P1（功能缺陷） | 4 | 2 | 6 |
| P2（体验稳定性） | 5 | 5 | 10 |
| P3（代码质量） | 11 | 3 | 14 |
| **合计** | **25** | **11** | **36** |

---

## 📝 验收检查清单

### P0 完成后
- [ ] LangChain 可通过现有 Provider 配置调用模型
- [ ] 所有 Tool 可独立调用并返回正确结果
- [ ] Agent 可自主完成灵感对话全流程
- [ ] Agent 多轮对话上下文连贯
- [ ] 前端完整流程走通（含 Agent 模式）
- [ ] `--legacy` 参数可切换回固定流程

### P1 完成后
- [ ] 多图生成期间前端轮询不超时
- [ ] `long_image` 导出功能正常或已移除入口
- [ ] 上传图片时可选择"风格参考"或"内容素材"
- [ ] 后端启动不再杀占端口进程（或有保护）
- [ ] 新建会话时可选择内容模式
- [ ] 前端导出面板有长图选项（或已移除）

### P2 完成后
- [ ] CORS 配置无矛盾
- [ ] SQLite 连接复用正常
- [ ] 多张图提示词格式校验完整
- [ ] 首次打开应用自动恢复历史任务状态
- [ ] 所有 Modal 支持 Escape 关闭
- [ ] 复制文案失败有错误提示

### P3 完成后
- [ ] 所有后端数据传输使用 dataclass（至少核心实体）
- [ ] 无文件超过 800 行
- [ ] HTTP 调用统一通过 `http_client.py`
- [ ] Prompt 模板在 `prompts/` 目录可独立编辑
- [ ] API Key 非明文存储
- [ ] 前端 InspirationPanel 拆分为子组件
