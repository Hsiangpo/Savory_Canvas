## Goal
对 Savory Canvas 进行一次覆盖前端交互、后端逻辑、数据库持久化、浏览器联调与门禁脚本的全面审计，确认真实可复现问题，并给出可落地修复顺序。

## Current Phase
Phase Audit

## Phases
### Phase Audit: 体检与取证
- [x] 技能与执行约束梳理
- [x] 自动化门禁验证
- [x] 浏览器真实联调
- [x] 数据库状态核对
- [x] 汇总高置信问题
- **Status:** complete

## Current Findings (2026-03-09)
- `pytest backend/tests -q` 通过（164 passed）。
- 前端 `npm run build` 通过。
- `scripts/check_limits.py` 在 Windows GBK 控制台会因 Unicode 输出崩溃；启用 UTF-8 后显示 `backend/app/services/inspiration_service.py` 已超 1000 行。
- 风格抽屉仍显示“系统风格 / 全局风格”残留文案。
- 生成区存在重复轮询与状态清空，可能引发右栏闪烁。
