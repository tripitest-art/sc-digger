"""Datenmodelle und Config-Loader."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class DownloadKind(str, Enum):
    NATIVE = "native"        # downloadable=true -> automatisch ladbar
    HYPEDDIT = "hypeddit"    # Gate, wird nur gemeldet
    DROPLOUD = "droploud"    # Gate, wird nur gemeldet
    TONEDEN = "toneden"      # Gate, wird nur gemeldet
    ARTIST_UNION = "artistunion"
    STORE = "store"          # Bandcamp/Beatport etc. -> Kaufempfehlung
    CLOUD = "cloud"          # Drive/Dropbox/Mega im Beschreibungstext
    NONE = "none"            # nur Stream


@dataclass
class Track:
    id: int
    title: str
    url: str
    artist: str
    artist_url: str
    created_at: str          # ISO 8601
    duration_ms: int
    genre: str
    tags: list[str]
    description: str
    bpm: float | None
    plays: int
    likes: int
    reposts: int
    comments: int
    downloadable: bool
    has_downloads_left: bool
    purchase_url: str | None
    purchase_title: str | None
    # Werden in der Pipeline gefüllt
    download_kind: DownloadKind = DownloadKind.NONE
    download_link: str | None = None
    score: float = 0.0
    percentile: float = 0.0
    duplicate_of: str | None = None
    quality_report: dict[str, Any] | None = None
    key_camelot: str | None = None
    key_name: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def like_ratio(self) -> float:
        return self.likes / self.plays if self.plays else 0.0

    @property
    def repost_ratio(self) -> float:
        return self.reposts / self.plays if self.plays else 0.0

    @property
    def comment_ratio(self) -> float:
        return self.comments / self.plays if self.plays else 0.0


@dataclass
class Config:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def telegram_token(self) -> str | None:
        return os.environ.get("TELEGRAM_BOT_TOKEN")

    @property
    def telegram_chat_id(self) -> str | None:
        return os.environ.get("TELEGRAM_CHAT_ID")
