from __future__ import annotations

import io
from typing import Any


def postprocess_generated_image(image_bytes: bytes, extension: str) -> tuple[bytes, str]:
    trimmed_bytes, trimmed_extension = _trim_uniform_outer_border(image_bytes=image_bytes, extension=extension)
    return trimmed_bytes, trimmed_extension


def _trim_uniform_outer_border(*, image_bytes: bytes, extension: str) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except Exception:
        return image_bytes, extension
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            working = image.copy()
            rgb = working.convert("RGB")
            crop_box = _detect_uniform_border_crop_box(rgb)
            if crop_box is None:
                return image_bytes, extension
            left, top, right, bottom = crop_box
            if right - left <= 0 or bottom - top <= 0:
                return image_bytes, extension
            cropped = working.crop(crop_box)
            output = io.BytesIO()
            normalized_extension = (extension or "").lower().strip()
            if normalized_extension in {"jpg", "jpeg"}:
                if cropped.mode in {"RGBA", "LA", "P"}:
                    cropped = cropped.convert("RGB")
                cropped.save(output, format="JPEG", quality=95, optimize=True)
                return output.getvalue(), "jpg"
            if normalized_extension == "webp":
                cropped.save(output, format="WEBP", quality=95)
                return output.getvalue(), "webp"
            if normalized_extension == "gif":
                cropped.save(output, format="GIF")
                return output.getvalue(), "gif"
            if normalized_extension == "avif":
                if cropped.mode in {"RGBA", "LA", "P"}:
                    cropped = cropped.convert("RGB")
                cropped.save(output, format="AVIF")
                return output.getvalue(), "avif"
            cropped.save(output, format="PNG", optimize=True)
            return output.getvalue(), "png"
    except Exception:
        return image_bytes, extension


def _detect_uniform_border_crop_box(image_rgb: Any) -> tuple[int, int, int, int] | None:
    width, height = image_rgb.size
    if width < 64 or height < 64:
        return None
    pixels = image_rgb.load()
    border_depth = min(2, width // 16, height // 16) or 1
    border_samples: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(border_depth):
            border_samples.append(pixels[x, y])
            border_samples.append(pixels[width - 1 - x, y])
    for x in range(width):
        for y in range(border_depth):
            border_samples.append(pixels[x, y])
            border_samples.append(pixels[x, height - 1 - y])
    if not border_samples:
        return None
    base_color = _median_rgb(border_samples)
    tolerance = 22
    row_step = max(1, width // 256)
    col_step = max(1, height // 256)

    def is_border_pixel(pixel: tuple[int, int, int]) -> bool:
        return (
            abs(int(pixel[0]) - base_color[0]) <= tolerance
            and abs(int(pixel[1]) - base_color[1]) <= tolerance
            and abs(int(pixel[2]) - base_color[2]) <= tolerance
        )

    def row_border_ratio(y: int) -> float:
        total = 0
        border_count = 0
        for x in range(0, width, row_step):
            total += 1
            if is_border_pixel(pixels[x, y]):
                border_count += 1
        return (border_count / total) if total else 0.0

    def col_border_ratio(x: int) -> float:
        total = 0
        border_count = 0
        for y in range(0, height, col_step):
            total += 1
            if is_border_pixel(pixels[x, y]):
                border_count += 1
        return (border_count / total) if total else 0.0

    threshold = 0.985
    top = 0
    while top < height - 1 and row_border_ratio(top) >= threshold:
        top += 1
    bottom = height - 1
    while bottom > top and row_border_ratio(bottom) >= threshold:
        bottom -= 1
    left = 0
    while left < width - 1 and col_border_ratio(left) >= threshold:
        left += 1
    right = width - 1
    while right > left and col_border_ratio(right) >= threshold:
        right -= 1

    margin_left = left
    margin_top = top
    margin_right = width - 1 - right
    margin_bottom = height - 1 - bottom
    if max(margin_left, margin_top, margin_right, margin_bottom) < 10:
        return None
    cropped_width = right - left + 1
    cropped_height = bottom - top + 1
    if cropped_width < int(width * 0.55) or cropped_height < int(height * 0.55):
        return None
    return left, top, right + 1, bottom + 1


def _median_rgb(values: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not values:
        return 255, 255, 255
    channels = [[], [], []]
    for red, green, blue in values:
        channels[0].append(int(red))
        channels[1].append(int(green))
        channels[2].append(int(blue))
    medians: list[int] = []
    for channel in channels:
        channel.sort()
        medians.append(channel[len(channel) // 2])
    return medians[0], medians[1], medians[2]
