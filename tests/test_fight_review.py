import unittest

from pro_replay.fight_review import extract_fights, render_fights


def make_match():
    parts = [dict(damage=10, healing=0, deaths=1, gold_delta=-100, killed={},
                  item_uses={}, ability_uses={}, buybacks=0) for _ in range(10)]
    parts[7].update(damage=60, healing=140, deaths=0, gold_delta=450, buybacks=1,
                    killed={"npc_dota_hero_nevermore": 2, "npc_dota_creep": 4},
                    item_uses={"force_staff": 1}, ability_uses={"dazzle_shallow_grave": 1},
                    ability_targets={"dazzle_shallow_grave": {"npc_dota_hero_ursa": 1}})
    heroes = {11: {"name": "npc_dota_hero_nevermore", "localized_name": "Shadow Fiend"},
              50: {"name": "npc_dota_hero_dazzle", "localized_name": "Dazzle"}}
    player = {"hero_id": 50, "player_slot": 130,
              "kills_log": [{"time": t, "key": "npc_dota_hero_nevermore"} for t in (99, 100, 130, 131)],
              "buyback_log": [{"time": 105}]}
    return {"players": [player], "teamfights": [{"start": 100, "end": 130, "players": parts}]}, heroes


class FightReviewTests(unittest.TestCase):
    def test_dire_slot_arrives_with_personal_actions_and_both_teams(self):
        raw, heroes = make_match()
        data = extract_fights(raw, 130, heroes)
        f = data["intervals"][0]
        self.assertEqual((f["damage"], f["team"]["damage"], f["enemy"]["damage"]), (60, 100, 50))
        self.assertEqual((f["team"]["deaths"], f["enemy"]["deaths"], f["team"]["gold_delta"]), (4, 5, 50))
        self.assertEqual(f["kills"], {"Shadow Fiend": 2})
        self.assertEqual([(e["time"], e["kind"]) for e in f["events"]], [(100, "kill"), (105, "buyback"), (130, "kill")])
        html = render_fights(data)
        self.assertIn("60,0%", html)
        self.assertIn("100,0%", html)
        self.assertIn("Shallow Grave → Ursa", html)
        self.assertIn("Force Staff ×1", html)

    def test_missing_teammate_suppresses_team_share_without_erasing_player(self):
        raw, heroes = make_match()
        parts = raw["teamfights"][0]["players"]
        del parts[5]["damage"]
        parts[7]["item_uses"] = None
        f = extract_fights(raw, 130, heroes)["intervals"][0]
        self.assertIsNone(f["team"]["damage"])
        self.assertEqual(f["damage"], 60)
        self.assertIsNone(f["items"])
        self.assertEqual(f["team"]["healing"], 140)

    def test_invalid_fights_report_coverage_and_missing_actions_stay_unknown(self):
        raw, heroes = make_match()
        raw["teamfights"].append({"start": 20, "end": 10, "players": [{}] * 10})
        raw["teamfights"][0]["players"][7]["ability_targets"] = {}
        raw["players"][0]["ability_targets"] = {"dazzle_shallow_grave": {"npc_dota_hero_ursa": 20}}
        data = extract_fights(raw, 130, heroes)
        self.assertEqual(data["recorded"], 2)
        self.assertEqual(data["intervals"][0]["targets"], [])
        self.assertIn("1 из 2", render_fights(data))
        self.assertIn("отсутствует", render_fights(extract_fights({}, 1, {})))
        self.assertIn("пустой журнал", render_fights(extract_fights({"teamfights": []}, 1, {})))

    def test_unsafe_names_do_not_reach_markup(self):
        raw, heroes = make_match()
        heroes[11]["localized_name"] = "<script>"
        raw["teamfights"][0]["players"][7]["item_uses"] = {"<svg>": 1}
        html = render_fights(extract_fights(raw, 130, heroes))
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<svg>", html.lower())

    def test_postfight_objectives_owner_actor_and_overlap_boundaries(self):
        raw, heroes = make_match()
        raw["teamfights"].append({**raw["teamfights"][0], "start": 140, "end": 150})
        raw["objectives"] = [
            {"time": 130, "type": "CHAT_MESSAGE_AEGIS", "player_slot": 130},
            {"time": 131, "type": "building_kill", "key": "npc_dota_goodguys_tower2_mid", "unit": "npc_dota_hero_dazzle"},
            {"time": 140, "type": "CHAT_MESSAGE_AEGIS", "player_slot": 130},
            {"time": 149, "type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 3},
            {"time": 210, "type": "CHAT_MESSAGE_AEGIS", "player_slot": 130},
            {"time": 151, "type": "building_kill", "key": "npc_dota_badguys_range_rax_bot",
             "unit": "npc_dota_creep_goodguys_ranged", "player_slot": 130},
            {"time": 151, "type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2},
            {"time": 211, "type": "CHAT_MESSAGE_AEGIS", "player_slot": 130},
            {"time": 152, "type": "building_kill", "key": "npc_dota_badguys_tower_unknown"},
        ]
        first, second = extract_fights(raw, 130, heroes)["intervals"]
        self.assertEqual([e["time"] for e in first["objectives_after"]], [131])
        self.assertIn("T2 команды Radiant", first["objectives_after"][0]["label"])
        self.assertIn("последний удар — Dazzle", first["objectives_after"][0]["label"])
        self.assertEqual([e["time"] for e in second["objectives_after"]], [151, 151, 210])
        self.assertIn("казарма дальнего боя команды Dire", second["objectives_after"][0]["label"])
        self.assertNotIn("Dazzle", second["objectives_after"][0]["label"])
        self.assertEqual(second["objectives_after"][1]["label"], "Команда Radiant убила Roshan")
        self.assertIn("Dazzle (Dire)", second["objectives_after"][2]["label"])
        self.assertIn("(+60 с)", render_fights(extract_fights(raw, 130, heroes)))

    def test_missing_objectives_and_unusable_intervening_fight(self):
        raw, heroes = make_match()
        self.assertNotIn("objectives_after", extract_fights(raw, 130, heroes)["intervals"][0])
        raw["teamfights"].append({"start": 132, "end": 140, "players": []})
        raw["objectives"] = [{"time": 145, "type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 99}]
        self.assertEqual(extract_fights(raw, 130, heroes)["intervals"][0]["objectives_after"], [])
        raw["teamfights"].pop()
        self.assertEqual(extract_fights(raw, 130, heroes)["intervals"][0]["objectives_after"][0]["label"],
                         "Roshan погиб · сторона неизвестна")
