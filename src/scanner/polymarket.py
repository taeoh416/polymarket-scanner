from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from scanner.config import ScannerConfig

USER_AGENT = "Mozilla/5.0 (compatible; PolymarketDigestFeed/1.0)"


class PolymarketFetchError(RuntimeError):
    """Raised when Gamma API cannot be queried reliably."""


def _http_get_json(url: str, timeout_sec: int, max_retries: int) -> Any:
    last_err: Exception | None = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", "replace")[:200]
            except OSError:
                pass
            # 4xx (other than 429) should not be retried blindly
            if exc.code == 422:
                raise
            if 400 <= exc.code < 500 and exc.code != 429:
                raise PolymarketFetchError(
                    f"HTTP {exc.code} for {url} body={err_body!r}"
                ) from exc
            last_err = exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as exc:
            last_err = exc

        if attempt + 1 < max_retries:
            time.sleep(min(2**attempt, 6))

    raise PolymarketFetchError(f"Failed to fetch {url} after {max_retries} attempts: {last_err}")


def _extract_events_and_cursor(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(payload, dict):
        events = payload.get("events")
        if events is None:
            events = payload.get("data", [])
        next_cursor = payload.get("next_cursor") or payload.get("nextCursor")
        if isinstance(events, list):
            return events, str(next_cursor) if next_cursor else None
        return [], None
    if isinstance(payload, list):
        return payload, None
    return [], None


def _paginate_keyset(
    base_api: str,
    order_candidates: list[str | None],
    max_pages: int,
    page_size: int,
    extra_params: dict[str, Any],
    timeout_sec: int,
    max_retries: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    chosen_order: str | None = None
    events_acc: list[dict[str, Any]] = []
    warnings: list[str] = []
    used_endpoint = "/events/keyset"

    for order_field in order_candidates:
        cursor: str | None = None
        pages_fetched = 0
        batch_events: list[dict[str, Any]] = []
        failed_422 = False

        for _ in range(max_pages):
            params: dict[str, Any] = {
                "closed": "false",
                "limit": str(page_size),
                **extra_params,
            }
            if order_field:
                params["order"] = order_field
                params["ascending"] = "false"
            if cursor:
                params["after_cursor"] = cursor

            url = f"{base_api.rstrip('/')}/events/keyset?{urllib.parse.urlencode(params)}"
            try:
                payload = _http_get_json(url, timeout_sec=timeout_sec, max_retries=max_retries)
            except urllib.error.HTTPError as exc:
                if exc.code == 422:
                    warnings.append(f"keyset order={order_field!r} rejected with 422; trying fallback")
                    failed_422 = True
                    break
                raise

            page_events, next_cursor = _extract_events_and_cursor(payload)
            batch_events.extend(page_events)
            pages_fetched += 1
            if not page_events or not next_cursor or len(page_events) < page_size:
                break
            cursor = next_cursor

        if not failed_422:
            chosen_order = order_field
            events_acc = batch_events
            return events_acc, {
                "endpoint": used_endpoint,
                "order": chosen_order,
                "pages": pages_fetched,
                "count": len(events_acc),
                "warnings": warnings,
            }

    # Final fallback: legacy /events endpoint with offset if all keyset order attempts failed
    used_endpoint = "/events"
    fallback_order = order_candidates[0]
    pages_fetched = 0
    for page_idx in range(max_pages):
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(page_size),
            "offset": str(page_idx * page_size),
        }
        if fallback_order:
            params["order"] = fallback_order
            params["ascending"] = "false"
        url = f"{base_api.rstrip('/')}/events?{urllib.parse.urlencode(params)}"
        payload = _http_get_json(url, timeout_sec=timeout_sec, max_retries=max_retries)
        page_events, _ = _extract_events_and_cursor(payload)
        events_acc.extend(page_events)
        pages_fetched += 1
        if len(page_events) < page_size:
            break

    warnings.append("fell back to /events offset pagination")
    return events_acc, {
        "endpoint": used_endpoint,
        "order": fallback_order,
        "pages": pages_fetched,
        "count": len(events_acc),
        "warnings": warnings,
    }


def fetch_active_events(cfg: ScannerConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fcfg = cfg.fetch
    base_api = fcfg["base_api"]
    page_size = int(fcfg.get("page_size", 100))
    timeout_sec = int(fcfg.get("timeout_sec", 20))
    max_retries = int(fcfg.get("max_retries", 3))
    api_liq = fcfg.get("api_liquidity_min", 10000)
    api_vol = fcfg.get("api_volume_min", 5000)

    vol_events, vol_meta = _paginate_keyset(
        base_api=base_api,
        order_candidates=["volume24hr", "volume", None],
        max_pages=int(fcfg.get("volume_pages", 5)),
        page_size=page_size,
        extra_params={"liquidity_min": str(api_liq), "volume_min": str(api_vol)},
        timeout_sec=timeout_sec,
        max_retries=max_retries,
    )

    new_events, new_meta = _paginate_keyset(
        base_api=base_api,
        order_candidates=["createdAt", "id", None],
        max_pages=int(fcfg.get("newest_pages", 3)),
        page_size=page_size,
        extra_params={"liquidity_min": str(api_liq)},
        timeout_sec=timeout_sec,
        max_retries=max_retries,
    )

    dedup: dict[str, dict[str, Any]] = {}
    for ev in vol_events + new_events:
        ev_id = str(ev.get("id") or "")
        if not ev_id:
            continue
        if ev_id not in dedup:
            dedup[ev_id] = ev

    meta = {
        "volume_query": vol_meta,
        "newest_query": new_meta,
        "raw_total": len(vol_events) + len(new_events),
        "unique_events": len(dedup),
    }
    return list(dedup.values()), meta
