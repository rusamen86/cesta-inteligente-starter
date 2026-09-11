from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from .media_store import StoredMedia
from .ocr_layout import normalize_vision_receipt_text


class OCRError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: Decimal | None
    engine: str
    page_count: int = 1


class OCREngine(Protocol):
    def extract(self, media: StoredMedia) -> OCRResult: ...


class MappingOCR:
    """Deterministic OCR adapter used by golden/integration tests."""

    def __init__(self, texts_by_storage_id: dict[str, str], *, confidence: Decimal | None = None) -> None:
        self.texts_by_storage_id = texts_by_storage_id
        self.confidence = confidence

    def extract(self, media: StoredMedia) -> OCRResult:
        try:
            text = self.texts_by_storage_id[media.storage_id]
        except KeyError as exc:
            raise OCRError(f"no OCR fixture for {media.storage_id}") from exc
        return OCRResult(text=text, confidence=self.confidence, engine="mapping-fixture")


class MacVisionOCR:
    """Local macOS Vision OCR through one fixed, prebuilt helper binary.

    The helper path is operator configuration, not message input. Invocation never uses a shell.
    """

    def __init__(self, helper_binary: str | Path, *, timeout_seconds: int = 45) -> None:
        helper = Path(helper_binary).resolve(strict=True)
        if helper.is_symlink() or not helper.is_file() or not os.access(helper, os.X_OK):
            raise ValueError("Vision helper must be a regular executable file")
        self.helper_binary = helper
        self.timeout_seconds = timeout_seconds

    def extract(self, media: StoredMedia) -> OCRResult:
        try:
            completed = subprocess.run(
                [str(self.helper_binary), str(media.path)],
                shell=False,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env={"PATH": "/usr/bin:/bin", "LANG": "es_ES.UTF-8"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OCRError("local Vision OCR failed") from exc
        try:
            payload = json.loads(completed.stdout)
            text = normalize_vision_receipt_text(
                str(payload["text"]),
                str(payload.get("right_column_text", "")),
            ).strip()
            raw_confidence = payload.get("confidence")
            confidence = Decimal(str(raw_confidence)) if raw_confidence is not None else None
            page_count = int(payload.get("page_count", 1))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OCRError("Vision helper returned an invalid response") from exc
        if not text:
            raise OCRError("Vision OCR returned no text")
        return OCRResult(
            text=text,
            confidence=confidence,
            engine="macos-vision",
            page_count=page_count,
        )
