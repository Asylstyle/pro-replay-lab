import json
import re
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from pro_replay.analysis import extract_match, summarize
from pro_replay.events import event_data, summarize_events
from pro_replay.learning import predict, train_model
from pro_replay.source import OpenDota, SourceError


ACCOUNT = 834


def raw_match(match_id, **changes):
    player = {
        "account_id": ACCOUNT, "player_slot": 128, "hero_id": 1,
        "times": [-60, 0, 600, 660],
        "lh_t": [0, 0, 68, 99], "gold_t": [0, 0, 4300, 8000],
        "xp_t": [0, 0, 3200, 7000],
        "kills": 4, "assists": 2, "deaths": 1,
        "purchase_log": [
            {"time": 1050, "key": "black_king_bar"},
            {"time": 600, "key": "blink"},
            {"time": 700, "key": "recipe_black_king_bar"},
        ],
        **changes,
    }
    return {
        "match_id": match_id, "start_time": 1700000000 + match_id,
        "duration": 1900, "patch": 50, "leagueid": 7,
        "radiant_win": False, "dire_score": 20,
        "players": [{"account_id": 901, "player_slot": 0}, player],
    }


@contextmanager
def fake_api(respond):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            payload = respond(url.path, parse_qs(url.query))
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            with patch("pro_replay.source.time.sleep"):
                yield OpenDota(f"http://127.0.0.1:{server.server_port}", api_key="test-only")
        finally:
            server.shutdown()
            thread.join()


class PipelineTests(unittest.TestCase):
    def test_player_query_cursor_and_fetch_are_consistent(self):
        requests = []

        def respond(path, params):
            requests.append((path, params))
            if path == "/explorer":
                sql = params["sql"][0]
                cursor = re.search(r"pm\.match_id\s*<\s*(\d+)", sql)
                ids = [104, 103] if cursor is None else [100]
                return {"rows": [{"match_id": str(i)} for i in ids]}
            return raw_match(int(path.rsplit("/", 1)[1]))

        with fake_api(respond) as api:
            recent = api.player_pro_ids(ACCOUNT)
            older = api.player_pro_ids(ACCOUNT, before=recent[-1])
            rows = [extract_match(api.match(i), ACCOUNT) for i in recent + older]

        self.assertEqual([r["match_id"] for r in rows], [104, 103, 100])
        self.assertEqual(summarize(rows)["matches"], 3)
        first, second = [params["sql"][0] for path, params in requests if path == "/explorer"]
        self.assertRegex(first, rf"pm\.account_id\s*=\s*{ACCOUNT}\b")
        self.assertRegex(first, r"m\.leagueid\s*>\s*0")
        self.assertNotRegex(first, r"match_id\s*<")
        self.assertRegex(second, r"pm\.match_id\s*<\s*103\b")

    def test_cursor_rejects_repeated_or_out_of_order_ids(self):
        for ids in ([88, 88], [86, 87], [90]):
            with self.subTest(ids=ids), fake_api(
                lambda *_: {"rows": [{"match_id": i} for i in ids]}
            ) as api:
                with self.assertRaises(SourceError):
                    api.player_pro_ids(ACCOUNT, before=90)

    def test_minute_ten_never_consumes_later_snapshots(self):
        row = extract_match(raw_match(1), ACCOUNT)
        self.assertEqual([row[k] for k in ("lh_10", "gold_10", "xp_10")], [68, 4300, 3200])
        self.assertTrue(row["win"])
        self.assertAlmostEqual(row["kill_participation"], 0.3)
        self.assertIsNone(extract_match(raw_match(1), 123))
        for match in (
            raw_match(1, times=[0, 601, 660]),
            {**raw_match(1), "duration": 580},
        ):
            row = extract_match(match, ACCOUNT)
            self.assertIsNone(row["lh_10"])
            self.assertIsNone(row["gold_10"])
            self.assertIsNone(row["xp_10"])

    def test_empty_missing_and_partially_malformed_event_streams(self):
        matches = [
            raw_match(1, obs_log=[{"time": -12}], sen_log=[], kills_log=[{"time": 630, "key": "npc_dota_hero_lina"}]),
            raw_match(2, obs_log=None, sen_log=[], kills_log=[]),
            raw_match(3, obs_log=[{"time": 50}, {"key": "bad"}], sen_log=None, kills_log=None),
        ]
        rows = [{**extract_match(m, ACCOUNT), **event_data(m, ACCOUNT)} for m in matches]
        self.assertEqual([e["time"] for e in rows[0]["timeline"]], [-12, 630])
        self.assertIn("Lina", rows[0]["timeline"][1]["detail"])
        self.assertEqual(rows[2]["timeline"][0]["time"], 50)
        self.assertFalse(rows[2]["event_coverage"]["observer"])
        summary = summarize_events(rows)
        self.assertEqual(summary["observer"]["available_matches"], 1)
        self.assertEqual(summary["observer"]["before_or_at_ten_ids"], [1])
        self.assertEqual(summary["sentry"]["available_matches"], 2)
        self.assertEqual(summary["sentry"]["matches_with_event"], 0)
        self.assertEqual(summary["kill"]["median_first_time"], 630)
        self.assertEqual(summary["kill"]["before_or_at_ten_matches"], 0)

    def test_purchase_and_learning_populations_are_explicit(self):
        matches = [raw_match(1), raw_match(2, purchase_log=[]), raw_match(3, purchase_log=None),
                   raw_match(4, purchase_log=[{"time": 900, "key": "blink"}, {"time": 900, "key": "manta"}]),
                   {**raw_match(5), "demo": True}]
        rows = [extract_match(m, ACCOUNT) for m in matches]
        self.assertEqual([p["key"] for p in rows[0]["core_items"]], ["blink", "black_king_bar"])
        self.assertEqual(summarize(rows)["coverage"]["purchase_logs"], 4)
        result = train_model(rows)
        self.assertEqual(result["coverage"]["labeled_matches"], 1)
        self.assertEqual(result["coverage"]["excluded"], {
            "no_recorded_target_in_window": 1, "missing_purchase_log": 1,
            "ambiguous_first_purchase": 1, "demo_row": 1,
        })
        self.assertIsNone(result["metrics"])

    def test_chronological_unseen_item_counts_as_incorrect(self):
        rows = []
        for i in range(30):
            item = ("blink", "manta")[i % 2] if i < 24 else "bfury"
            raw = raw_match(i + 1, hero_id=i % 2 + 1, purchase_log=[
                {"time": 600, "key": "power_treads"}, {"time": 750, "key": item}
            ])
            rows.append(extract_match(raw, ACCOUNT))
        result = train_model(list(reversed(rows)) + [rows[0]])
        self.assertEqual(result["coverage"]["excluded"], {"duplicate_match": 1})
        self.assertEqual(result["metrics"]["test_matches"], 6)
        self.assertEqual(result["metrics"]["unseen_test_labels"], 6)
        self.assertEqual(result["metrics"]["correct"], 0)
        self.assertEqual(result["metrics"]["baseline_correct"], 0)
        row = rows[-1]
        self.assertEqual(predict(result["model"], row),
                         predict(result["model"], {**row, "win": False, "core_items": [], "kills": 99}))
