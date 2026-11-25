from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scanner.config import ScannerConfig
from scanner.engine import evaluate_events, select_items_to_emit
from scanner.polymarket import PolymarketFetchError, fetch_active_events
from scanner.publisher import publish_outputs
from scanner.store import StateStore


def run_scan(
    config_path: Path,
    state_dir: Path,
    out_dir: Path,
    base_url: str | None = None,
    input_file: Path | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    cfg = ScannerConfig.from_file(config_path)
    if base_url:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        raw["base_url"] = base_url.rstrip("/")
        cfg = ScannerConfig.from_dict(raw)

    ts = time.time() if now_ts is None else now_ts
    store = StateStore(state_dir)

    if input_file is not None:
        raw_payload = json.loads(input_file.read_text(encoding="utf-8"))
        events = raw_payload["events"] if isinstance(raw_payload, dict) else raw_payload
        fetch_meta: dict[str, Any] = {"source": str(input_file), "unique_events": len(events)}
    else:
        try:
            events, fetch_meta = fetch_active_events(cfg)
        except PolymarketFetchError as exc:
            # Preserve existing valid feeds and only update health.json with error status
            out_dir.mkdir(parents=True, exist_ok=True)
            health = {
                "status": "fetch_error_preserved_previous",
                "updated_at_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "error": str(exc),
            }
            (out_dir / "health.json").write_text(
                json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            raise

    if not events:
        raise PolymarketFetchError("Gamma API returned 0 active events; preserving previous feeds")

    signals, eval_stats = evaluate_events(events=events, store=store, cfg=cfg, now_ts=ts)
    newly_emitted = select_items_to_emit(signals=signals, store=store, cfg=cfg, now_ts=ts)

    pcfg = cfg.publishing
    store.prune_snapshots(ts, retention_hours=float(pcfg["snapshot_retention_hours"]))
    store.prune_published(ts, retention_days=float(pcfg["history_retention_days"]))
    history = store.merge_history(
        new_items=newly_emitted,
        now_ts=ts,
        retention_days=float(pcfg["history_retention_days"]),
        max_items=int(pcfg["max_history_items"]),
    )
    store.save()

    return publish_outputs(
        out_dir=out_dir,
        cfg=cfg,
        live_signals=signals,
        history_items=history,
        newly_emitted=newly_emitted,
        now_ts=ts,
        fetch_meta=fetch_meta,
        eval_stats=eval_stats,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Polymarket 세상물정 스캐너")
    parser.add_argument("--config", type=Path, default=Path("config/scanner.json"))
    parser.add_argument("--state-dir", type=Path, default=Path("dist/_state"))
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    parser.add_argument("--base-url", type=str, default=None)
    parser.add_argument("--input-file", type=Path, default=None, help="Offline JSON fixture path")
    parser.add_argument(
        "--soft-fail",
        action="store_true",
        help="Exit 0 when API fails if previous all.xml already exists",
    )
    args = parser.parse_args(argv)

    try:
        health = run_scan(
            config_path=args.config,
            state_dir=args.state_dir,
            out_dir=args.out_dir,
            base_url=args.base_url,
            input_file=args.input_file,
        )
    except PolymarketFetchError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        if args.soft_fail and (args.out_dir / "all.xml").exists():
            print("[WARN] Preserved previous valid feeds; exiting 0 due to --soft-fail", file=sys.stderr)
            return 0
        return 1

    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
