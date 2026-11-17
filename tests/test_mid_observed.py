import unittest

from pro_replay.mid_observed import observed_exchanges, render_observed_exchanges


def hit(t, source, target, amount, key=None):
    return {"game_time": t, "tick": int(30*t), "kind": "spell_damage" if key else "attack_damage",
            "actor_slot": source, "target_slot": target, "damage": amount, "button_key": key}


class ObservedExchangeTests(unittest.TestCase):
    def test_only_actual_bilateral_damage_and_no_overlap(self):
        rows = [hit(20, 1, 130, 20), hit(21, 130, 1, 15, "bolt"), hit(21, 4, 1, 100),
                hit(21, 130, 4, 300), hit(22, 130, 1, 10), hit(23.1, 130, 1, 60),
                hit(27, 1, 130, 80)]
        groups = observed_exchanges(list(reversed(rows)), 1, 130, {t:30*t for t in range(20,28)}, {"bolt":"Bolt"})
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["dealt"], groups[0]["received"]), (20,25))
        self.assertEqual(groups[0]["enemy_actions"], {"Bolt":15, "Обычная атака":10})
        self.assertIn("0:20–0:22", render_observed_exchanges(groups, "Enemy"))

    def test_no_opponent_or_unlocated_hit(self):
        rows = [hit(10, 1, 130, 20), hit(11, 130, 1, 15), hit(610, 130, 1, 1)]
        self.assertFalse(observed_exchanges(rows, 1, 130, {10:301, 11:330}, {}))
        self.assertFalse(observed_exchanges(rows, 1, 130, {10:300}, {}))
        groups = observed_exchanges(rows, 1, 130, {9:270,10:301,11:330}, {})
        self.assertEqual(len(groups), 1)
