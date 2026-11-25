from __future__ import annotations

import html
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from scanner.config import ScannerConfig
from scanner.engine import EvaluatedSignal, format_usd_compact

KST = timezone(timedelta(hours=9))
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)

FEED_SPECS: list[tuple[str, str, str, str | None]] = [
    ("all.xml", "통합 다이제스트", "확률 급변·자금 쏠림·신규 화제·매크로/테크 통합 상위 시그널", None),
    ("movers.xml", "확률 급변 (Odds Movers)", "24시간 10%p 이상 또는 6시간 7%p 이상 확률이 급변한 마켓", "movers"),
    ("surge.xml", "자금 쏠림 (Money Surge)", "주간 일평균 대비 3배 이상 거래량이 급증한 마켓", "surge"),
    ("new-hot.xml", "신규 화제 (New Radar)", "최근 48시간 내 개설되어 자금이 빠르게 몰리는 신규 마켓", "new-hot"),
    ("macro-tech.xml", "매크로·테크·지정학", "거시경제·금융·AI/빅테크·지정학/정치 큐레이션", "macro-tech"),
]

TRACK_LABELS = {
    "movers": "확률 급변",
    "surge": "자금 쏠림",
    "new-hot": "신규 화제",
    "macro-tech": "매크로·테크",
}


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val * 100:.1f}%"


def _fmt_delta(val: float | None) -> str:
    if val is None:
        return "-"
    pp = val * 100.0
    sign = "+" if pp > 0 else ""
    return f"{sign}{pp:.1f}%p"


def _fmt_kst(iso_str: str | None) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except ValueError:
        return iso_str


def _render_item_html(item: dict[str, Any]) -> str:
    track_names = ", ".join(TRACK_LABELS.get(t, t) for t in item.get("tracks", []))
    urgent_line = ""
    if item.get("is_urgent"):
        reasons = ", ".join(item.get("urgent_reasons", []))
        urgent_line = f"<p><strong>🚨 긴급 시그널:</strong> {html.escape(reasons)}</p>"

    outcomes_rows = []
    for o in item.get("top_outcomes", []):
        mark = " ★" if o.get("is_primary") else ""
        d24 = f" ({_fmt_delta(o.get('delta_24h'))})" if o.get("delta_24h") is not None else ""
        outcomes_rows.append(
            f"<li>{html.escape(str(o.get('label', '')))}{mark}: "
            f"<strong>{_fmt_pct(o.get('price'))}</strong>{html.escape(d24)}</li>"
        )
    outcomes_html = f"<ul>{''.join(outcomes_rows)}</ul>" if outcomes_rows else ""

    tags = ", ".join(item.get("tags", [])) or "-"
    excerpt = html.escape(item.get("description_excerpt") or "-")
    url = html.escape(item.get("url") or "")

    return (
        f"{urgent_line}"
        f"<p><strong>트랙:</strong> {html.escape(track_names)} · "
        f"<strong>Score:</strong> {item.get('score')} · "
        f"<strong>판세 반전:</strong> {'예' if item.get('is_flip') else '아니오'}</p>"
        f"<p><strong>확률 요약 ({html.escape(str(item.get('primary_label') or item.get('outcome_label') or 'Yes'))}):</strong> "
        f"현재 <strong>{_fmt_pct(item.get('price'))}</strong> | "
        f"6h {_fmt_delta(item.get('delta_6h'))} | "
        f"24h {_fmt_delta(item.get('delta_24h'))} | "
        f"7d {_fmt_delta(item.get('delta_7d'))}</p>"
        f"{outcomes_html}"
        f"<p><strong>자금 흐름:</strong> 24h 거래량 {format_usd_compact(float(item.get('volume_24h', 0)))} "
        f"(7일 일평균 대비 {item.get('spike_ratio', 1.0):.1f}x) · "
        f"유동성 {format_usd_compact(float(item.get('liquidity', 0)))} · "
        f"미결제약정 {format_usd_compact(float(item.get('open_interest', 0)))} · "
        f"댓글 {item.get('comment_count', 0)}</p>"
        f"<p><strong>일정:</strong> 개설 {_fmt_kst(item.get('created_at'))} · "
        f"마감 {_fmt_kst(item.get('end_date'))}</p>"
        f"<p><strong>태그:</strong> {html.escape(tags)}</p>"
        f"<p><strong>마켓 기준 요약:</strong> {excerpt}</p>"
        f"<p><a href=\"{url}\">Polymarket 원문 링크</a> "
        f"<small>(참고: 한국 IP에서는 451 지역 제한으로 원문 페이지가 열리지 않을 수 있습니다)</small></p>"
    )


