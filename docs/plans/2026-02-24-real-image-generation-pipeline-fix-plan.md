# 生图链路真实化与可观测性修复计划（2026-02-24）

## 1. 目标

1. 修复“生成结果无真实图片”的阻断问题，确保用户点击生成后能看到真实图片文件。
2. 补齐“资产提取拆解”可见性，前端可查看本次生成使用了哪些素材与拆解结果。
3. 补齐“中间流程可见性”，前端可查看每个阶段是否走过、当前在哪一步。
4. 保持前后端契约一致，避免并行开发期间字段漂移。

## 2. 已确认现状（审计结论）

1. 任务流程确实走过：`asset_extract -> asset_allocate -> prompt_generate -> image_generate -> copy_generate -> finalize`，最终状态 `success`。
2. 后端当前并未真实生图：`backend/app/workers/generation_worker.py` 在 `image_generate` 阶段写入的是 `.txt` 占位文件（`mock image for prompt`）。
3. `GET /api/v1/jobs/{job_id}/results` 返回的 `images[].image_url` 是本地路径字符串，不是可直接访问的图片 URL。
4. 前端 `ResultPanel` 按 `<img src={image_url}>` 渲染，拿到 `.txt` 路径后只能显示破图与替代文本。

## 3. 范围与硬约束

1. 先做 P1，再做 P2。
2. 所有接口字段以 `docs/OPENAPI.JSON` 为准；任何字段变化必须先改文档再改代码。
3. 用户可见文案统一中文。
4. 错误码保持现有体系，`E-1099` 仅兜底未知异常。
5. 后端回归必须通过：`pytest -q`。
6. 前端回归必须通过：`npm run lint && npm run build`。

## 4. 契约冻结与新增接口

### 4.1 既有接口语义修正（不改路径）

1. `GET /api/v1/jobs/{job_id}/results`
2. 字段 `images[].image_url` 语义固定为“前端可直接访问的图片 URL（http/https）”，不再返回本地文件系统路径。

### 4.2 新增接口 A：查询任务阶段轨迹

1. 路径：`GET /api/v1/jobs/{job_id}/stages`
2. 200 响应：

```json
{
  "job_id": "string",
  "items": [
    {
      "stage": "asset_extract",
      "status": "running",
      "stage_message": "正在提取素材",
      "created_at": "2026-02-24T01:16:38.795266+00:00"
    }
  ]
}
```

### 4.3 新增接口 B：查询本次生成资产拆解结果

1. 路径：`GET /api/v1/jobs/{job_id}/asset-breakdown`
2. 200 响应：

```json
{
  "job_id": "string",
  "session_id": "string",
  "content_mode": "food_scenic",
  "source_assets": [
    {
      "asset_id": "string",
      "asset_type": "text",
      "content": "用户输入文本"
    }
  ],
  "extracted": {
    "foods": ["烧麦", "奶茶"],
    "scenes": ["大召寺", "夜景街区"],
    "keywords": ["暖色", "旅行感"]
  },
  "created_at": "2026-02-24T01:16:38.956957+00:00"
}
```

### 4.4 文档同步文件

1. `docs/OPENAPI.JSON`
2. `docs/BACKEND.MD`
3. `docs/FRONTED.MD`

## 5. 任务分配

## 5.1 后端任务（Owner: Backend）

### B1（P1）真实生图替换 mock 文件

1. 修改 `backend/app/workers/generation_worker.py`。
2. 在 `image_generate` 阶段调用已配置 `image_model` 的真实生成能力，不再写 `.txt` 占位。
3. 生成文件落地为真实图片（如 `.png` 或 `.jpg`）。
4. 失败按既有错误码返回，并保留部分成功语义。

### B2（P1）图片 URL 可访问化

1. 修改 `backend/app/main.py`（或统一文件访问路由层）暴露生成图片静态访问路径。
2. 修改 `backend/app/repositories/result_repo.py` / `backend/app/services/generation_service.py`，确保 `image_url` 返回可访问 URL。
3. 兼容本地端口约定：后端 `8887`。

### B3（P1）资产提取拆解落库

1. 修改 `backend/app/workers/generation_worker.py` 与对应仓储层。
2. `asset_extract` 阶段输出结构化拆解结果（food/scenic/keywords）。
3. 拆解结果与 job 绑定并可查询。
4. `images[].asset_refs` 必须填充与图片 prompt 关联的资产引用。

### B4（P1）新增可观测接口

1. 新增 `GET /api/v1/jobs/{job_id}/stages`。
2. 新增 `GET /api/v1/jobs/{job_id}/asset-breakdown`。
3. 路由、schema、service、repository 全链路补齐。

### B5（P1）测试补齐

1. 新增/更新 `backend/tests/test_generation_worker.py`。
2. 新增接口测试：`backend/tests/test_generation_flow.py`（可新建）。
3. 覆盖点：
4. 真实图片结果不再是 `.txt`。
5. `image_url` 可访问（返回 200）。
6. `stages` 接口返回完整轨迹。
7. `asset-breakdown` 接口返回结构化拆解。
8. 全量 `pytest -q` 全绿。

### B6（P2）提示词质量修正

1. 修正当前 prompt 中 Python 字典字符串直接拼接问题。
2. 输出面向模型的自然语言提示词，不出现 `{'k': 'v'}` 这种原样序列化结构。
3. 按 `content_mode`（food/scenic/food_scenic）分别优化模板。

## 5.2 前端任务（Owner: Frontend）

### F1（P1）结果图渲染与异常提示

1. 修改 `frontend/src/components/ResultPanel.tsx`。
2. 只要 `image_url` 是可访问图片就正常渲染。
3. 图片加载失败时展示中文错误态，不再显示混乱替代文本。
4. 复制文案功能保持不变。

### F2（P1）任务阶段可视化

1. 修改 `frontend/src/api.ts`、`frontend/src/store.ts`。
2. 新增调用 `GET /api/v1/jobs/{job_id}/stages`。
3. 在生成面板展示阶段时间线（提取、分配、提示词、生图、文案、收尾）。

### F3（P1）资产拆解可视化

1. 新增调用 `GET /api/v1/jobs/{job_id}/asset-breakdown`。
2. 在结果区展示“本次识别素材”与“拆解关键词”。
3. 字段为空时展示“暂无拆解结果”，避免假数据显示。

### F4（P2）交互体验优化

1. 生成中锁定重复点击。
2. 失败信息直接透传后端 message（中文）。
3. 保持现有会话切换与草稿锁定行为不回归。

## 6. 联调验收标准

1. 点击生成后，结果区出现真实图片，不再出现 `.txt` 内容作为图片。
2. `GET /api/v1/jobs/{job_id}/results` 的 `images[].image_url` 在浏览器可直接打开。
3. 前端能看到阶段轨迹，不再“黑盒等待”。
4. 前端能看到资产拆解结果，回答“资产有提取拆解吗”。
5. 回归通过：
6. 后端：`pytest -q`
7. 前端：`npm run lint && npm run build`

## 7. 交付格式（前后端统一）

1. 变更文件清单（含路径）。
2. 按任务编号逐条说明（Backend: B1-B6，Frontend: F1-F4）。
3. 命令输出摘要（pytest / lint / build）。
4. 契约变更说明（字段级、接口级）。
