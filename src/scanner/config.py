from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScannerConfig:
    base_url: str
    site_title: str
    site_description: str
    fetch: dict[str, Any]
    filters: dict[str, Any]
    focus_tags: frozenset[str]
    focus_keywords: tuple[str, ...]
    tracks: dict[str, dict[str, Any]]
    urgent: dict[str, Any]
    publishing: dict[str, Any]
    exclude_tags: frozenset[str] = field(default_factory=frozenset)
    exclude_title_regexes: tuple[re.Pattern[str], ...] = field(default_factory=tuple)

    @classmethod
    def from_file(cls, path: Path) -> "ScannerConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScannerConfig":
        filters = raw["filters"]
        exclude_tags = frozenset(t.strip().lower() for t in filters.get("exclude_tags", []))
        exclude_regexes = tuple(
            re.compile(pat) for pat in filters.get("exclude_title_patterns", [])
        )
        focus_tags = frozenset(t.strip().lower() for t in raw.get("focus_tags", []))
        focus_keywords = tuple(k.lower() for k in raw.get("focus_keywords", []))
        return cls(
            base_url=raw["base_url"].rstrip("/"),
            site_title=raw["site_title"],
            site_description=raw["site_description"],
            fetch=raw["fetch"],
            filters=filters,
            focus_tags=focus_tags,
            focus_keywords=focus_keywords,
            tracks=raw["tracks"],
            urgent=raw["urgent"],
            publishing=raw["publishing"],
            exclude_tags=exclude_tags,
            exclude_title_regexes=exclude_regexes,
        )
