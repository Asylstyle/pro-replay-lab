import unittest
from unittest.mock import call, patch

from pro_replay.match_decisions import _distinct_decisions, _stakes, _timing, top_decisions
from pro_replay.source import OpenDota, SourceError


def decision(start, end, button, target="Lion"):
    return {"start": start, "end": end, "button": button, "target": target, "enemy_focus": "Lion"}


class DecisionRankingTests(unittest.TestCase):
    def test_channel_search_starts_at_fight_start_when_cast_second_is_unknown(self):
        event = {"time": 973, "kind": "death", "target": "Sand King"}
        cue, start = _timing({"kind": "interrupt", "enemy_focus": "Rubick"}, event, 936)
        self.assertIn("Rubick", cue)
        self.assertEqual(start, 936)

    def test_three_distinct_fights_can_require_second_candidate_for_leading_fight(self):
        a = decision(100, 140, "Hex")
        b = decision(100, 140, "Earth Spike")
        c = decision(200, 250, "Hex")
        d = decision(300, 340, "BKB", "Mars")
        candidates = [(9, a), (8, b), (7, c), (6, d)]
        self.assertEqual(_distinct_decisions(candidates), [b, c, d])

    def test_overlaps_duplicates_and_missing_decisions_do_not_fill_three_slots(self):
        a = decision(100, 140, "Hex")
        b = decision(140, 145, "Earth Spike")
        c = decision(180, 200, "Hex")
        self.assertEqual(_distinct_decisions([(3, a), (2, b)]), [a])
        self.assertEqual(_distinct_decisions([(3, a), (2, c)]), [a])
        self.assertEqual(_distinct_decisions([]), [])

    def test_enemy_objective_and_unattributed_roshan_do_not_imply_loss_for_this_side(self):
        context = {"team": {"deaths": 0}, "enemy": {"deaths": 3},
                   "objectives_after": [{"lost_side": "dire", "time": 100, "label": "Dire tower"},
                                        {"lost_side": None, "time": 110, "label": "Roshan"}]}
        self.assertIsNone(_stakes(context, [], "radiant", 0))
        score, facts = _stakes(context, [], "dire", 128)
        self.assertGreater(score, 0)
        self.assertIn("Dire tower", facts)
        self.assertNotIn("Roshan", facts)

    def test_exact_anchor_must_match_both_participants_and_be_feasible_before_event(self):
        fight = {"start": 100, "end": 120, "items": []}
        death = {"time": 100, "kind": "death", "target_slot": 0, "actor_slot": 128, "target": "Mars",
                 "text": "Mars died to Lion", "perspective": "self", "time_dead": 50}
        option = {"kind": "focus", "target": "Lion", "target_slot": 128, "focus_slot": 128, "enemy_focus": "Lion",
                  "button": "Spear", "strength": 3, "event_time": 100, "event_kind": "death",
                  "event_actor_slot": 128, "event_target_slot": 0}
        def run():
            return top_decisions({}, 0, {}, [fight], [])
        with (patch("pro_replay.match_decisions.observed_events", return_value=[death]),
              patch("pro_replay.match_decisions.decision_options", return_value=[option]),
              patch("pro_replay.match_decisions.known_dead_at", return_value=False) as dead):
            first, = run()
            self.assertEqual((first["time"], first["event"], first["review_from"]), (100, death["text"], 95))
            self.assertIn("50 сек", first["importance"])
            self.assertTrue(all(c.kwargs == {"before": True} for c in dead.call_args_list))
            dead.side_effect = lambda raw, who, heroes, t, before: who == 128
            self.assertEqual(run(), [])
            dead.side_effect = None
            option["event_actor_slot"] = 129
            self.assertEqual(run(), [])

    def test_unrelated_death_cannot_supply_an_exact_cast_or_save_time(self):
        fight = {"start": 100, "end": 120, "items": []}
        death = {"time": 114, "kind": "death", "target_slot": 2, "actor_slot": 128,
                 "text": "Another ally died", "perspective": "ally"}
        save = {"kind": "protect", "target": "Mars", "target_slot": 1, "focus_slot": 128,
                "enemy_focus": "Lion", "button": "Glimmer Cape", "strength": 2}
        with (patch("pro_replay.match_decisions.observed_events", return_value=[death]),
              patch("pro_replay.match_decisions.decision_options", return_value=[save])):
            first, = top_decisions({}, 0, {}, [fight], [])
            self.assertIsNone(first["time"])
            self.assertEqual(first["review_from"], fight["start"])
            self.assertNotIn(death["text"], first["event"])


class CoachMatchSourceTests(unittest.TestCase):
    def test_normal_and_enriched_match_reads(self):
        for enriched, params in ((False, None), (True, {"meta": 1})):
            api = OpenDota(match_metadata=enriched)
            with patch.object(api, "_get", return_value={"match_id": 123}) as get:
                self.assertEqual(api.match(123)["match_id"], 123)
                get.assert_called_once_with("/matches/123", params)

    def test_basic_match_remains_available_if_optional_metadata_fails(self):
        api = OpenDota(match_metadata=True)
        with patch.object(api, "_get", side_effect=[SourceError("metadata failed", 502), {"match_id": 123}]) as get:
            self.assertEqual(api.match(123)["match_id"], 123)
            self.assertEqual(get.call_args_list, [call("/matches/123", {"meta": 1}), call("/matches/123")])
        with patch.object(api, "_get", side_effect=SourceError("rate limited", 429)) as get:
            with self.assertRaises(SourceError):
                api.match(123)
            self.assertEqual(get.call_count, 1)
