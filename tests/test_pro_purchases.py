import unittest

from pro_replay.fight_coach import CANDIDATES
from pro_replay.impact import SLOTS
from pro_replay.pro_purchases import PurchaseIndex, add_purchase_evidence, fit_purchase_reference


def game(mid):
    heroes = (26, 39, 74, 33, 80, 22, 27, 99, 69, 14)
    players, actors = [], []
    for i, (slot, hero) in enumerate(zip(SLOTS, heroes)):
        players.append({"player_slot": slot, "hero_id": hero, "position_est": i % 5 + 1,
                        "purchase_log": [{"key": "boots", "time": 120}]})
        actors.append({"ability_uses": {}, "item_uses": {}})
    players[0]["purchase_log"].append({"key": "glimmer_cape", "time": 600 if mid % 2 else 1100})
    actors[0].update(ability_uses={"lion_voodoo": 1}, item_uses={"glimmer_cape": 1})
    actors[5]["ability_uses"] = {"zuus_lightning_bolt": 2}
    return {"match_id": mid, "start_time": mid * 10000, "duration": 2000, "lobby_type": 1,
            "leagueid": 2, "league": {"tier": "premium"}, "patch": 60,
            "players": players, "teamfights": [{"start": 1100, "end": 1140, "players": actors}]}


class HistoricalPurchaseTests(unittest.TestCase):
    def setUp(self):
        games = [game(i) for i in range(1, 14)]
        games[9]["players"][0]["purchase_log"] = None
        games[10]["patch"] = 59
        games[11]["duration"] = 800
        train_ids = list(range(1, 14))
        data = fit_purchase_reference([*games, game(4), game(999)],
                                      {"coverage": {"train_match_ids": train_ids}},
                                      {k for keys in CANDIDATES.values() for k in keys})
        self.assertEqual(len(data["games"]), 13)
        self.index = PurchaseIndex(data)
        self.row = {"match_id": 8, "start_time": 130000, "patch": 60, "hero_id": 26,
                    "detail": {"fight_review": {"hero_prefix": "lion"}}}

    def test_only_earlier_matches_correct_role_patch_enemy_and_reached_time(self):
        rates = self.index.rates(self.row, 1, 1100, {22}, ["glimmer_cape"])
        # Cases 1..7 and 9; 8 is the client, 10 has no purchase log, 11 the wrong
        # patch, 12 ended early, 13 is later. An exact-time purchase lies outside.
        self.assertEqual((rates["glimmer_cape"]["n"], rates["glimmer_cape"]["bought"]), (8, 5))
        self.assertTrue(rates["glimmer_cape"]["same_hero"])
        self.assertFalse(self.index.rates(self.row, None, 1100, {22}, ["glimmer_cape"]))
        self.assertFalse(self.index.rates(self.row, 1, 1100, {333}, ["glimmer_cape"]))

    def test_fights_match_same_enemy_cast_within_five_minutes(self):
        threat = {"category": "magic_burst", "hero_id": 22, "hero": "Zeus", "key": "zuus_lightning_bolt",
                  "ability": "Lightning Bolt"}
        fight = {"start": 1100, "threats": [threat], "items": [{"category": "magic_burst", "key": "glimmer_cape",
                  "hero_id": 22, "ability": "Lightning Bolt", "threat_hero": "Zeus"}]}
        add_purchase_evidence(fight, self.row, 1, self.index)
        self.assertEqual(fight["pro_action"]["ability"], "Hex")
        self.assertEqual(fight["items"][0]["fight_pro"]["n"], 5)
        self.assertEqual(fight["items"][0]["fight_pro"]["uses"], 5)
        self.assertEqual(fight["items"][0]["pro"]["n"], 8)
        isolated = {"start": 1401, "items": [], "threats": [threat]}
        add_purchase_evidence(isolated, self.row, 1, self.index)
        self.assertNotIn("pro_action", isolated)
