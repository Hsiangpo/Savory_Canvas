from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import threading
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import request
from urllib.request import Request
from uuid import uuid4

from backend.app.core.utils import new_id, now_iso
from backend.app.repositories.asset_repo import AssetRepository

TRANSCRIBE_LANGUAGE = os.getenv("SAVORY_CANVAS_TRANSCRIBE_LANGUAGE", "zh")
TRANSCRIBE_TIMEOUT_SECONDS = int(os.getenv("SAVORY_CANVAS_TRANSCRIBE_TIMEOUT_SECONDS", "180"))
TRANSCRIBE_TEMPERATURE = os.getenv("SAVORY_CANVAS_TRANSCRIBE_TEMPERATURE", "0")
TRANSCRIBE_PROMPT = os.getenv(
    "SAVORY_CANVAS_TRANSCRIBE_PROMPT",
    "请使用简体中文准确转写，保留地名、人名、品牌名与口语，不要翻译，不要总结，不要改写。",
)
TRANSCRIBE_MIN_MEDIA_BYTES = 1024


class TranscriptWorker:
    def __init__(self, asset_repo: AssetRepository, storage_base_dir: Path):
        self.asset_repo = asset_repo
        self.storage_base_dir = storage_base_dir

    def schedule(
        self,
        session_id: str,
        asset_id: str,
        file_name: str,
        file_path: str,
        provider: dict[str, Any],
        model_name: str,
    ) -> None:
        threading.Thread(
            target=lambda: asyncio.run(
                self._run(
                    session_id=session_id,
                    asset_id=asset_id,
                    file_name=file_name,
                    file_path=file_path,
                    provider=provider,
                    model_name=model_name,
                )
            ),
            daemon=True,
        ).start()

    async def _run(
        self,
        session_id: str,
        asset_id: str,
        file_name: str,
        file_path: str,
        provider: dict[str, Any],
        model_name: str,
    ) -> None:
        try:
            await asyncio.sleep(0.08)
            text, segments = await asyncio.to_thread(
                self._transcribe_file,
                file_name,
                file_path,
                provider,
                model_name,
            )
            self.asset_repo.update_transcript(
                asset_id=asset_id,
                status="ready",
                text=text,
                segments=segments,
                error_code=None,
                error_message=None,
                updated_at=now_iso(),
            )
            self.asset_repo.update_status(asset_id, "ready")
            self.asset_repo.create(
                {
                    "id": new_id(),
                    "session_id": session_id,
                    "asset_type": "transcript",
                    "content": text,
                    "file_path": file_path,
                    "status": "ready",
                    "created_at": now_iso(),
                }
            )
        except Exception:
            self.asset_repo.update_status(asset_id, "failed")
            self.asset_repo.update_transcript(
                asset_id=asset_id,
                status="failed",
                text=None,
                segments=None,
                error_code="E-1001",
                error_message="视频转写失败",
                updated_at=now_iso(),
            )

    def _transcribe_file(
        self,
        file_name: str,
        file_path: str,
        provider: dict[str, Any],
        model_name: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        path = self._resolve_file_path(file_path)
        if not path.is_file() or path.stat().st_size < TRANSCRIBE_MIN_MEDIA_BYTES:
            raise RuntimeError(f"转写文件无效: {file_name}")
        if not self._looks_like_supported_media(path):
            raise RuntimeError(f"转写文件格式不支持: {file_name}")
        payload = self._transcribe_via_api(path, provider=provider, model_name=model_name)
        text = str(payload.get("text") or payload.get("output_text") or "").strip()
        if not text:
            raise RuntimeError("转写服务未返回有效文本")
        return text, self._normalize_segments(payload.get("segments"))

    def _transcribe_via_api(self, file_path: Path, *, provider: dict[str, Any], model_name: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for endpoint in self._build_transcription_endpoints(provider.get("base_url", "")):
            try:
                return self._post_transcription_request(
                    endpoint=endpoint,
                    api_key=str(provider.get("api_key") or ""),
                    model_name=model_name,
                    file_path=file_path,
                )
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError("转写服务调用失败") from last_error

    def _build_transcription_endpoints(self, base_url: str) -> list[str]:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            return []
        endpoints = [f"{normalized_base_url}/audio/transcriptions"]
        if not normalized_base_url.lower().endswith("/v1"):
            endpoints.append(f"{normalized_base_url}/v1/audio/transcriptions")
        return list(dict.fromkeys(endpoints))

    def _post_transcription_request(self, *, endpoint: str, api_key: str, model_name: str, file_path: Path) -> dict[str, Any]:
        body, boundary = self._build_transcription_body(file_path=file_path, model_name=model_name)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        }
        upstream_request = Request(url=endpoint, method="POST", headers=headers, data=body)
        try:
            with request.urlopen(upstream_request, timeout=TRANSCRIBE_TIMEOUT_SECONDS) as response:
                raw_text = response.read().decode("utf-8")
        except url_error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(body_text or f"HTTP {getattr(exc, 'code', 0)}") from exc
        except (TimeoutError, url_error.URLError, OSError) as exc:
            raise RuntimeError(str(exc)) from exc

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("转写服务响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("转写服务响应结构错误")
        return payload

    def _build_transcription_body(self, *, file_path: Path, model_name: str) -> tuple[bytes, str]:
        boundary = f"----SavoryCanvasBoundary{uuid4().hex}"
        media_bytes = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        fields = self._build_transcription_fields(model_name)
        chunks: list[bytes] = []
        for field_name, field_value in fields:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode("utf-8"),
                    str(field_value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode("utf-8"),
                media_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        return b"".join(chunks), boundary

    def _build_transcription_fields(self, model_name: str) -> list[tuple[str, str]]:
        lowered = model_name.strip().lower()
        fields: list[tuple[str, str]] = [
            ("model", model_name),
            ("language", TRANSCRIBE_LANGUAGE),
            ("temperature", TRANSCRIBE_TEMPERATURE),
        ]
        if TRANSCRIBE_PROMPT.strip():
            fields.append(("prompt", TRANSCRIBE_PROMPT.strip()))
        if "gpt-4o" in lowered and "transcribe" in lowered:
            fields.append(("response_format", "json"))
            return fields
        fields.append(("response_format", "verbose_json"))
        fields.append(("timestamp_granularities[]", "segment"))
        return fields

    def _resolve_file_path(self, file_path: str) -> Path:
        path = Path(file_path)
        if path.is_absolute():
            return path
        return (self.storage_base_dir / path).resolve()

    def _looks_like_supported_media(self, file_path: Path) -> bool:
        try:
            header = file_path.read_bytes()[:32]
        except OSError:
            return False
        lowered_name = file_path.name.lower()
        if lowered_name.endswith((".mp4", ".m4v", ".mov")) and b"ftyp" in header:
            return True
        if lowered_name.endswith((".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")):
            return True
        return False

    def _normalize_segments(self, raw_segments: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_segments, list):
            return []
        segments: list[dict[str, Any]] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            segments.append(
                {
                    "start": item.get("start", 0),
                    "end": item.get("end", item.get("start", 0)),
                    "text": text,
                }
            )
        return segments