def build_rss_xml(
    cfg: ScannerConfig,
    filename: str,
    feed_label: str,
    feed_desc: str,
    items: list[dict[str, Any]],
    now_ts: float,
) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = f"{cfg.site_title} — {feed_label}"
    ET.SubElement(channel, "link").text = f"{cfg.base_url}/"
    ET.SubElement(channel, "description").text = feed_desc
    ET.SubElement(channel, "language").text = "ko"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.fromtimestamp(now_ts, tz=timezone.utc)
    )
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {
            "href": f"{cfg.base_url}/{filename}",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for item in items:
        el = ET.SubElement(channel, "item")
        ET.SubElement(el, "title").text = item["headline"]
        ET.SubElement(el, "link").text = item["url"]
        guid_el = ET.SubElement(el, "guid", {"isPermaLink": "false"})
        guid_el.text = item["guid"]
        pub_dt = datetime.fromtimestamp(float(item["published_ts"]), tz=timezone.utc)
        ET.SubElement(el, "pubDate").text = format_datetime(pub_dt)
        for tr in item.get("tracks", []):
            ET.SubElement(el, "category").text = TRACK_LABELS.get(tr, tr)
        for tag in item.get("tags", [])[:3]:
            ET.SubElement(el, "category").text = str(tag)
        ET.SubElement(el, "description").text = _render_item_html(item)

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def _build_dashboard_html(
    cfg: ScannerConfig,
    live_items: list[dict[str, Any]],
    urgent_items: list[dict[str, Any]],
    now_ts: float,
    stats: dict[str, Any],
) -> str:
    updated_kst = datetime.fromtimestamp(now_ts, tz=KST).strftime("%Y-%m-%d %H:%M KST")

    feed_links = "".join(
        f'<a class="pill" href="./{fname}">{html.escape(label)} ({fname})</a>'
        for fname, label, _, _ in FEED_SPECS
    )
    feed_links += '<a class="pill" href="./feed.json">JSON Feed (feed.json)</a>'
    feed_links += '<a class="pill" href="./urgent.json">긴급 트리거 (urgent.json)</a>'

    def _card(item: dict[str, Any]) -> str:
        badges = "".join(
            f'<span class="tag">{html.escape(TRACK_LABELS.get(t, t))}</span>'
            for t in item.get("tracks", [])
        )
        if item.get("is_urgent"):
            badges = '<span class="tag urgent">🚨 긴급</span>' + badges
        if item.get("is_flip"):
            badges += '<span class="tag flip">🔄 판세반전</span>'

        d24 = _fmt_delta(item.get("delta_24h"))
        d6 = _fmt_delta(item.get("delta_6h"))
        outcomes = " · ".join(
            f"{html.escape(str(o['label']))} <strong>{_fmt_pct(o['price'])}</strong>"
            for o in item.get("top_outcomes", [])[:3]
        )
        return (
            f'<article class="card">'
            f'<div class="meta">{badges}<span class="score">Score {item["score"]}</span></div>'
            f'<h3>{html.escape(item["headline"])}</h3>'
            f'<p class="nums">현재 <strong>{_fmt_pct(item["price"])}</strong> · '
            f'24h <strong>{html.escape(d24)}</strong> · 6h {html.escape(d6)} · '
            f'24h 거래량 <strong>{format_usd_compact(float(item["volume_24h"]))}</strong> '
            f'({item["spike_ratio"]:.1f}x) · 유동성 {format_usd_compact(float(item["liquidity"]))}</p>'
            f'<p class="outcomes">{outcomes}</p>'
            f'<p class="desc">{html.escape(item.get("description_excerpt") or "")}</p>'
            f'<div class="foot">마감 {_fmt_kst(item.get("end_date"))} · '
            f'태그: {html.escape(", ".join(item.get("tags", [])) or "-")} · '
            f'<a href="{html.escape(item["url"])}" target="_blank" rel="noopener">Polymarket ↗</a></div>'
            f"</article>"
        )

    urgent_section = ""
    if urgent_items:
        urgent_cards = "".join(_card(it) for it in urgent_items[:5])
        urgent_section = (
            f'<section><h2>🚨 최근 긴급 시그널 (14시간 이내)</h2>{urgent_cards}</section>'
        )

    live_cards = "".join(_card(it) for it in live_items[:20]) or "<p>현재 조건을 만족하는 시그널이 없습니다.</p>"

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(cfg.site_title)}</title>
  <link rel="alternate" type="application/rss+xml" title="{html.escape(cfg.site_title)}" href="./all.xml" />
  <style>
    :root {{ color-scheme: dark; --bg: #0d1117; --card: #161b22; --border: #30363d; --fg: #e6edf3; --muted: #8b949e; --accent: #58a6ff; --urgent: #f85149; --flip: #d29922; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Pretendard", sans-serif; background: var(--bg); color: var(--fg); line-height: 1.55; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }}
    header {{ border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }}
    h1 {{ margin: 0 0 6px; font-size: 1.5rem; }}
    h2 {{ font-size: 1.15rem; margin: 28px 0 12px; }}
    .sub {{ color: var(--muted); font-size: 0.92rem; margin: 0 0 12px; }}
    .pills {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .pill {{ display: inline-block; padding: 5px 10px; border: 1px solid var(--border); border-radius: 999px; color: var(--accent); text-decoration: none; font-size: 0.82rem; background: var(--card); }}
    .pill:hover {{ border-color: var(--accent); }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }}
    .tag {{ font-size: 0.74rem; padding: 2px 8px; border-radius: 999px; background: #21262d; color: #c9d1d9; }}
    .tag.urgent {{ background: rgba(248,81,73,0.18); color: var(--urgent); font-weight: 700; }}
    .tag.flip {{ background: rgba(210,153,34,0.18); color: var(--flip); font-weight: 600; }}
    .score {{ margin-left: auto; font-size: 0.78rem; color: var(--muted); }}
    .card h3 {{ margin: 4px 0 8px; font-size: 1.02rem; }}
    .nums, .outcomes {{ margin: 4px 0; font-size: 0.9rem; }}
    .desc {{ margin: 8px 0; font-size: 0.85rem; color: var(--muted); }}
    .foot {{ font-size: 0.8rem; color: var(--muted); }}
    .foot a {{ color: var(--accent); text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(cfg.site_title)}</h1>
      <p class="sub">{html.escape(cfg.site_description)}</p>
      <p class="sub">최종 갱신: <strong>{updated_kst}</strong> · 스캔 이벤트 {stats.get("input_events", 0)}건 → 고신호 {stats.get("candidate_signals", 0)}건 (긴급 {stats.get("urgent_candidates", 0)}건)</p>
      <div class="pills">{feed_links}</div>
    </header>
    {urgent_section}
    <section>
      <h2>📡 실시간 상위 시그널</h2>
      {live_cards}
    </section>
  </main>
</body>
</html>
"""


def publish_outputs(
    out_dir: Path,
    cfg: ScannerConfig,
    live_signals: list[EvaluatedSignal],
    history_items: list[dict[str, Any]],
    newly_emitted: list[dict[str, Any]],
    now_ts: float,
    fetch_meta: dict[str, Any],
    eval_stats: dict[str, int],
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    feed_limit = int(cfg.publishing["feed_item_limit"])
    live_limit = int(cfg.publishing["live_top_limit"])
    urgent_window_sec = float(cfg.urgent["window_hours"]) * 3600.0

    # 1. Build live top items snapshot
    live_items = [
        sig.to_item_dict(now_ts=now_ts, guid=f"live:{sig.event_id}")
        for sig in live_signals[:live_limit]
    ]

    # 2. Urgent items within window (from emitted history + any current live urgent not yet in history)
    urgent_items: list[dict[str, Any]] = []
    seen_urgent_events: set[str] = set()
    for it in history_items:
        if it.get("is_urgent") and (now_ts - float(it.get("published_ts", 0))) <= urgent_window_sec:
            ev_id = str(it.get("event_id"))
            if ev_id not in seen_urgent_events:
                urgent_items.append(it)
                seen_urgent_events.add(ev_id)
    for it in live_items:
        if it.get("is_urgent") and str(it.get("event_id")) not in seen_urgent_events:
            urgent_items.append(it)
            seen_urgent_events.add(str(it.get("event_id")))

    # 3. Write RSS 2.0 XML feeds
    feed_counts: dict[str, int] = {}
    for fname, label, desc, track_filter in FEED_SPECS:
        if track_filter is None:
            selected = history_items[:feed_limit]
        else:
            selected = [it for it in history_items if track_filter in it.get("tracks", [])][
                :feed_limit
            ]
        xml_text = build_rss_xml(
            cfg=cfg,
            filename=fname,
            feed_label=label,
            feed_desc=desc,
            items=selected,
            now_ts=now_ts,
        )
        (out_dir / fname).write_text(xml_text, encoding="utf-8")
        feed_counts[fname] = len(selected)

    updated_utc = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
    updated_kst = datetime.fromtimestamp(now_ts, tz=KST).isoformat()

    # 4. Write rich feed.json (for Aside daily briefing)
    feed_json = {
        "version": "1.0",
        "title": cfg.site_title,
        "home_page_url": f"{cfg.base_url}/",
        "feed_url": f"{cfg.base_url}/feed.json",
        "updated_at_utc": updated_utc,
        "updated_at_kst": updated_kst,
        "stats": {
            **eval_stats,
            "newly_emitted_this_run": len(newly_emitted),
            "history_total": len(history_items),
            "urgent_active_count": len(urgent_items),
        },
        "urgent_signals": urgent_items[:10],
        "live_top_signals": live_items,
        "published_items": history_items[:feed_limit],
    }
    (out_dir / "feed.json").write_text(
        json.dumps(feed_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 5. Write ultra-compact urgent.json (for Aside 20:00 KST conditional 2nd briefing)
    urgent_json = {
        "updated_at_utc": updated_utc,
        "updated_at_kst": updated_kst,
        "has_urgent": len(urgent_items) > 0,
        "urgent_count": len(urgent_items),
        "window_hours": cfg.urgent["window_hours"],
        "signals": urgent_items[:8],
    }
    (out_dir / "urgent.json").write_text(
        json.dumps(urgent_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 6. Write health.json
    health = {
        "status": "ok",
        "updated_at_utc": updated_utc,
        "updated_at_kst": updated_kst,
        "fetch": fetch_meta,
        "eval": eval_stats,
        "newly_emitted": len(newly_emitted),
        "feed_counts": feed_counts,
        "urgent_count": len(urgent_items),
    }
    (out_dir / "health.json").write_text(
        json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 7. Write human-readable dashboard index.html + .nojekyll
    dashboard_html = _build_dashboard_html(
        cfg=cfg,
        live_items=live_items,
        urgent_items=urgent_items,
        now_ts=now_ts,
        stats=eval_stats,
    )
    (out_dir / "index.html").write_text(dashboard_html, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    return health
