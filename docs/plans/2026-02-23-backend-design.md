# Savory Canvas 后端设计

- 日期：2026-02-23
- 目标：从零实现符合 `docs/OPENAPI.JSON` 契约的异步任务后端。

## 1. 设计结论

推荐采用单进程分层架构：`API -> Service -> Repository -> SQLite`，并由 `Worker` 负责异步任务推进（转写、生成、导出）。

## 2. 方案比较

### 方案 A（推荐）：FastAPI 后台协程 + SQLite 仓储 + 本地模拟模型客户端
- 优点：实现成本低、契约可完整覆盖、易测试、适配本地 exe 服务层。
- 缺点：高并发能力有限，不适合分布式扩展。

### 方案 B：引入 Celery/RQ + 外部队列
- 优点：任务调度能力强。
- 缺点：超出当前本地单体边界，部署复杂，不符合 YAGNI。

### 方案 C：全同步实现，接口直接等待结果
- 优点：实现快。
- 缺点：违反“长耗时统一异步任务”硬约束，不可选。

## 3. 模块与数据流

1. API 只做校验、序列化、错误映射。
2. Service 编排业务：创建记录、调度 worker、控制状态机。
3. Repository 只负责 SQL 和映射。
4. Worker 按固定阶段推进：
   - `asset_extract`
   - `asset_allocate`
   - `prompt_generate`
   - `image_generate`
   - `copy_generate`
   - `finalize`
5. 任务结果统一入库，查询接口仅读库返回。

## 4. 错误处理

- 使用领域异常 `DomainError(code, message, details)`。
- API 统一转换为 `{code,message,details}`。
- 关键错误码：`E-1001~E-1006`，兜底 `E-1099`。

## 5. 测试策略

- 以 API 集成测试为主，覆盖清单：
  - 会话 CRUD 路径
  - 视频转写状态
  - 风格对话 JSON 合法性与 fallback
  - 生成状态机与 partial_success
  - 导出任务
  - 模型路由读写

## 6. 非功能约束落实

- 单函数 <= 200 行。
- 单文件 <= 1000 行。
- 新增目录单层文件数控制在 10 以内。
- 时间统一 ISO8601 字符串，主键统一 UUID。
