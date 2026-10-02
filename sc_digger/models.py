"""Datenmodelle und Config-Loader."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from sc_digger.schema import deep_merge


class DownloadKind(str, Enum):
    NATIVE = "native"        # downloadable=true -> automatisch ladbar
    HYPEDDIT = "hypeddit"    # Gate, wird nur gemeldet
    DROPLOUD = "droploud"    # Gate, wird nur gemeldet
    TONEDEN = "toneden"      # Gate, wird nur gemeldet
    ARTIST_UNION = "artistunion"
    STORE = "store"          # Bandcamp/Beatport etc. -> Kaufempfehlung
    CLOUD = "cloud"          # Drive/Dropbox im Beschreibungstext
    WETRANSFER = "wetransfer"   # ablaufend (7 Tage), manuell laden
    MEGA = "mega"               # clientseitig verschlüsselt, manuell laden
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
    download_error: str | None = None   # Grund, warum ein Download scheiterte (im Digest sichtbar)
    score: float = 0.0
    percentile: float = 0.0
    duplicate_of: str | None = None
    quality_report: dict[str, Any] | None = None
    key_camelot: str | None = None
    key_name: str | None = None
    notes: list[str] = field(default_factory=list)
    set_minutes: int | None = None   # gesetzt von pipeline.mark_sets
    reference_hit: bool = False  # von einem reference_accounts-Profil gerepostet/geliked
    velocity: float = 0.0  # Likes pro Stunde seit Upload, gefüllt in score_tracks()
    exploration_tag: str | None = None  # Tag, falls Track über einen Explorations-Suchlauf entdeckt wurde

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
    def load(cls, path: str | Path, local: str | Path | None = None) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # local=None: Pfad aus der Umgebungsvariable SC_DIGGER_CONFIG_LOCAL. Eine fehlende oder
        # leere Datei wird ignoriert; kaputtes YAML scheitert laut (Regel 7).
        local_path = Path(local) if local is not None else None
        if local_path is None:
            env = os.environ.get("SC_DIGGER_CONFIG_LOCAL")
            if env:
                local_path = Path(env)

        if local_path is not None and local_path.is_file():
            text = local_path.read_text(encoding="utf-8")
            if text.strip():
                try:
                    override = yaml.safe_load(text)
                except yaml.YAMLError as exc:
                    raise ValueError(
                        f"Lokale Override-Datei {local_path} ist kein gültiges YAML: {exc}"
                    ) from exc
                if not isinstance(override, dict):
                    raise ValueError(
                        f"Lokale Override-Datei {local_path} muss eine Zuordnung "
                        "(Schlüssel/Wert) auf oberster Ebene sein."
                    )
                if isinstance(raw, dict):
                    raw = deep_merge(raw, override)
                else:
                    raw = override
        return cls(raw)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def telegram_token(self) -> str | None:
        return os.environ.get("TELEGRAM_BOT_TOKEN")

    @property
    def telegram_chat_id(self) -> str | None:
        return os.environ.get("TELEGRAM_CHAT_ID")

    @property
    def soundcloud_auth_token(self) -> str | None:
        """OAuth-Token des eigenen SoundCloud-Accounts; nötig für Original-Downloads."""
        return os.environ.get("SOUNDCLOUD_AUTH_TOKEN") or None
