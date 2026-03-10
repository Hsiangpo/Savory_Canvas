# Findings & Decisions

## Requirements
- 后端运行栈：Python + FastAPI + SQLite。
- 任务型接口必须异步可轮询，禁止同步返回完整结果。
- 全量实现批次 A~E 的所有 API。
- 错误码至少覆盖 E-1001~E-1006、E-1099。
- 交付迁移脚本、worker、自动化测试。

## Research Findings
- 初始仓库仅含 docs，`backend` 为空目录。
- `docs/OPENAPI.JSON` 提供完整契约，已按字段名/枚举对齐实现。
- TestClient 中同步路由运行在线程池，直接 `asyncio.create_task` 会触发无事件循环异常。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 启动时执行 `migrations/001_init.sql` 初始化 SQLite | 满足迁移交付物并降低环境依赖 |
| Repository 持有原生 SQL，Service 做业务编排 | 满足职责分离，便于测试 |
| 任务执行采用后台线程 + `asyncio.run` | 保证异步流程在 sync/async 端点下都可调度 |
| 风格对话支持模型不可用 fallback | 满足需求“模型异常可 fallback” |
| 生成支持 `force_partial_fail` 触发部分失败场景 | 可稳定覆盖 `partial_success` 测试 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Provider 删除时路由配置外键冲突 | migration 调整为 `ON DELETE CASCADE` |
| 取消任务存在竞态 | worker 启动增加可取消窗口并在阶段间轮询取消状态 |
| 测试运行后产生缓存目录 | 清理 `__pycache__/.pytest_cache` |

## Resources
- `docs/BACKEND.MD`
- `docs/OPENAPI.JSON`
- `docs/plans/2026-02-23-backend-design.md`
- `docs/plans/2026-02-23-backend-implementation-plan.md`
- `backend/migrations/001_init.sql`

## Visual/Browser Findings
- 本任务无需浏览器/图片分析。
## 2026-03-09 审计新增发现

### 自动化验证
- `pytest backend/tests -q`：164 passed。
- `npm run build`：通过。
- `python scripts/check_limits.py`：在 Windows GBK 控制台触发 `UnicodeEncodeError`；用 `python -X utf8 scripts/check_limits.py` 可正常运行并暴露真实门禁问题。

### 高置信问题
1. **风格抽屉仍残留概念分层文案**
   - 文件：`frontend/src/components/StyleManagementDrawer.tsx`
   - 现象：卡片副标题仍显示“系统风格 / 全局风格”，与现有产品要求冲突。
2. **右侧面板重复轮询与闪烁根因**
   - 文件：`frontend/src/components/GeneratePanel.tsx`、`frontend/src/components/ResultPanel.tsx`、`frontend/src/store.ts`、`frontend/src/components/InspirationPanel.tsx`
   - 现象：生成区至少有双重 job 轮询；同时 `syncLatestJob` 每次都会清空结果态，容易导致右栏闪一下。
3. **Windows 门禁脚本不可用**
   - 文件：`scripts/check_limits.py`
   - 现象：默认 GBK 控制台直接因 emoji 输出崩溃，导致真实超限问题被掩盖。
4. **后端文件超限**
   - 文件：`backend/app/services/inspiration_service.py`
   - 现象：当前 1008 行，违反 `< 1000` 约束。

### 联调观察
- 真视频转写已成功落库，自动续跑也成功推进到了“确认图片张数”。
- Chrome 控制台仍有可访问性 issue：表单字段缺少 id/name 或关联 label。
## 2026-03-09 审查新增发现
- 高置信前端问题 1：`InspirationPanel` 在转写轮询中用“消息里是否存在 processing 视频附件”作为继续轮询条件；由于历史用户消息附件状态不会回写为 ready，进入下个阶段后仍持续轮询，导致 `GET /inspirations/{session_id}` 持续发起，严重时出现 pending 堆积。
- 高置信前端问题 2：思考展示直接消费 Agent reasoning summary，导致“需要调用 extract_assets 工具…”这类内部策略文案暴露给最终用户。
- 可访问性问题：会话标题输入框和主对话 textarea 缺少明确 label 关联，Chrome DevTools 报告表单告警。
- 实机复验结果：修复后，转写完成进入“确认生成张数”阶段时，请求计数不再继续增长；思考区仅展示泛化后的状态和工具完成文案，不再暴露内部推理。
