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
