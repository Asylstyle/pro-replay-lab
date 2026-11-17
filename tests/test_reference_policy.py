import copy
from datetime import datetime, timezone
import unittest

from pro_replay.reference_policy import compact_live_evidence, make_policy, reference_eligibility, resolve_patch
from tests.test_coach_model import replay


class ReferencePolicyTests(unittest.TestCase):
    def setUp(self):
        self.pro = dict(replay(1), human_players=10)
        self.ranked = dict(self.pro, lobby_type=7, game_mode=22, leagueid=0)
        self.start = self.ranked["start_time"]
        self.as_of = self.start + self.ranked["duration"] + 300
        self.calendar = [(self.start + 10000, "7.42"), (self.start, "7.41f"), (self.start - 60000, "7.41e")]
        self.policy = make_policy(self.calendar, as_of=self.as_of)
        self.utc = datetime.fromtimestamp(self.as_of, timezone.utc).isoformat()
        self.live = {"match_id": str(self.ranked["match_id"]), "average_mmr": 7300,
                     "last_update_time": self.start + 300, "league_id": 0, "lobby_type": 7, "game_mode": 22,
                     "players": [{"team": int(p["player_slot"] >= 128), "team_slot": (p["player_slot"] % 128) + 1,
                                  "hero_id": p["hero_id"], "account_id": 999} for p in self.ranked["players"]]}
        self.claim = compact_live_evidence(self.live, observed_at_utc=self.utc)

    def test_match_start_and_snapshot_cutoff_define_exact_minor_patch(self):
        self.assertEqual(resolve_patch(self.calendar, self.start - 1), "7.41e")
        self.assertEqual(resolve_patch(self.calendar, self.start), "7.41f")
        self.assertEqual(self.policy["patch"], "7.41f")
        self.assertEqual(reference_eligibility(self.pro, self.policy), ("professional", None))
        self.assertEqual(reference_eligibility(dict(self.pro, start_time=self.start - 1, patch_version="7.41f"),
                                               self.policy), (None, "outside_reference_patch"))
        self.assertEqual(reference_eligibility(dict(self.pro, duration=self.as_of-self.start+1),
                                               self.policy), (None, "match_unfinished_at_cutoff"))
        tomorrow = make_policy(self.calendar, as_of=self.start+15000)
        self.assertEqual(reference_eligibility(self.pro, tomorrow), (None, "outside_reference_patch"))

    def test_rejects_changed_threshold_and_ambiguous_or_missing_calendar(self):
        with self.assertRaises(ValueError):
            reference_eligibility(self.ranked, dict(self.policy, minimum_avg_mmr=7000))
        with self.assertRaises(ValueError):
            make_policy(self.calendar + [(self.start, "7.42a")], as_of=self.as_of)
        with self.assertRaises(ValueError):
            make_policy([(self.start+10000, "7.42")], as_of=self.as_of)

    def test_accepts_only_reported_lobby_average_and_exact_threshold(self):
        self.assertEqual(reference_eligibility(self.ranked, self.policy, public_match=self.claim), ("ranked_7300", None))
        self.assertEqual(reference_eligibility(dict(self.ranked, opendota_live=self.claim), self.policy),
                         ("ranked_7300", None))
        self.assertEqual(reference_eligibility(self.ranked, self.policy, public_match=dict(self.claim, average_mmr=7299)),
                         (None, "below_7300_average_mmr"))
        proxy = dict(self.ranked, average_mmr=9999, avg_rank_tier=80, num_rank_tier=10, avg_mmr=9000, num_mmr=10)
        self.assertEqual(reference_eligibility(proxy, self.policy), (None, "missing_verified_lobby_mmr"))
        for wrong in (dict(self.claim, average_mmr=True), dict(self.claim, average_mmr="9999"),
                      dict(self.claim, source="https://example.org/live")):
            self.assertIsNone(reference_eligibility(proxy, self.policy, public_match=wrong)[0])
        self.assertFalse(any("account_id" in p for p in self.claim["roster"]))

    def test_forbids_mismatched_match_mode_team_or_incomplete_roster(self):
        for wrong in (dict(self.claim, match_id=987654), dict(self.claim, game_mode=23),
                      dict(self.claim, league_id=2), dict(self.claim, roster=self.claim["roster"][:-1])):
            self.assertIsNone(reference_eligibility(self.ranked, self.policy, public_match=wrong)[0])
        permuted = copy.deepcopy(self.claim)
        roster = permuted["roster"]
        roster[0]["hero_id"], roster[1]["hero_id"] = roster[1]["hero_id"], roster[0]["hero_id"]
        self.assertEqual(reference_eligibility(self.ranked, self.policy, public_match=permuted)[0], "ranked_7300")
        roster[0]["hero_id"], roster[5]["hero_id"] = roster[5]["hero_id"], roster[0]["hero_id"]
        self.assertEqual(reference_eligibility(self.ranked, self.policy, public_match=permuted)[1], "live_roster_mismatch")

    def test_live_snapshot_time_and_slots_are_validated_before_inclusion(self):
        try_invalid = (dict(self.live, last_update_time=self.as_of+1), dict(self.live, players=self.live["players"][:-1]),
                       dict(self.live, players=[self.live["players"][0]] * 10))
        for raw in try_invalid:
            with self.assertRaises(ValueError):
                compact_live_evidence(raw, observed_at_utc=self.utc)
        with self.assertRaises(ValueError):
            compact_live_evidence(self.live, observed_at_utc=self.utc.split("+")[0])
        later_utc = datetime.fromtimestamp(self.as_of + 10, timezone.utc).isoformat()
        future = dict(self.claim, observed_at_utc=later_utc)
        self.assertEqual(reference_eligibility(self.ranked, self.policy, public_match=future)[1], "invalid_live_observation_time")

    def test_requires_ten_real_players_verified_pro_league_and_economy_targets(self):
        for raw in (dict(self.pro, league={"tier": "amateur"}), dict(self.pro, human_players=9),
                    dict(self.pro, league={"tier": "premium", "leagueid": 54}), dict(self.pro, demo=True),
                    dict(self.pro, players=self.pro["players"][:-1])):
            self.assertIsNone(reference_eligibility(raw, self.policy)[0])
        no_label = copy.deepcopy(self.pro)
        no_label["players"][3]["times"] = [0, 600]
        self.assertIsNone(reference_eligibility(no_label, self.policy)[0])
