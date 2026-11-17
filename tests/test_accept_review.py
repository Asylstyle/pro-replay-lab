from copy import deepcopy
import unittest

from pro_replay.accept_review import render_review, review_moments


def lesson(signal, **fields):
    return {"signal": signal, "video_id": "abcdefghij0", "video_second": 78, "title": "Покупка",
            "question": "Какое действие ты выберешь?", "criterion": "Сверь выбранную цель.",
            "source_note": "Учебный пример.", **fields}


class AcceptReviewTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"duration": 1600, "players": [
            {"player_slot": 0, "hero_id": 1, "purchase_log": [{"time": 630, "key": "blink"}]},
            {"player_slot": 128, "hero_id": 2, "purchase_log": [{"time": 55, "key": "manta"}]}]}

    def test_matching_purchase_and_two_time_domains(self):
        lesson_ = lesson("item_timing", item_keys=["blink"], roles=[1], hero_ids=[1], lead_seconds=10)
        card, = review_moments(self.raw, 0, [], [lesson_], position=1, at=623)
        self.assertEqual((card["pause_time"], card["time"], card["video_second"]), (620, 630, 78))
        self.assertIn("Blink Dagger", card["fact"])
        html = render_review([card])
        self.assertIn("10:20 в твоей игре", html)
        self.assertIn("10:30</b>", html)
        self.assertIn("t=78s", html)
        self.assertIn("1:18 в видео", html)
        for wrong in (dict(position=4), dict(position=1, at=300)):
            self.assertEqual(review_moments(self.raw, 0, [], [lesson_], **wrong), [])
        self.assertEqual(review_moments(self.raw, 128, [], [lesson_], position=1), [])
        self.assertEqual(review_moments(self.raw, 0, [], [lesson("item_timing", item_keys=["manta"])]), [])

    def test_verified_fight_only_and_anchor_is_the_verified_frame(self):
        d = {"kind": "focus", "target": "Mars", "button": "Hex", "start": 500, "end": 530, "time": 525,
             "verification": {"status": "verified", "time": 521}}
        catalog = [lesson("verified_focus")]
        card, = review_moments(self.raw, 0, [d], catalog, at=525)
        self.assertEqual(card["time"], 521)
        self.assertIn("Hex", card["fact"])
        self.assertNotIn("убил", card["fact"])
        for gate in ({}, {"status": "unknown"}, {"status": "blocked", "time": 520}):
            self.assertEqual(review_moments(self.raw, 0, [dict(d, verification=gate)], catalog), [])

    def test_roshan_requires_team_credit_and_lane_needs_exact_observation(self):
        raw = deepcopy(self.raw)
        raw["objectives"] = [{"type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 3, "time": 1200},
                             {"type": "CHAT_MESSAGE_ROSHAN_KILL", "time": 900}]
        self.assertEqual(review_moments(raw, 0, [], [lesson("objective_review")]), [])
        enemy, = review_moments(raw, 0, [], [lesson("enemy_roshan", lead_seconds=20)])
        self.assertEqual((enemy["pause_time"], enemy["time"]), (1180, 1200))
        self.assertIn("сопернику", enemy["fact"])
        self.assertEqual(review_moments(raw, 128, [], [lesson("enemy_roshan")]), [])
        raw["objectives"].append({"type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2, "time": 1300})
        card, = review_moments(raw, 0, [], [lesson("objective_review")])
        self.assertIn("Команда", card["fact"])
        self.assertEqual(card["time"], 1300)
        raw["players"][0].update(times=[599, 601], lh_t=[51, 54], gold_t=[4000, 4010])
        self.assertEqual(review_moments(raw, 0, [], [lesson("lane_economy")]), [])
        raw["players"][0]["times"][0] = 600
        card, = review_moments(raw, 0, [], [lesson("lane_economy")])
        self.assertIn("51", card["fact"])

    def test_empty_duplicate_slot_or_source_and_escaping(self):
        self.assertEqual(render_review([]), "")
        catalog = [lesson("item_timing", item_keys=["blink"], question="<script>alert(1)</script>")]
        moments = review_moments(self.raw, 0, [], catalog * 2)
        self.assertEqual(len(moments), 1)
        self.assertNotIn("<script>", render_review(moments))
        self.raw["players"].append(dict(self.raw["players"][0]))
        self.assertEqual(review_moments(self.raw, 0, [], catalog), [])

    def test_laning_review_excludes_a_later_death_even_if_the_pause_is_early(self):
        catalog = [lesson("death_review", lead_seconds=20, max_game_second=600)]
        self.raw["players"][0]["deaths_log"] = [{"time": 601}]
        self.assertEqual(review_moments(self.raw, 0, [], catalog), [])
        self.raw["players"][0]["deaths_log"].append({"time": 600})
        card, = review_moments(self.raw, 0, [], catalog)
        self.assertEqual((card["pause_time"], card["time"]), (580, 600))
