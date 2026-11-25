from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from scanner.config import ScannerConfig
from scanner.store import StateStore


def parse_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        num = float(value)
        if math.isnan(num) or math.isinf(num):
            return default
        return num
    except (TypeError, ValueError):
        return default


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return []
    return []


def parse_iso_timestamp(value: Any) -> float | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Normalize fractional seconds to <=6 digits for Python ISO parsing consistency
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _extract_yes_probability(market: dict[str, Any]) -> tuple[float | None, str, list[tuple[str, float]]]:
    outcomes = [str(o) for o in parse_json_list(market.get("outcomes"))]
    raw_prices = parse_json_list(market.get("outcomePrices"))
    prices = [parse_float(p) for p in raw_prices]

    pairs: list[tuple[str, float]] = []
    for idx, price in enumerate(prices):
        if price is None:
            continue
        label = outcomes[idx] if idx < len(outcomes) else f"Outcome {idx + 1}"
        pairs.append((label, max(0.0, min(1.0, price))))

    if not pairs:
        last_trade = parse_float(market.get("lastTradePrice"))
        if last_trade is not None:
            p = max(0.0, min(1.0, last_trade))
            return p, "Yes", [("Yes", p), ("No", max(0.0, 1.0 - p))]
        return None, "Yes", []

    for label, price in pairs:
        if label.strip().lower() == "yes":
            return price, "Yes", pairs

    return pairs[0][1], pairs[0][0], pairs


@dataclass
class MarketSnapshotView:
    market_id: str
    label: str
    question: str
    price: float
    outcome_label: str
    outcome_pairs: list[tuple[str, float]]
    delta_1h: float | None
    delta_6h: float | None
    delta_24h: float | None
    delta_7d: float | None
    volume_24h: float
    liquidity: float
    spread: float | None


@dataclass
class EvaluatedSignal:
    event_id: str
    slug: str
    title: str
    url: str
    description_excerpt: str
    tags: list[str]
    created_at: str | None
    end_date: str | None
    age_hours: float | None
    is_multi_market: bool
    primary_market: MarketSnapshotView
    top_outcomes: list[dict[str, Any]]
    volume_24h: float
    volume_1wk: float
    liquidity: float
    open_interest: float
    comment_count: int
    spike_ratio: float
    is_flip: bool
    is_focus: bool
    tracks: list[str]
    score: float
    is_urgent: bool
    urgent_reasons: list[str]
    series_key: str | None = None
    all_market_prices: dict[str, float] = field(default_factory=dict)

    def to_item_dict(
        self,
        now_ts: float,
        guid: str,
        is_update: bool = False,
        prev_published_price: float | None = None,
    ) -> dict[str, Any]:
        pm = self.primary_market
        return {
            "guid": guid,
            "event_id": self.event_id,
            "slug": self.slug,
            "title": self.title,
            "headline": build_headline(self, is_update=is_update),
            "url": self.url,
            "is_update": is_update,
            "prev_published_price": round(prev_published_price, 4)
            if prev_published_price is not None
            else None,
            "published_ts": int(now_ts),
            "published_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            "score": round(self.score, 1),
            "tracks": list(self.tracks),
            "is_urgent": self.is_urgent,
            "urgent_reasons": list(self.urgent_reasons),
            "is_flip": self.is_flip,
            "is_focus": self.is_focus,
            "is_multi_market": self.is_multi_market,
            "primary_market_id": pm.market_id,
            "primary_label": pm.label,
            "outcome_label": pm.outcome_label,
            "price": round(pm.price, 4),
            "delta_1h": round(pm.delta_1h, 4) if pm.delta_1h is not None else None,
            "delta_6h": round(pm.delta_6h, 4) if pm.delta_6h is not None else None,
            "delta_24h": round(pm.delta_24h, 4) if pm.delta_24h is not None else None,
            "delta_7d": round(pm.delta_7d, 4) if pm.delta_7d is not None else None,
            "top_outcomes": self.top_outcomes,
            "volume_24h": round(self.volume_24h, 2),
            "volume_1wk": round(self.volume_1wk, 2),
            "spike_ratio": round(self.spike_ratio, 2),
            "liquidity": round(self.liquidity, 2),
            "open_interest": round(self.open_interest, 2),
            "comment_count": self.comment_count,
            "created_at": self.created_at,
            "end_date": self.end_date,
            "age_hours": round(self.age_hours, 1) if self.age_hours is not None else None,
            "tags": self.tags,
            "description_excerpt": self.description_excerpt,
        }


