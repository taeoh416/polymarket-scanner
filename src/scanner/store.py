from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class StateStore:
    """Persists rolling price snapshots, dedup cooldown state, and 7-day published item history."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.snapshots_path = state_dir / "snapshots.json"
        self.published_path = state_dir / "published.json"
        self.history_path = state_dir / "items_history.json"

        self.snapshots: dict[str, list[dict[str, Any]]] = _read_json(self.snapshots_path, {})
        self.published: dict[str, dict[str, Any]] = _read_json(self.published_path, {})
        self.history: list[dict[str, Any]] = _read_json(self.history_path, [])

    def find_reference_price(
        self,
        event_id: str,
        market_id: str,
        now_ts: float,
        target_hours: float,
        min_hours: float,
        max_hours: float,
    ) -> tuple[float | None, float | None]:
        entries = self.snapshots.get(event_id, [])
        if not entries:
            return None, None

        best_price: float | None = None
        best_age: float | None = None
        best_diff = float("inf")

        for snap in entries:
            ts = snap.get("ts")
            prices = snap.get("prices")
            if ts is None or not isinstance(prices, dict) or market_id not in prices:
                continue
            age_hours = (now_ts - float(ts)) / 3600.0
            if age_hours < min_hours or age_hours > max_hours:
                continue
            diff = abs(age_hours - target_hours)
            if diff < best_diff:
                best_diff = diff
                best_price = float(prices[market_id])
                best_age = age_hours

        return best_price, best_age

    def record_snapshot(
        self,
        event_id: str,
        now_ts: float,
        volume_24h: float,
        liquidity: float,
        market_prices: dict[str, float],
        min_interval_sec: int = 1200,
    ) -> None:
        if not market_prices:
            return
        entries = self.snapshots.setdefault(event_id, [])
        if entries:
            last_ts = float(entries[-1].get("ts", 0))
            if (now_ts - last_ts) < min_interval_sec:
                # Overwrite most recent point if re-run rapidly within 20 minutes
                entries[-1] = {
                    "ts": int(now_ts),
                    "vol24h": round(volume_24h, 2),
                    "liq": round(liquidity, 2),
                    "prices": {k: round(v, 4) for k, v in market_prices.items()},
                }
                return

        entries.append(
            {
                "ts": int(now_ts),
                "vol24h": round(volume_24h, 2),
                "liq": round(liquidity, 2),
                "prices": {k: round(v, 4) for k, v in market_prices.items()},
            }
        )

    def prune_snapshots(self, now_ts: float, retention_hours: float) -> None:
        cutoff = now_ts - retention_hours * 3600.0
        pruned: dict[str, list[dict[str, Any]]] = {}
        for event_id, entries in self.snapshots.items():
            kept = [e for e in entries if float(e.get("ts", 0)) >= cutoff]
            if kept:
                pruned[event_id] = kept
        self.snapshots = pruned

    def prune_published(self, now_ts: float, retention_days: float) -> None:
        cutoff = now_ts - retention_days * 86400.0
        self.published = {
            ev_id: meta
            for ev_id, meta in self.published.items()
            if float(meta.get("last_pub_ts", 0)) >= cutoff
        }

    def merge_history(
        self,
        new_items: list[dict[str, Any]],
        now_ts: float,
        retention_days: float,
        max_items: int,
    ) -> list[dict[str, Any]]:
        cutoff = now_ts - retention_days * 86400.0
        seen_guids = {item["guid"] for item in new_items}
        merged = list(new_items)
        for existing in self.history:
            if existing.get("guid") in seen_guids:
                continue
            if float(existing.get("published_ts", 0)) < cutoff:
                continue
            merged.append(existing)

        merged.sort(
            key=lambda x: (float(x.get("published_ts", 0)), float(x.get("score", 0))),
            reverse=True,
        )
        self.history = merged[:max_items]
        return self.history

    def save(self) -> None:
        _atomic_write_json(self.snapshots_path, self.snapshots)
        _atomic_write_json(self.published_path, self.published)
        _atomic_write_json(self.history_path, self.history)
