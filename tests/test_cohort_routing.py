import gzip
import io
import json
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from pro_replay.archive import Archive, normalize
from pro_replay.cli import main
from pro_replay.hero_review import COHORT_HISTORY, render_coach_hero_review
from pro_replay.patch_reference import PACKAGED_MODEL, load_reference
from test_hero_review import row
from test_match_analytics import scorecard


class DetailedCohortTests(unittest.TestCase):
    def setUp(self):
        self.model = {"policy": {"patch": "7.41f", "patch_start": 1, "as_of": 20_000},
                      "cohort_ids": {"ranked_7300": [1, 10, 11], "professional": [2]}}
        self.ranked = {"leagueid": 0, "lobby_type": 7, "game_mode": 22, "start_time": 10_000}
        self.pub = {"league_id": 0, "lobby_type": 7, "game_mode": 22}

    def test_ranked_detail_uses_verified_ids_and_excludes_future_and_self(self):
        target = row(10, **self.pub)
        peers = [row(k, **self.pub) for k in (1, 2, 3, 10, 11)]
        html = render_coach_hero_review(target, self.ranked, self.model, peers)
        self.assertIn("средним MMR лобби 7300+", html)
        self.assertIn("тем же героем — <b>1</b>", html)
        self.assertIn("opendota.com/matches/1\"", html)
        self.assertNotIn("opendota.com/matches/2\"", html)
        self.assertNotIn("opendota.com/matches/3\"", html)

    def test_pro_detail_needs_verified_league_and_active_patch(self):
        target = row(10)
        raw = {"leagueid": 10, "lobby_type": 1, "game_mode": 2, "start_time": 10_000,
               "league": {"leagueid": 10, "tier": "professional"}}
        peers = [row(k) for k in (1, 2, 3, 11)]
        html = render_coach_hero_review(target, raw, self.model, peers)
        self.assertIn("профессиональные матчи", html)
        self.assertIn("тем же героем — <b>1</b>", html)
        self.assertIn("opendota.com/matches/2\"", html)
        for unsupported in ({**raw, "league": {"tier": "amateur"}}, {**raw, "start_time": 0}):
            html = render_coach_hero_review(target, unsupported, self.model, peers)
            self.assertNotIn("Карты этого героя на той же позиции", html)
            self.assertIn("Игрок нанёс", html)
        html = render_coach_hero_review(target, None, self.model, peers)
        self.assertIn("Для сравнения нужен", html)
        self.assertNotIn("Карты этого героя на той же позиции", html)

    def test_cached_ranked_match_and_pro_report_details_follow_the_same_id_filter(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            db = str(path / "scores.sqlite")
            model_path = path / "model.json"
            model_path.write_text(json.dumps({**self.model, "format": "patch-economic-reference-1"}))
            a = Archive(db)
            with closing(a.db):
                for mid, pro in ((1, False), (3, False), (10, False), (2, True), (4, True), (9, True)):
                    raw = dict(scorecard(), match_id=mid, start_time=mid*1000, duration=600,
                               game_mode=2 if pro else 22, lobby_type=1 if pro else 7, leagueid=10 if pro else 0)
                    if pro:
                        raw["league"] = {"leagueid": 10, "tier": "premium"}
                    for p in raw["players"]:
                        p["position_est"] = 1 + p["player_slot"] - 128
                    a.save(normalize(raw, 104, {}))
            flags = ["--reference", str(model_path)]
            match = path / "ranked.html"
            index = path / "report.html"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--db", db, "match", "slot:132", "10", "--out", str(match), *flags]), 0)
                self.assertEqual(main(["--db", db, "report", "104", "--details", "--out", str(index), *flags]), 0)
            for file, allowed, excluded in ((match, 1, 3), (path / "report-matches" / "9.html", 2, 4)):
                section = file.read_text(encoding="utf8").split('<section id="hero">')[1].split('</section>')[0]
                self.assertIn(f'opendota.com/matches/{allowed}"', section)
                self.assertNotIn(f'opendota.com/matches/{excluded}"', section)

    def test_shipped_detailed_scorecards_cover_exactly_the_shipped_models(self):
        model = load_reference(Path(COHORT_HISTORY).with_name(PACKAGED_MODEL))
        with gzip.open(COHORT_HISTORY, "rt", encoding="utf8") as source:
            rows = json.load(source)
        ids = {cohort: set(group) for cohort, group in model["cohort_ids"].items()}
        for cohort in ("ranked_7300", "professional"):
            subset = [r for r in rows if bool(r["league_id"]) == (cohort == "professional")]
            self.assertEqual({r["match_id"] for r in subset}, ids[cohort])
            self.assertEqual(len(subset), len(ids[cohort]))
            self.assertTrue(all(len(r["hero_samples"]) == 10 and r["patch_version"] == model["policy"]["patch"]
                                for r in subset))
        # These cards were published before OpenDota had parsed their replays.
        for mid in (9094435134, 9094449522, 9094461458):
            samples = next(r["hero_samples"] for r in rows if r["match_id"] == mid)
            self.assertTrue(all(s["position"] in range(1, 6) and s["values"]["control"] is not None
                                and s["values"]["vision"] is not None for s in samples))
