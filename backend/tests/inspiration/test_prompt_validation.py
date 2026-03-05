from __future__ import annotations


def test_internal_parameter_dump_detection_allows_normal_json_wording(client):
    service = client.app.state.services.inspiration

    assert service._looks_like_internal_parameter_dump("请输出 json 风格说明 { 城墙、泡馍、夜景 }") is False


def test_split_prompt_validation_rejects_any_multi_image_batch_instruction(client):
    service = client.app.state.services.inspiration

    assert service._validate_split_prompt_format("生成五张旅行攻略图，每张聚焦一个地点。", image_count=5) is False
