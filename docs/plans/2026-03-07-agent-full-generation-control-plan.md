# Agent 完全接管生成控制

> 基线：当前 main HEAD  
> 目标：去掉右侧面板的手动「开始生成」按钮，生图和文案完全由 Agent 控制触发  
> 预计工作量：后端 0.3 天，前端 0.3 天

---

## 改造目标

```
之前：
  左侧对话（Agent 控制） ──── 右侧面板（用户手动点「开始生成」）
                                       ↑ 独立的 POST /jobs/generate

之后：
  左侧对话（Agent 控制一切） ── 右侧面板（纯状态展示，无按钮）
       ↓ Agent 自动调 generate_images
       → active_job_id 写入 draft
       → 前端自动 syncLatestJob
       → 右侧面板自动展示进度
```

---

## 后端任务

### BE-T1：生图防重复

**文件**：`inspiration_service.py` 的 `generate_images()` 方法

**问题**：如果 Agent 因 LLM 不稳定性重复调用 `generate_images`，会创建多个 job。

**修复**：在 `generate_images()` 开头，`_ensure_state` 之后加防重复检查：

```python
existing_job_id = state.get("active_job_id")
if existing_job_id:
    existing_job = self.generation_worker.job_repo.get(existing_job_id)
    if existing_job and existing_job.get("status") not in {
        "success", "partial_success", "failed", "canceled"
    }:
        return {
            "job_id": existing_job_id,
            "status": existing_job["status"],
            "already_running": True,
        }
```

**新增测试**：`test_generate_images_prevents_duplicate_job`

---

### BE-T2：文案生成 prompt 引导

**文件**：`backend/prompts/agent/creative_agent_system_prompt.txt`

在现有自动生成标准之后追加规则：

```
- 当图片生成任务完成（active_job_id 对应的 job 为 success 或 partial_success）后，
  主动询问用户是否需要配套文案，用户确认后调用 generate_copy。
```

---

## 前端任务

### FE-T1：右侧面板删除「开始生成」按钮

**文件**：`GeneratePanel.tsx`

1. 删除 `handleStart` 函数（约 134-153 行）
2. 删除底部的「开始生成」按钮区域（约 355-373 行整个 `<div>`）
3. 在原按钮位置替换为状态提示文案（仅 idle 时显示）：

```tsx
{status === 'idle' && (
  <div style={{
    textAlign: 'center',
    color: 'var(--text-secondary)',
    fontSize: '0.85rem',
    padding: '16px 0',
  }}>
    Agent 会在创作方案确认后自动启动生成
  </div>
)}
```

4. 保留取消按钮，仅在 isRunning 时显示：

```tsx
{isRunning && (
  <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0' }}>
    <button className="btn btn-secondary" onClick={cancelJob} title="取消任务">
      <XCircle size={18} color="var(--error)" /> 取消生成
    </button>
  </div>
)}
```

---

### FE-T2：store.ts 清理 startJob

**文件**：`store.ts`

删除 `startJob` 函数（约 208-228 行）、`isStartingJob` state 及相关类型。这些不再被前端使用。

---

## 完成标准

- [ ] 右侧面板无「开始生成」按钮
- [ ] 右侧面板在 idle 时显示"Agent 会在创作方案确认后自动启动生成"
- [ ] 取消按钮仍然可用（仅在 isRunning 时显示）
- [ ] Agent 重复调 generate_images 不会创建多个 job
- [ ] system prompt 引导 Agent 在图片生成完成后主动询问文案需求
- [ ] pytest 全绿，npm run build 通过