def format_usd_compact(amount: float) -> str:
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount:.0f}"


def build_headline(sig: EvaluatedSignal, is_update: bool = False) -> str:
    pm = sig.primary_market
    curr_pct = round(pm.price * 100)
    delta_ref = pm.delta_24h if pm.delta_24h is not None else pm.delta_6h

    badges: list[str] = []
    if is_update:
        badges.append("UPDATE")

    if delta_ref is not None and abs(delta_ref) >= 0.045:
        delta_pp = round(delta_ref * 100)
        prev_pct = max(0, min(100, curr_pct - delta_pp))
        direction = "급등" if delta_pp > 0 else "급락"
        sign = "+" if delta_pp > 0 else ""
        badges.append(f"{direction} {sign}{delta_pp}%p | {prev_pct}%→{curr_pct}%")
    elif "new-hot" in sig.tracks:
        badges.append(f"신규 {curr_pct}%")
    elif "surge" in sig.tracks:
        badges.append(f"자금급증 {sig.spike_ratio:.1f}x | {curr_pct}%")
    else:
        badges.append(f"현재 {curr_pct}%")

    if sig.is_flip:
        badges.append("판세반전")
    if sig.is_urgent:
        badges.append("긴급")

    tag_str = " · ".join(badges)
    focus_part = f" [{pm.label}]" if sig.is_multi_market and pm.label and pm.label != sig.title else ""
    return f"[{tag_str}] {sig.title}{focus_part} ({format_usd_compact(sig.volume_24h)}/24h)"


def _extract_tags(event: dict[str, Any]) -> tuple[list[str], set[str]]:
    raw_tags = event.get("tags") or []
    labels: list[str] = []
    normalized: set[str] = set()
    for t in raw_tags:
        if isinstance(t, dict):
            label = str(t.get("label") or t.get("slug") or "").strip()
            slug = str(t.get("slug") or "").strip().lower()
            if label and label not in labels:
                labels.append(label)
            if label:
                normalized.add(label.lower())
            if slug:
                normalized.add(slug)
        elif isinstance(t, str) and t.strip():
            labels.append(t.strip())
            normalized.add(t.strip().lower())
    return labels, normalized


def _is_excluded(event: dict[str, Any], norm_tags: set[str], cfg: ScannerConfig) -> str | None:
    if event.get("closed") is True or event.get("archived") is True:
        return "closed_or_archived"
    if event.get("active") is False:
        return "inactive"

    if norm_tags & cfg.exclude_tags:
        return "excluded_tag"

    title = str(event.get("title") or "").strip()
    if not title:
        return "missing_title"

    for rx in cfg.exclude_title_regexes:
        if rx.search(title):
            return "excluded_title_pattern"

    # Also guard short-term date-specific crypto price target markets
    if re.search(
        r"(?i)\b(btc|bitcoin|eth|ethereum|sol|solana|xrp|doge)\b.*\b(above|below|between|dip to|reach|hit)\b.*\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b",
        title,
    ):
        return "short_term_crypto"

    return None


def _is_focus_event(title: str, norm_tags: set[str], cfg: ScannerConfig) -> bool:
    if norm_tags & cfg.focus_tags:
        return True
    lowered = title.lower()
    return any(kw in lowered for kw in cfg.focus_keywords)


def _check_flip(curr_price: float, delta_ref: float | None) -> bool:
    if delta_ref is None or abs(delta_ref) < 0.04:
        return False
    prev = curr_price - delta_ref
    crossed_50 = (prev < 0.48 and curr_price >= 0.52) or (prev > 0.52 and curr_price <= 0.48)
    major_upset = (prev >= 0.75 and curr_price <= 0.60) or (prev <= 0.25 and curr_price >= 0.40)
    return crossed_50 or major_upset


