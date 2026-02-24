# Task Plan: Savory Canvas 后端全量实现

## Goal
基于 `docs/OPENAPI.JSON` 与 `docs/BACKEND.MD`，从零实现可运行的 FastAPI + SQLite 后端，覆盖会话、素材转写、风格、生成、导出、提供商与模型路由，并交付自动化测试与迁移脚本。

## Current Phase
Phase 5

## Phases
### Phase 1: 需求与契约梳理
- [x] 读取 BACKEND 与 OPENAPI 契约
- [x] 确认空仓状态与实现范围
- [x] 输出架构方向与边界
- **Status:** complete

### Phase 2: 架构与计划文档
- [x] 生成设计文档
- [x] 生成实施计划文档
- [x] 创建 planning 工作记忆文件
- **Status:** complete

### Phase 3: TDD 测试基线
- [x] 先写核心链路失败测试
- [x] 运行测试确认失败原因正确
- [x] 记录失败证据
- **Status:** complete

### Phase 4: 后端实现
- [x] 完成数据库层、仓储层、服务层、worker 层
- [x] 完成全部 API 路由与错误码映射
- [x] 完成迁移脚本、启动入口与配置
- **Status:** complete

### Phase 5: 验证与交付
- [x] 运行全量测试
- [x] 逐项对照 BACKEND 清单验收
- [x] 输出变更总结与验证证据
- **Status:** complete

## Key Questions
1. 如何在本地单体中实现“异步任务”同时保证接口可轮询？
2. 如何在无真实模型供应商时满足风格对话 fallback 与生成流程可测？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 采用分层模块：api/services/repositories/workers/infra | 满足低耦合与职责边界要求 |
| 使用 SQLite + 原生 SQL 仓储 | 轻量、可控、与约束一致 |
| Worker 采用后台线程 + 协程执行 | 兼容 FastAPI 同步路由线程池，避免无事件循环报错 |
| 模型调用以 provider/model 配置校验 + 本地模拟返回 | 在离线环境下仍可验证链路 |
| 测试以 API 集成测试为主 | 覆盖契约链路，直接验证状态机与错误码 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| rg 不可用 | 1 | 切换 PowerShell 递归命令 |
| Pytest 相对导入失败 | 1 | 测试改为 `from conftest import ...` |
| `RuntimeError: no running event loop` | 1 | worker 调度改为后台线程执行协程 |
| 批量替换引入语法错误 | 1 | 修复 worker import 语句并回归 |

## Notes
- 严格以 OPENAPI 字段作为返回结构。
- 所有源码文件均 < 1000 行，目录文件数均 <= 10。
- `generation_worker.py` 文件 235 行，但单函数均 < 200 行。
