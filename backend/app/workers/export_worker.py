from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path
from typing import Any

from backend.app.core.utils import now_iso
from backend.app.repositories.export_repo import ExportRepository
from backend.app.repositories.job_repo import JobRepository
from backend.app.repositories.result_repo import ResultRepository
from backend.app.infra.storage import Storage


class ExportWorker:
    def __init__(
        self,
        export_repo: ExportRepository,
        job_repo: JobRepository,
        result_repo: ResultRepository,
        storage: Storage,
        font_paths: list[str] | None = None,
    ):
        self.export_repo = export_repo
        self.job_repo = job_repo
        self.result_repo = result_repo
        self.storage = storage
        self.font_paths = list(font_paths or [])

    def schedule(self, export_id: str) -> None:
        threading.Thread(target=lambda: asyncio.run(self._run(export_id)), daemon=True).start()

    async def _run(self, export_id: str) -> None:
        task = self.export_repo.get(export_id)
        if not task:
            return

        try:
            self.export_repo.update_state(
                export_id,
                status="running",
                file_path=None,
                error_code=None,
                error_message=None,
            )
            await asyncio.sleep(0.08)

            job = self.job_repo.get(task["job_id"])
            if not job or job["status"] not in {"success", "partial_success"}:
                self._fail(export_id, "生成任务尚未完成")
                return

            images = self.result_repo.list_images(task["job_id"])
            copy_result = self.result_repo.get_copy(task["job_id"])
            if not images or not copy_result:
                self._fail(export_id, "缺少可导出内容")
                return

            if task["export_format"] == "pdf":
                file_content = self._build_pdf_bytes(images=images, copy_result=copy_result)
                extension = "pdf"
            elif task["export_format"] == "long_image":
                file_content = self._build_long_image_bytes(images=images, copy_result=copy_result)
                extension = "png"
            else:
                file_content = str(copy_result.get("full_text") or "")
                extension = "txt"
            file_path = self.storage.save_export(filename=f"{export_id}.{extension}", content=file_content)
            self.export_repo.update_state(
                export_id,
                status="success",
                file_path=file_path,
                error_code=None,
                error_message=None,
            )
        except Exception:
            self._fail(export_id, "导出流程异常")

    def _fail(self, export_id: str, message: str) -> None:
        self.export_repo.update_state(
            export_id,
            status="failed",
            file_path=None,
            error_code="E-1005",
            error_message=message,
        )

    def _build_pdf_bytes(self, *, images: list[dict[str, Any]], copy_result: dict[str, Any]) -> bytes:
        from PIL import Image, ImageFont

        page_width = 1240
        page_height = 1754
        margin = 72
        section_font = self._load_font(size=34, bold=True, fallback=ImageFont)
        text_font = self._load_font(size=28, bold=False, fallback=ImageFont)
        small_font = self._load_font(size=24, bold=False, fallback=ImageFont)

        pages: list[Image.Image] = []
        pages.extend(
            self._build_image_pages(
                page_width=page_width,
                page_height=page_height,
                margin=margin,
                section_font=section_font,
                text_font=text_font,
                small_font=small_font,
                images=images,
            )
        )
        pages.extend(
            self._build_copy_pages(
                page_width=page_width,
                page_height=page_height,
                margin=margin,
                section_font=section_font,
                text_font=text_font,
                small_font=small_font,
                copy_result=copy_result,
            )
        )
        buffer = io.BytesIO()
        first_page = pages[0].convert("RGB")
        append_pages = [page.convert("RGB") for page in pages[1:]]
        first_page.save(buffer, format="PDF", save_all=True, append_images=append_pages, resolution=150.0)
        return buffer.getvalue()

    def _build_long_image_bytes(self, *, images: list[dict[str, Any]], copy_result: dict[str, Any]) -> bytes:
        from PIL import Image, ImageDraw, ImageFont

        canvas_width = 1080
        margin = 48
        image_gap = 24
        section_font = self._load_font(size=34, bold=True, fallback=ImageFont)
        text_font = self._load_font(size=28, bold=False, fallback=ImageFont)
        small_font = self._load_font(size=24, bold=False, fallback=ImageFont)
        prepared_images = self._prepare_long_image_blocks(
            images=images,
            canvas_width=canvas_width,
            margin=margin,
            text_font=text_font,
        )
        text_blocks = self._build_long_image_text_blocks(copy_result)

        total_height = margin
        for image in prepared_images:
            total_height += image.height + image_gap
        total_height += self._estimate_text_blocks_height(
            text_blocks=text_blocks,
            canvas_width=canvas_width,
            margin=margin,
            text_font=text_font,
            small_font=small_font,
        )
        total_height += margin

        canvas = Image.new("RGB", (canvas_width, total_height), "#fffdfa")
        draw = ImageDraw.Draw(canvas)
        y = margin

        for image in prepared_images:
            paste_x = (canvas_width - image.width) // 2
            canvas.paste(image, (paste_x, y))
            y += image.height + image_gap

        for title, content in text_blocks:
            draw.text((margin, y), title, font=small_font, fill="#f97352")
            y += 38
            y = self._draw_wrapped_text(
                draw,
                content or "无",
                text_font,
                margin,
                y,
                canvas_width - margin,
                "#2d2d2d",
                1.6,
            )
            y += 22

        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        return buffer.getvalue()

    def _prepare_long_image_blocks(self, *, images: list[dict[str, Any]], canvas_width: int, margin: int, text_font) -> list:
        from PIL import Image, ImageDraw

        prepared: list[Image.Image] = []
        max_width = canvas_width - margin * 2
        for item in images:
            image_path = self._resolve_image_file_path(item.get("image_path"))
            if image_path and image_path.is_file():
                with Image.open(image_path) as raw:
                    image = raw.convert("RGB")
                ratio = min(max_width / image.width, 1.0)
                resized = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
                prepared.append(resized)
                continue
            placeholder = Image.new("RGB", (max_width, 320), "#fff4f1")
            draw = ImageDraw.Draw(placeholder)
            draw.text((32, 132), "图片文件不存在，已跳过渲染。", font=text_font, fill="#d9464a")
            prepared.append(placeholder)
        return prepared

    def _build_long_image_text_blocks(self, copy_result: dict[str, Any]) -> list[tuple[str, str]]:
        blocks: list[tuple[str, str]] = []
        title = str(copy_result.get("title") or "").strip()
        if title:
            blocks.append(("标题", title))
        intro = str(copy_result.get("intro") or "").strip()
        if intro:
            blocks.append(("导语", intro))
        sections = copy_result.get("guide_sections")
        if isinstance(sections, list):
            for index, section in enumerate(sections, start=1):
                if not isinstance(section, dict):
                    continue
                heading = str(section.get("heading") or f"段落 {index}").strip()
                content = str(section.get("content") or "").strip()
                if content:
                    blocks.append((heading, content))
        ending = str(copy_result.get("ending") or "").strip()
        if ending:
            blocks.append(("结语", ending))
        if not blocks:
            blocks.append(("文案", str(copy_result.get("full_text") or "无").strip() or "无"))
        return blocks

    def _estimate_text_blocks_height(
        self,
        *,
        text_blocks: list[tuple[str, str]],
        canvas_width: int,
        margin: int,
        text_font,
        small_font,
    ) -> int:
        from PIL import Image, ImageDraw

        probe = Image.new("RGB", (canvas_width, 10), "#ffffff")
        draw = ImageDraw.Draw(probe)
        line_height = int(getattr(text_font, "size", 24) * 1.6)
        total_height = 0
        max_width = canvas_width - margin * 2
        for _title, content in text_blocks:
            total_height += max(getattr(small_font, "size", 24), 24) + 14
            text = str(content or "").splitlines() or [""]
            line_count = 0
            for raw_line in text:
                current = ""
                for char in raw_line:
                    probe_text = current + char
                    box = draw.textbbox((0, 0), probe_text, font=text_font)
                    if box[2] - box[0] <= max_width:
                        current = probe_text
                        continue
                    line_count += 1
                    current = char
                line_count += 1
            total_height += line_count * line_height + 22
        return total_height

    def _build_image_pages(
        self,
        *,
        page_width: int,
        page_height: int,
        margin: int,
        section_font,
        text_font,
        small_font,
        images: list[dict[str, Any]],
    ) -> list:
        from PIL import Image, ImageDraw

        pages: list[Image.Image] = []
        for item in images:
            page = Image.new("RGB", (page_width, page_height), "#ffffff")
            image_path = self._resolve_image_file_path(item.get("image_path"))
            if image_path and image_path.is_file():
                with Image.open(image_path) as raw:
                    image = raw.convert("RGB")
                max_width = page_width - margin * 2
                max_height = page_height - margin * 2
                ratio = min(max_width / image.width, max_height / image.height)
                resized = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
                paste_x = (page_width - resized.width) // 2
                paste_y = (page_height - resized.height) // 2
                page.paste(resized, (paste_x, paste_y))
            else:
                draw = ImageDraw.Draw(page)
                y = margin + 140
                draw.text((margin, y), "图片文件不存在，已跳过渲染。", font=text_font, fill="#d9464a")
            pages.append(page)
        return pages

    def _build_copy_pages(
        self,
        *,
        page_width: int,
        page_height: int,
        margin: int,
        section_font,
        text_font,
        small_font,
        copy_result: dict[str, Any],
    ) -> list:
        from PIL import Image, ImageDraw

        pages: list[Image.Image] = []
        page = Image.new("RGB", (page_width, page_height), "#fffdfa")
        draw = ImageDraw.Draw(page)
        y = margin
        blocks: list[tuple[str, str]] = []
        title = str(copy_result.get("title") or "").strip()
        if title:
            blocks.append(("标题", title))
        intro = str(copy_result.get("intro") or "").strip()
        if intro:
            blocks.append(("导语", intro))
        sections = copy_result.get("guide_sections")
        if isinstance(sections, list):
            for idx, section in enumerate(sections, start=1):
                if not isinstance(section, dict):
                    continue
                heading = str(section.get("heading") or f"段落 {idx}").strip()
                content = str(section.get("content") or "").strip()
                blocks.append((heading, content))
        ending = str(copy_result.get("ending") or "").strip()
        if ending:
            blocks.append(("结语", ending))
        if not blocks:
            blocks.append(("文案", "无"))

        for title, content in blocks:
            estimated_line_height = int(getattr(text_font, "size", 28) * 1.6)
            estimated_lines = max(1, len(content) // 20 + 1)
            estimated_height = 56 + estimated_line_height * estimated_lines + 24
            if y + estimated_height > page_height - margin:
                pages.append(page)
                page = Image.new("RGB", (page_width, page_height), "#fffdfa")
                draw = ImageDraw.Draw(page)
                y = margin
            draw.text((margin, y), title, font=small_font, fill="#f97352")
            y += 38
            y = self._draw_wrapped_text(draw, content or "无", text_font, margin, y, page_width - margin, "#2d2d2d", 1.6)
            y += 22
        pages.append(page)
        return pages

    def _draw_wrapped_text(
        self,
        draw,
        text: str,
        font,
        x: int,
        y: int,
        max_x: int,
        fill: str,
        line_height_ratio: float,
    ) -> int:
        max_width = max_x - x
        line_height = int(getattr(font, "size", 24) * line_height_ratio)
        for line in str(text or "").splitlines() or [""]:
            current = ""
            for char in line:
                probe = current + char
                box = draw.textbbox((0, 0), probe, font=font)
                width = box[2] - box[0]
                if width <= max_width:
                    current = probe
                    continue
                if current:
                    draw.text((x, y), current, font=font, fill=fill)
                    y += line_height
                current = char
            draw.text((x, y), current or " ", font=font, fill=fill)
            y += line_height
        return y

    def _resolve_image_file_path(self, image_path: Any) -> Path | None:
        if not isinstance(image_path, str) or not image_path.strip():
            return None
        raw = Path(image_path)
        if raw.is_file():
            return raw
        normalized = image_path.replace("\\", "/").lstrip("/")
        if normalized.startswith("static/"):
            normalized = normalized[len("static/") :]
        return self.storage.base_dir / normalized

    def _load_font(self, *, size: int, bold: bool, fallback):
        candidates = [
            *self.font_paths,
            "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for font_path in candidates:
            try:
                return fallback.truetype(font_path, size=size)
            except Exception:
                continue
        return fallback.load_default()

