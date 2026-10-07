"""Application settings, read from environment variables with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

MB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    )
    upload_dir: Path = field(
        default_factory=lambda: Path(os.getenv("UPLOAD_DIR", "./data/uploads"))
    )
    # Maximum size of the uploaded file itself.
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "50")) * MB
    )
    # Zip-bomb protection: maximum total size after decompression.
    max_uncompressed_bytes: int = field(
        default_factory=lambda: int(os.getenv("MAX_UNCOMPRESSED_MB", "300")) * MB
    )
    max_zip_members: int = field(
        default_factory=lambda: int(os.getenv("MAX_ZIP_MEMBERS", "200"))
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
