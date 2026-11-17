import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pro_replay.cli import main
from pro_replay.patterns import find_order
from test_pipeline import ACCOUNT, fake_api, raw_match


class ProductTests(unittest.TestCase):
    def test_real_case_search_and_gap(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as log:
            db = str(Path(folder) / "archive.db")
            self.assertEqual(main(["--db", db, "find", "demo", "tower", "item:manta"]), 0)
            self.assertIn("11:51 → 15:59", log.getvalue())
            self.assertIn("09:12 → 14:32", log.getvalue())
            html = Path(folder) / "example.html"
            self.assertEqual(main(["demo", "--out", str(html)]), 0)
            self.assertIn("mobacast.com/recordings/8960577698", html.read_text(encoding="utf8"))
            with redirect_stdout(io.StringIO()) as limited:
                self.assertEqual(main(["--db", db, "find", "demo", "tower", "item:manta", "--within", "200"]), 0)
                # On TB, the second tower came 195 seconds before Manta.
                self.assertIn("11:17 → 14:32", limited.getvalue())
                self.assertIn("Недостаточно данных: 1", limited.getvalue())

    def test_absence_unknown_and_ties(self):
        row = {"match_id": 1, "event_coverage": {"kill": True},
               "purchase_log_available": True, "purchases": [{"time": 500, "key": "blink"}],
               "timeline": [{"time": 500, "kind": "kill"}]}
        self.assertEqual(find_order([row], "item:blink", "kill")["absent"], [1])
        row["event_coverage"]["kill"] = False
        self.assertEqual(find_order([row], "item:blink", "kill")["unknown"], [1])

    def test_discovery_cache_training_and_prediction(self):
        ids = list(range(35, 0, -1))
        details = []
        def serve(path, params):
            if path == "/heroes":
                return [{"id": 1, "name": "npc_dota_hero_antimage", "localized_name": "Anti-Mage"}]
            if path == "/proPlayers":
                return [{"name": "Sample", "account_id": ACCOUNT}]
            if path == "/explorer":
                return {"rows": [] if "pm.match_id <" in params["sql"][0] else [{"match_id": n} for n in ids]}
            n = int(path.rsplit("/", 1)[1])
            details.append(n)
            return raw_match(n, purchase_log=[{"time": 720, "key": "blink" if n % 2 else "manta"}])
        with tempfile.TemporaryDirectory() as folder, fake_api(serve) as api, patch("pro_replay.cli.OpenDota", return_value=api), redirect_stdout(io.StringIO()):
            directory = Path(folder)
            db = ["--db", str(directory / "state.sqlite")]
            self.assertEqual(main(db + ["sync", "Sample", "--all"]), 0)
            self.assertEqual(main(db + ["sync", str(ACCOUNT), "--all"]), 0)
            self.assertEqual(details, ids)
            saved = directory / "model.json"
            self.assertEqual(main(db + ["train", "Sample", "--out", str(saved)]), 0)
            scores = json.loads(saved.read_text())["metrics"]
            self.assertEqual(scores["test_matches"], 7)
            self.assertIn("hero_baseline_accuracy", scores)
            self.assertEqual(main(["predict", str(saved), "--hero", "1", "--patch", "50", "--lh", "65",
                                   "--gold", "4500", "--xp", "4200"]), 0)
            output = directory / "local.html"
            self.assertEqual(main(db + ["report", "Sample", "--hero", "1", "--out", str(output)]), 0)
            self.assertIn("Anti-Mage", output.read_text(encoding="utf8"))