def evaluate_events(
    events: list[dict[str, Any]],
    store: StateStore,
    cfg: ScannerConfig,
    now_ts: float,
) -> tuple[list[EvaluatedSignal], dict[str, int]]:
    stats: dict[str, int] = {
        "input_events": len(events),
        "excluded_noise": 0,
        "excluded_low_liquidity_or_volume": 0,
        "excluded_no_open_markets": 0,
        "excluded_high_spread": 0,
        "excluded_settled_stagnant": 0,
        "tracked_snapshots": 0,
        "candidate_signals": 0,
        "urgent_candidates": 0,
    }

    f = cfg.filters
    min_liq = float(f["min_liquidity"])
    min_vol24 = float(f["min_volume_24h"])
    max_spread = float(f["max_spread"])
    settled_high = float(f["settled_high"])
    settled_low = float(f["settled_low"])
    bypass_d24 = float(f["settled_bypass_delta_24h"])
    bypass_d6 = float(f["settled_bypass_delta_6h"])

    signals: list[EvaluatedSignal] = []

    for ev in events:
        ev_id = str(ev.get("id") or "")
        if not ev_id:
            continue

        tag_labels, norm_tags = _extract_tags(ev)
        excl_reason = _is_excluded(ev, norm_tags, cfg)
        if excl_reason is not None:
            stats["excluded_noise"] += 1
            continue

        liquidity = parse_float(ev.get("liquidity"), 0.0) or 0.0
        vol24 = parse_float(ev.get("volume24hr"), 0.0) or 0.0
        vol1wk = parse_float(ev.get("volume1wk"), 0.0) or 0.0
        open_interest = parse_float(ev.get("openInterest"), 0.0) or 0.0
        comment_count = int(parse_float(ev.get("commentCount"), 0.0) or 0)

        raw_markets = ev.get("markets") or []
        open_markets = [
            m
            for m in raw_markets
            if isinstance(m, dict)
            and m.get("closed") is not True
            and m.get("archived") is not True
            and m.get("active") is not False
        ]
        if not open_markets:
            stats["excluded_no_open_markets"] += 1
            continue

        # Fallback: sum market volumes/liquidity if event-level field is missing or zero
        if vol24 <= 0:
            vol24 = sum(parse_float(m.get("volume24hr"), 0.0) or 0.0 for m in open_markets)
        if liquidity <= 0:
            liquidity = sum(parse_float(m.get("liquidity"), 0.0) or 0.0 for m in open_markets)
        if vol1wk <= 0:
            vol1wk = sum(parse_float(m.get("volume1wk"), 0.0) or 0.0 for m in open_markets)

        # Build market views and snapshot prices
        market_views: list[MarketSnapshotView] = []
        market_prices: dict[str, float] = {}
        for m in open_markets:
            mid = str(m.get("id") or "")
            if not mid:
                continue
            price, outcome_label, pairs = _extract_yes_probability(m)
            if price is None:
                continue
            market_prices[mid] = price

            snap_6h, _ = store.find_reference_price(
                ev_id, mid, now_ts, target_hours=6.0, min_hours=3.0, max_hours=10.0
            )
            delta_6h = (price - snap_6h) if snap_6h is not None else None

            snap_24h, _ = store.find_reference_price(
                ev_id, mid, now_ts, target_hours=24.0, min_hours=18.0, max_hours=32.0
            )
            gamma_d24 = parse_float(m.get("oneDayPriceChange"))
            delta_24h = (price - snap_24h) if snap_24h is not None else gamma_d24

            delta_1h = parse_float(m.get("oneHourPriceChange"))
            delta_7d = parse_float(m.get("oneWeekPriceChange"))
            m_vol24 = parse_float(m.get("volume24hr"), 0.0) or 0.0
            m_liq = parse_float(m.get("liquidity"), 0.0) or 0.0
            spread = parse_float(m.get("spread"))

            label = str(m.get("groupItemTitle") or m.get("question") or "").strip()
            market_views.append(
                MarketSnapshotView(
                    market_id=mid,
                    label=label,
                    question=str(m.get("question") or "").strip(),
                    price=price,
                    outcome_label=outcome_label,
                    outcome_pairs=pairs,
                    delta_1h=delta_1h,
                    delta_6h=delta_6h,
                    delta_24h=delta_24h,
                    delta_7d=delta_7d,
                    volume_24h=m_vol24,
                    liquidity=m_liq,
                    spread=spread,
                )
            )

        if not market_views:
            stats["excluded_no_open_markets"] += 1
            continue

        # Record snapshots for all non-noise events with basic liquidity so future 6h/24h diffs work
        if liquidity >= 5000:
            store.record_snapshot(ev_id, now_ts, vol24, liquidity, market_prices)
            stats["tracked_snapshots"] += 1

        if liquidity < min_liq or vol24 < min_vol24:
            stats["excluded_low_liquidity_or_volume"] += 1
            continue

        is_multi = len(market_views) > 1

        # Select representative primary market (for multi-market: biggest meaningful mover)
        if is_multi:
            eligible_sub = [
                mv for mv in market_views if mv.volume_24h >= 5000 or mv.liquidity >= 5000
            ] or market_views

            def _mover_key(mv: MarketSnapshotView) -> tuple[float, float, float, float]:
                d24 = abs(mv.delta_24h) if mv.delta_24h is not None else 0.0
                d6 = abs(mv.delta_6h) if mv.delta_6h is not None else 0.0
                return (max(d24, d6), d24, mv.volume_24h, mv.price)

            primary = max(eligible_sub, key=_mover_key)
        else:
            primary = market_views[0]

        if primary.spread is not None and primary.spread > max_spread:
            stats["excluded_high_spread"] += 1
            continue

        abs_d24 = abs(primary.delta_24h) if primary.delta_24h is not None else 0.0
        abs_d6 = abs(primary.delta_6h) if primary.delta_6h is not None else 0.0

        # Skip stagnant settled markets unless there was a fresh sudden resolution swing
        lead_prob = max(mv.price for mv in market_views) if is_multi else primary.price
        is_extreme = lead_prob >= settled_high or (not is_multi and lead_prob <= settled_low)
        if is_extreme and abs_d24 < bypass_d24 and abs_d6 < bypass_d6:
            stats["excluded_settled_stagnant"] += 1
            continue

        created_ts = parse_iso_timestamp(ev.get("createdAt") or ev.get("creationDate"))
        age_hours = ((now_ts - created_ts) / 3600.0) if created_ts is not None else None

        daily_avg_7d = (vol1wk / 7.0) if vol1wk > 0 else 0.0
        if daily_avg_7d > 0:
            spike_ratio = vol24 / daily_avg_7d
        else:
            spike_ratio = 1.0

        is_flip = _check_flip(primary.price, primary.delta_24h) or _check_flip(
            primary.price, primary.delta_6h
        )
        title = str(ev.get("title") or "").strip()
        is_focus = _is_focus_event(title, norm_tags, cfg)

        # Evaluate 4 signal tracks
        tcfg = cfg.tracks
        tracks: list[str] = []

        mt = tcfg["movers"]
        if (
            (abs_d24 >= float(mt["min_delta_24h"]) or abs_d6 >= float(mt["min_delta_6h"]))
            and liquidity >= float(mt["min_liquidity"])
            and vol24 >= float(mt["min_volume_24h"])
        ):
            tracks.append("movers")

        st = tcfg["surge"]
        if (
            vol24 >= float(st["min_volume_24h"])
            and spike_ratio >= float(st["min_spike_ratio"])
            and liquidity >= float(st["min_liquidity"])
        ):
            tracks.append("surge")

        nt = tcfg["new_hot"]
        if (
            age_hours is not None
            and age_hours <= float(nt["max_age_hours"])
            and (vol24 >= float(nt["min_volume_24h"]) or liquidity >= float(nt["min_liquidity"]))
        ):
            tracks.append("new-hot")

        ft = tcfg["macro_tech"]
        if (
            is_focus
            and vol24 >= float(ft["min_volume_24h"])
            and liquidity >= float(ft["min_liquidity"])
            and (
                abs_d24 >= float(ft["min_delta_24h_or"])
                or abs_d6 >= float(ft["min_delta_24h_or"])
                or spike_ratio >= float(ft["min_spike_ratio_or"])
                or (age_hours is not None and age_hours <= float(ft["max_age_hours_or"]))
                or vol24 >= float(ft["high_volume_bypass"])
            )
        ):
            tracks.append("macro-tech")

        if not tracks:
            continue

        # Compute Signal Score
        effective_delta = max(abs_d24, abs_d6)
        vol_factor = math.log10(max(vol24, 10.0))
        score = (effective_delta * 100.0) * vol_factor
        score += 12.0 * min(max(spike_ratio - 1.0, 0.0), 5.0)
        if is_flip:
            score += 25.0
        if "new-hot" in tracks:
            score += 15.0
        if is_focus:
            score += 10.0

        # Evaluate Urgent (2nd-briefing conditional trigger)
        ucfg = cfg.urgent
        urgent_reasons: list[str] = []
        if (
            (abs_d24 >= float(ucfg["shock_delta_24h"]) or abs_d6 >= float(ucfg["shock_delta_6h"]))
            and vol24 >= float(ucfg["shock_min_volume_24h"])
            and liquidity >= float(ucfg["shock_min_liquidity"])
        ):
            urgent_reasons.append("확률 급변 충격")
        if (
            is_flip
            and vol24 >= float(ucfg["flip_min_volume_24h"])
            and liquidity >= float(ucfg["flip_min_liquidity"])
        ):
            urgent_reasons.append("대형 마켓 50% 판세 반전")
        if (
            vol24 >= float(ucfg["surge_min_volume_24h"])
            and spike_ratio >= float(ucfg["surge_min_spike_ratio"])
            and effective_delta >= float(ucfg["surge_min_delta_24h"])
        ):
            urgent_reasons.append("대규모 자금 쏠림")
        if (
            age_hours is not None
            and age_hours <= float(ucfg["new_max_age_hours"])
            and vol24 >= float(ucfg["new_min_volume_24h"])
            and liquidity >= float(ucfg["new_min_liquidity"])
        ):
            urgent_reasons.append("신규 대형 화제 마켓")

        is_urgent = bool(urgent_reasons)
        if is_urgent:
            stats["urgent_candidates"] += 1

        # Build Top 3 outcomes list for display
        if is_multi:
            sorted_mvs = sorted(market_views, key=lambda x: x.price, reverse=True)
            top_outcomes = [
                {
                    "label": mv.label,
                    "price": round(mv.price, 4),
                    "delta_24h": round(mv.delta_24h, 4) if mv.delta_24h is not None else None,
                    "is_primary": mv.market_id == primary.market_id,
                }
                for mv in sorted_mvs[:3]
            ]
            if not any(o["is_primary"] for o in top_outcomes):
                top_outcomes.append(
                    {
                        "label": primary.label,
                        "price": round(primary.price, 4),
                        "delta_24h": round(primary.delta_24h, 4)
                        if primary.delta_24h is not None
                        else None,
                        "is_primary": True,
                    }
                )
        else:
            top_outcomes = [
                {
                    "label": label,
                    "price": round(p, 4),
                    "delta_24h": round(primary.delta_24h, 4)
                    if idx == 0 and primary.delta_24h is not None
                    else None,
                    "is_primary": idx == 0,
                }
                for idx, (label, p) in enumerate(primary.outcome_pairs[:2])
            ]

        slug = str(ev.get("slug") or ev_id).strip()
        url = f"https://polymarket.com/event/{slug}"
        raw_desc = str(ev.get("description") or primary.question or "").strip()
        clean_desc = re.sub(r"\s+", " ", raw_desc)
        excerpt = (clean_desc[:420] + "…") if len(clean_desc) > 420 else clean_desc

        series_key: str | None = None
        raw_series = ev.get("series")
        if isinstance(raw_series, list) and raw_series and isinstance(raw_series[0], dict):
            s_id = raw_series[0].get("slug") or raw_series[0].get("id")
            if s_id:
                series_key = f"series:{s_id}"
        if series_key is None:
            m_gpu = re.match(r"(?i)^((?:h100|h200|b200|b300|a100|gb200)\s+average\s+rental\s+price)", title)
            if m_gpu:
                series_key = f"gpu:{m_gpu.group(1).lower()}"

        signals.append(
            EvaluatedSignal(
                event_id=ev_id,
                slug=slug,
                title=title,
                url=url,
                description_excerpt=excerpt,
                tags=tag_labels[:6],
                created_at=ev.get("createdAt") or ev.get("creationDate"),
                end_date=ev.get("endDate"),
                age_hours=age_hours,
                is_multi_market=is_multi,
                primary_market=primary,
                top_outcomes=top_outcomes,
                volume_24h=vol24,
                volume_1wk=vol1wk,
                liquidity=liquidity,
                open_interest=open_interest,
                comment_count=comment_count,
                spike_ratio=spike_ratio,
                is_flip=is_flip,
                is_focus=is_focus,
                tracks=tracks,
                score=score,
                is_urgent=is_urgent,
                urgent_reasons=urgent_reasons,
                series_key=series_key,
                all_market_prices=market_prices,
            )
        )

    signals.sort(key=lambda s: (s.is_urgent, s.score, s.volume_24h), reverse=True)

    # Deduplicate recurring series strips (e.g., 8 monthly GPU rental contracts -> keep top N)
    max_per_series = int(cfg.publishing.get("max_per_series", 0) or 0)
    if max_per_series > 0:
        series_counts: dict[str, int] = {}
        deduped: list[EvaluatedSignal] = []
        for sig in signals:
            if sig.series_key:
                cnt = series_counts.get(sig.series_key, 0)
                if cnt >= max_per_series:
                    stats["excluded_series_duplicate"] = (
                        stats.get("excluded_series_duplicate", 0) + 1
                    )
                    continue
                series_counts[sig.series_key] = cnt + 1
            deduped.append(sig)
        signals = deduped

    stats["candidate_signals"] = len(signals)
    stats["urgent_candidates"] = sum(1 for s in signals if s.is_urgent)
    return signals, stats


