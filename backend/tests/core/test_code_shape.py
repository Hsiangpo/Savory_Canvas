from __future__ import annotations

from pathlib import Path


def test_style_service_file_stays_under_800_lines():
    path = Path("backend/app/services/style_service.py")
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count < 800


def test_inspiration_flow_mixin_file_stays_under_800_lines():
    path = Path("backend/app/services/inspiration/flow_mixin.py")
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count < 800


def test_generation_pipeline_mixin_file_stays_under_800_lines():
    path = Path("backend/app/workers/generation/pipeline_mixin.py")
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count < 800


def test_generation_worker_file_stays_under_800_lines():
    path = Path("backend/app/workers/generation_worker.py")
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count < 800
