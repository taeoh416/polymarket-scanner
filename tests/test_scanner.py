from __future__ import annotations

import gzip
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.config import ScannerConfig
from scanner.engine import evaluate_events, select_items_to_emit
from scanner.polymarket import decode_response_bytes
from scanner.publisher import publish_outputs
from scanner.run import run_scan
from scanner.store import StateStore

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "scanner.json"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ScannerEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ScannerConfig.from_file(CONFIG_PATH)
        self.now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
        self.now_ts = self.now.timestamp()

    def test_noise_filters_drop_sports_short_crypto_tweets_and_stagnant_settled(self) -> None:
        events = [
            # 1. Sports game (should be dropped by tag)
            {
                "id": "sports-1",
                "slug": "lakers-vs-celtics",
                "title": "Lakers vs. Celtics",
                "active": True,
                "closed": False,
                "volume24hr": 800000,
                "volume1wk": 900000,
                "liquidity": 200000,
                "tags": [{"slug": "nba", "label": "NBA"}],
                "markets": [
                    {
                        "id": "m-s1",
                        "question": "Lakers vs Celtics",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.62", "0.38"]',
                        "oneDayPriceChange": 0.22,
                        "spread": 0.01,
                    }
                ],
            },
            # 2. Short-term crypto up/down (should be dropped by title regex)
            {
                "id": "crypto-15m",
                "slug": "btc-up-or-down-15m",
                "title": "Bitcoin Up or Down - 15m",
                "active": True,
                "closed": False,
                "volume24hr": 1500000,
                "volume1wk": 2000000,
                "liquidity": 120000,
                "tags": [{"slug": "crypto", "label": "Crypto"}],
                "markets": [
                    {
                        "id": "m-c1",
                        "question": "Bitcoin Up or Down",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.55", "0.45"]',
                        "oneDayPriceChange": 0.20,
                        "spread": 0.01,
                    }
                ],
            },
            # 3. Elon tweet count spam (should be dropped by title regex)
            {
                "id": "tweets-1",
                "slug": "elon-musk-tweets-nov",
                "title": "Elon Musk # tweets between Nov 20 and Nov 27?",
                "active": True,
                "closed": False,
                "volume24hr": 3000000,
                "volume1wk": 5000000,
                "liquidity": 400000,
                "tags": [{"slug": "culture", "label": "Culture"}],
                "markets": [
                    {
                        "id": "m-t1",
                        "question": "200-220",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.51", "0.49"]',
                        "oneDayPriceChange": 0.25,
                        "spread": 0.01,
                    }
                ],
            },
            # 4. Stagnant 99.85% settled market with tiny 24h delta (should be dropped)
            {
                "id": "settled-1",
                "slug": "new-nvidia-ceo-2026",
                "title": "New NVIDIA CEO named by December 31, 2026?",
                "active": True,
                "closed": False,
                "volume24hr": 3700000,
                "volume1wk": 5800000,
                "liquidity": 450000,
                "tags": [{"slug": "tech", "label": "Tech"}],
                "markets": [
                    {
                        "id": "m-n1",
                        "question": "New NVIDIA CEO named by December 31, 2026?",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.9985", "0.0015"]',
                        "oneDayPriceChange": -0.001,
                        "spread": 0.001,
                    }
                ],
            },
            # 5. Valid macro mover + 50% flip + urgent!
            {
                "id": "fed-cut-dec",
                "slug": "fed-cut-dec-2026",
                "title": "Will the Fed cut rates in December 2026?",
                "description": "Resolves Yes if FOMC lowers the target federal funds rate.",
                "active": True,
                "closed": False,
                "createdAt": _iso(self.now - timedelta(days=20)),
                "endDate": _iso(self.now + timedelta(days=24)),
                "volume24hr": 850000,
                "volume1wk": 1200000,
                "liquidity": 320000,
                "openInterest": 900000,
                "commentCount": 140,
                "tags": [{"slug": "fed", "label": "Fed"}, {"slug": "finance", "label": "Finance"}],
                "markets": [
                    {
                        "id": "m-fed",
                        "question": "Will the Fed cut rates in December 2026?",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.62", "0.38"]',
                        "oneDayPriceChange": 0.21,
                        "oneWeekPriceChange": 0.28,
                        "spread": 0.01,
                    }
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            signals, stats = evaluate_events(events, store, self.cfg, self.now_ts)

            self.assertEqual(stats["excluded_noise"], 3)
            self.assertEqual(stats["excluded_settled_stagnant"], 1)
            self.assertEqual(len(signals), 1)

            sig = signals[0]
            self.assertEqual(sig.event_id, "fed-cut-dec")
            self.assertIn("movers", sig.tracks)
            self.assertIn("surge", sig.tracks)
            self.assertIn("macro-tech", sig.tracks)
            self.assertTrue(sig.is_flip)
            self.assertTrue(sig.is_urgent)
            self.assertGreaterEqual(len(sig.urgent_reasons), 2)

    def test_multi_market_aggregation_and_snapshot_6h_diff(self) -> None:
        event = {
            "id": "next-fed-chair",
            "slug": "who-will-be-next-fed-chair",
            "title": "Who will be the next Fed Chair?",
            "description": "Multi-candidate market.",
            "active": True,
            "closed": False,
            "createdAt": _iso(self.now - timedelta(days=10)),
            "volume24hr": 400000,
            "volume1wk": 1500000,
            "liquidity": 250000,
            "tags": [{"slug": "economy", "label": "Economy"}],
            "markets": [
                {
                    "id": "cand-a",
                    "groupItemTitle": "Candidate A",
                    "question": "Candidate A?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.45", "0.55"]',
                    "oneDayPriceChange": 0.02,
                    "volume24hr": 150000,
                    "liquidity": 90000,
                    "spread": 0.01,
                },
                {
                    "id": "cand-b",
                    "groupItemTitle": "Candidate B",
                    "question": "Candidate B?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.38", "0.62"]',
                    "oneDayPriceChange": 0.03,
                    "volume24hr": 200000,
                    "liquidity": 80000,
                    "spread": 0.01,
                },
                {
                    "id": "cand-c",
                    "groupItemTitle": "Candidate C",
                    "question": "Candidate C?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.12", "0.88"]',
                    "oneDayPriceChange": -0.01,
                    "volume24hr": 50000,
                    "liquidity": 40000,
                    "spread": 0.01,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            # Seed a snapshot 6 hours ago where Candidate B was at 0.24 (+14%p 6h jump!)
            six_hr_ago = (self.now - timedelta(hours=6)).timestamp()
            store.record_snapshot(
                "next-fed-chair",
                six_hr_ago,
                100000,
                200000,
                {"cand-a": 0.44, "cand-b": 0.24, "cand-c": 0.13},
            )

            signals, _ = evaluate_events([event], store, self.cfg, self.now_ts)
            self.assertEqual(len(signals), 1)
            sig = signals[0]
            self.assertTrue(sig.is_multi_market)
            self.assertEqual(sig.primary_market.market_id, "cand-b")
            self.assertAlmostEqual(sig.primary_market.delta_6h or 0.0, 0.14, places=3)
            self.assertEqual(len(sig.top_outcomes), 3)
            self.assertIn("movers", sig.tracks)
            self.assertTrue(sig.is_urgent)  # 6h >= 12%p and vol >= 200k and liq >= 50k

    def test_cooldown_retrigger_and_full_publish_outputs(self) -> None:
        base_event = {
            "id": "ai-bill-2026",
            "slug": "us-federal-ai-bill-2026",
            "title": "Will the US pass a federal AI safety bill in 2026?",
            "description": "Resolves Yes if signed into law before Dec 31, 2026.",
            "active": True,
            "closed": False,
            "createdAt": _iso(self.now - timedelta(hours=18)),
            "endDate": _iso(self.now + timedelta(days=35)),
            "volume24hr": 90000,
            "volume1wk": 90000,
            "liquidity": 45000,
            "openInterest": 60000,
            "commentCount": 25,
            "tags": [{"slug": "ai", "label": "AI"}, {"slug": "politics", "label": "Politics"}],
            "markets": [
                {
                    "id": "m-ai",
                    "question": "Will the US pass a federal AI safety bill in 2026?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.35", "0.65"]',
                    "oneDayPriceChange": 0.12,
                    "spread": 0.02,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fixture = tmp_path / "fixture.json"
            state_dir = tmp_path / "state"
            out_dir = tmp_path / "out"

            # Run 1: initial emit
            fixture.write_text(json.dumps({"events": [base_event]}), encoding="utf-8")
            h1 = run_scan(CONFIG_PATH, state_dir, out_dir, input_file=fixture, now_ts=self.now_ts)
            self.assertEqual(h1["newly_emitted"], 1)
            self.assertEqual(h1["feed_counts"]["all.xml"], 1)
            self.assertEqual(h1["feed_counts"]["new-hot.xml"], 1)
            self.assertEqual(h1["feed_counts"]["macro-tech.xml"], 1)

            # Verify valid XML and self-contained fields
            root = ET.fromstring((out_dir / "all.xml").read_text(encoding="utf-8"))
            items = root.findall("./channel/item")
            self.assertEqual(len(items), 1)
            desc = items[0].findtext("description") or ""
            self.assertIn("35.0%", desc)
            self.assertIn("451 지역 제한", desc)

            # Run 2 (+2h, tiny price change): suppressed by cooldown, history preserved!
            ev2 = json.loads(json.dumps(base_event))
            ev2["markets"][0]["outcomePrices"] = '["0.37", "0.63"]'
            fixture.write_text(json.dumps({"events": [ev2]}), encoding="utf-8")
            h2 = run_scan(
                CONFIG_PATH,
                state_dir,
                out_dir,
                input_file=fixture,
                now_ts=(self.now + timedelta(hours=2)).timestamp(),
            )
            self.assertEqual(h2["newly_emitted"], 0)
            self.assertEqual(h2["feed_counts"]["all.xml"], 1)

            # Run 3 (+4h, +16%p further jump -> crosses 50% -> UPDATE retrigger!)
            ev3 = json.loads(json.dumps(base_event))
            ev3["volume24hr"] = 320000
            ev3["liquidity"] = 90000
            ev3["markets"][0]["outcomePrices"] = '["0.53", "0.47"]'
            ev3["markets"][0]["oneDayPriceChange"] = 0.25
            fixture.write_text(json.dumps({"events": [ev3]}), encoding="utf-8")
            h3 = run_scan(
                CONFIG_PATH,
                state_dir,
                out_dir,
                input_file=fixture,
                now_ts=(self.now + timedelta(hours=4)).timestamp(),
            )
            self.assertEqual(h3["newly_emitted"], 1)
            self.assertEqual(h3["feed_counts"]["all.xml"], 2)

            urgent = json.loads((out_dir / "urgent.json").read_text(encoding="utf-8"))
            self.assertTrue(urgent["has_urgent"])
            self.assertEqual(urgent["urgent_count"], 1)
            self.assertIn("UPDATE", urgent["signals"][0]["headline"])

            # Verify dashboard HTML created
            html_out = (out_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("Polymarket 세상물정 스캐너", html_out)
            self.assertIn("긴급 시그널", html_out)

    def test_gzip_response_decoding(self) -> None:
        raw = json.dumps({"events": [{"id": "1"}]}).encode("utf-8")
        compressed = gzip.compress(raw)
        self.assertEqual(decode_response_bytes(compressed, "gzip"), raw.decode("utf-8"))
        self.assertEqual(decode_response_bytes(compressed, None), raw.decode("utf-8"))
        self.assertEqual(decode_response_bytes(raw, None), raw.decode("utf-8"))

    def test_weather_crypto_prices_and_series_dedup(self) -> None:
        events = [
            {
                "id": "weather-1",
                "slug": "moscow-temp-nov-25",
                "title": "Highest temperature in Moscow on November 25?",
                "active": True,
                "closed": False,
                "createdAt": _iso(self.now - timedelta(hours=5)),
                "volume24hr": 40000,
                "liquidity": 25000,
                "tags": [{"slug": "weather", "label": "Weather"}],
                "markets": [
                    {
                        "id": "m-w1",
                        "question": "-1C",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.70", "0.30"]',
                        "oneDayPriceChange": 0.30,
                        "spread": 0.01,
                    }
                ],
            },
            {
                "id": "btc-hit-1",
                "slug": "what-price-will-bitcoin-hit-nov",
                "title": "What price will Bitcoin hit November 23-29?",
                "active": True,
                "closed": False,
                "volume24hr": 500000,
                "liquidity": 120000,
                "tags": [{"slug": "crypto-prices", "label": "Crypto Prices"}],
                "markets": [
                    {
                        "id": "m-b1",
                        "question": "62000",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.42", "0.58"]',
                        "oneDayPriceChange": -0.20,
                        "spread": 0.01,
                    }
                ],
            },
            {
                "id": "gpu-jan",
                "slug": "b300-jan-2027",
                "title": "B300 average rental price in January 2027?",
                "active": True,
                "closed": False,
                "createdAt": _iso(self.now - timedelta(hours=10)),
                "volume24hr": 200000,
                "volume1wk": 200000,
                "liquidity": 60000,
                "tags": [{"slug": "ai", "label": "AI"}],
                "markets": [
                    {
                        "id": "m-g1",
                        "question": "Above $13",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.55", "0.45"]',
                        "oneDayPriceChange": 0.14,
                        "spread": 0.01,
                    }
                ],
            },
            {
                "id": "gpu-feb",
                "slug": "b300-feb-2027",
                "title": "B300 average rental price in February 2027?",
                "active": True,
                "closed": False,
                "createdAt": _iso(self.now - timedelta(hours=10)),
                "volume24hr": 50000,
                "volume1wk": 50000,
                "liquidity": 30000,
                "tags": [{"slug": "ai", "label": "AI"}],
                "markets": [
                    {
                        "id": "m-g2",
                        "question": "Above $14",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.40", "0.60"]',
                        "oneDayPriceChange": 0.10,
                        "spread": 0.01,
                    }
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp))
            signals, stats = evaluate_events(events, store, self.cfg, self.now_ts)
            self.assertEqual(stats["excluded_noise"], 2)
            self.assertEqual(stats.get("excluded_series_duplicate"), 1)
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0].event_id, "gpu-jan")


if __name__ == "__main__":
    unittest.main()