def select_items_to_emit(
    signals: list[EvaluatedSignal],
    store: StateStore,
    cfg: ScannerConfig,
    now_ts: float,
) -> list[dict[str, Any]]:
    pcfg = cfg.publishing
    cooldown_sec = float(pcfg["cooldown_hours"]) * 3600.0
    retrigger_delta = float(pcfg["retrigger_delta"])
    max_per_run = int(pcfg["max_items_per_run"])

    day_bucket = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y%m%d")
    hour_bucket = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y%m%d%H")

    emitted: list[dict[str, Any]] = []
    for sig in signals:
        if len(emitted) >= max_per_run:
            break

        prev = store.published.get(sig.event_id)
        curr_price = sig.primary_market.price
        is_update = False
        prev_price: float | None = None

        if prev is not None:
            elapsed = now_ts - float(prev.get("last_pub_ts", 0))
            prev_price = parse_float(prev.get("last_price"))
            same_market = str(prev.get("last_market_id") or "") == sig.primary_market.market_id
            further_move = (
                abs(curr_price - prev_price)
                if (prev_price is not None and same_market)
                else 0.0
            )
            newly_urgent = sig.is_urgent and not bool(prev.get("last_was_urgent", False))

            if elapsed < cooldown_sec:
                if further_move >= retrigger_delta or newly_urgent:
                    is_update = True
                else:
                    continue

        primary_track = sig.tracks[0]
        if is_update:
            guid = f"pm:{sig.event_id}:{primary_track}:upd:{hour_bucket}"
        else:
            guid = f"pm:{sig.event_id}:{primary_track}:{day_bucket}"

        item = sig.to_item_dict(
            now_ts=now_ts,
            guid=guid,
            is_update=is_update,
            prev_published_price=prev_price,
        )
        emitted.append(item)

        store.published[sig.event_id] = {
            "last_pub_ts": int(now_ts),
            "last_price": round(curr_price, 4),
            "last_market_id": sig.primary_market.market_id,
            "last_score": round(sig.score, 1),
            "last_tracks": list(sig.tracks),
            "last_was_urgent": sig.is_urgent,
            "guid": guid,
        }

    return emitted
