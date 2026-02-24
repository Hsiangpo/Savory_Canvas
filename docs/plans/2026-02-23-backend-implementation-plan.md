# Savory Canvas Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现符合 OPENAPI 与 BACKEND 说明的全量后端能力，并交付可通过的自动化测试。

**Architecture:** 采用 FastAPI 分层架构，SQLite 存储业务状态，后台协程执行转写/生成/导出任务。API 层仅做输入输出和错误映射，业务编排由 Service 层完成。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, Pytest

---

### Task 1: 初始化项目骨架与配置

**Files:**
- Create: `backend/app/main.py`
- Create: `backend/app/api/router.py`
- Create: `backend/app/core/settings.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/requirements.txt`

**Step 1: Write the failing test**

```python
def test_health_importable():
    from backend.app.main import app
    assert app is not None
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_bootstrap.py::test_health_importable -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

创建 FastAPI app、基础 router 与配置。

**Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_bootstrap.py::test_health_importable -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app backend/requirements.txt backend/tests/test_bootstrap.py
git commit -m "feat: bootstrap backend service"
```

### Task 2: 建立数据库 schema 与仓储层

**Files:**
- Create: `backend/migrations/001_init.sql`
- Create: `backend/app/infra/db.py`
- Create: `backend/app/repositories/*.py`

**Step 1: Write the failing test**

```python
def test_create_session_persisted(client):
    resp = client.post("/api/v1/sessions", json={"title":"t","content_mode":"food"})
    assert resp.status_code == 201
```

**Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_session_asset.py::test_create_session_persisted -v`
Expected: FAIL with 404

**Step 3: Write minimal implementation**

完成 schema 初始化和 session 基础仓储。

**Step 4: Run test to verify it passes**

Run: same command, Expected PASS

**Step 5: Commit**

```bash
git add backend/migrations backend/app/infra backend/app/repositories backend/tests
git commit -m "feat: add sqlite schema and repositories"
```

### Task 3: 完成会话与素材 API（批次 A）

**Files:**
- Create: `backend/app/services/session_service.py`
- Create: `backend/app/services/asset_service.py`
- Create: `backend/app/workers/transcript_worker.py`
- Create: `backend/app/api/v1/session.py`
- Create: `backend/app/api/v1/asset.py`
- Test: `backend/tests/test_session_asset.py`

**Step 1: Write failing tests**

覆盖创建会话、上传文本/视频素材、查询转写状态。

**Step 2: Verify red**

Run targeted pytest, Expected FAIL by missing handlers.

**Step 3: Minimal implementation**

实现批次 A 全接口与异步转写任务。

**Step 4: Verify green**

Run targeted pytest, Expected PASS.

**Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: implement session and asset apis"
```

### Task 4: 完成风格与生成 API（批次 B/C）

**Files:**
- Create: `backend/app/services/style_service.py`
- Create: `backend/app/services/generation_service.py`
- Create: `backend/app/workers/generation_worker.py`
- Create: `backend/app/api/v1/style.py`
- Create: `backend/app/api/v1/generation.py`
- Test: `backend/tests/test_style_generation.py`

**Step 1: Write failing tests**

覆盖 style chat/fallback、创建生成任务、轮询结果、取消任务。

**Step 2: Verify red**

Run targeted pytest, Expected FAIL.

**Step 3: Minimal implementation**

实现固定阶段状态机、partial_success 逻辑与结果落库。

**Step 4: Verify green**

Run targeted pytest, Expected PASS.

**Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: implement style and generation workflows"
```

### Task 5: 完成导出与模型管理 API（批次 D/E）

**Files:**
- Create: `backend/app/services/export_service.py`
- Create: `backend/app/services/provider_service.py`
- Create: `backend/app/services/model_service.py`
- Create: `backend/app/workers/export_worker.py`
- Create: `backend/app/api/v1/export.py`
- Create: `backend/app/api/v1/provider.py`
- Create: `backend/app/api/v1/model.py`
- Test: `backend/tests/test_export_provider_model.py`

**Step 1: Write failing tests**

覆盖导出任务、provider CRUD、model routing 读写与模型列表。

**Step 2: Verify red**

Run targeted pytest, Expected FAIL.

**Step 3: Minimal implementation**

实现批次 D/E 全接口。

**Step 4: Verify green**

Run targeted pytest, Expected PASS.

**Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: implement export and model management"
```

### Task 6: 全量回归与验收

**Files:**
- Modify: `task_plan.md`
- Modify: `progress.md`

**Step 1: Run full test**

Run: `pytest backend/tests -v`
Expected: 全部 PASS

**Step 2: Checklist verification**

逐条对照 `docs/BACKEND.MD` 第 11/12 节。

**Step 3: Document evidence**

将测试输出与满足项记录到 `progress.md`。

**Step 4: Final status**

输出最终变更总结。

**Step 5: Commit**

```bash
git add .
git commit -m "test: verify backend requirements end-to-end"
```
