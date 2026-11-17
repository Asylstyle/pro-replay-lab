import unittest

from pro_replay.decision_events import inventory_evidence, item_present, known_dead_at, observed_events


HEROES = {
    129: {"name": "npc_dota_hero_mars", "localized_name": "Mars"},
    86: {"name": "npc_dota_hero_rubick", "localized_name": "Rubick"},
    93: {"name": "npc_dota_hero_slark", "localized_name": "Slark"},
    5: {"name": "npc_dota_hero_crystal_maiden", "localized_name": "Crystal Maiden"},
}


def example():
    # Fields and timings mirror OpenDota's 9085649152 Mars example.
    return {"match_id": 9085649152, "players": [
        {"hero_id": 129, "player_slot": 0, "deaths_log": [
            {"time": 2244, "key": HEROES[93]["name"], "time_dead": 80, "gold_lost": 470}],
         "max_hero_hit": {"type": "max_hero_hit", "time": 2223, "key": HEROES[86]["name"],
                          "unit": HEROES[129]["name"], "value": 640, "player_slot": 0, "inflictor": "mars_gods_rebuke"}},
        {"hero_id": 86, "player_slot": 128},
        {"hero_id": 93, "player_slot": 129, "kills_log": [{"time": 2244, "key": HEROES[129]["name"]}]},
        {"hero_id": 5, "player_slot": 1},
    ]}


WINDOW = {"start": 2211, "end": 2259}


class DecisionEventTests(unittest.TestCase):
    def test_deduplicated_killer_victim_and_actual_damage_event(self):
        hit, death = observed_events(example(), 0, HEROES, WINDOW)
        self.assertEqual((hit["time"], hit["kind"], hit["ability"], hit["damage"]),
                         (2223, "hero_hit", "mars_gods_rebuke", 640))
        self.assertEqual((hit["actor"], hit["target"], hit["perspective"]), ("Mars", "Rubick", "self"))
        self.assertEqual((death["actor_slot"], death["target_slot"], death["time"]), (129, 0, 2244))
        self.assertEqual((death["actor_relation"], death["perspective"], death["gold_lost"], death["time_dead"]),
                         ("enemy", "self", 470, 80))
        self.assertEqual(death["sources"], ["deaths_log", "kills_log"])
        self.assertIn("Slark", death["text"])
        self.assertEqual(observed_events(example(), 1, HEROES, WINDOW)[1]["perspective"], "ally")
        self.assertEqual(observed_events(example(), 128, HEROES, WINDOW)[1]["perspective"], "enemy")

    def test_only_exact_events_and_one_event_per_victim_not_one_per_second(self):
        raw = example()
        raw["players"][1]["kills_log"] = [{"time": 2244, "key": HEROES[5]["name"]},
                                         {"time": 2245, "key": "npc_dota_neutral_centaur_khan"}]
        raw["players"][3]["ability_targets"] = {"crystal_maiden_frostbite": {HEROES[93]["name"]: 4}}
        events = observed_events(raw, 0, HEROES, {"start": 2244, "end": 2244})
        self.assertEqual({(e["actor"], e["target"]) for e in events},
                         {("Slark", "Mars"), ("Rubick", "Crystal Maiden")})
        raw["players"][1]["kills_log"].append({"time": 2244, "key": HEROES[129]["name"]})
        death = next(e for e in observed_events(raw, 0, HEROES, WINDOW) if e["target"] == "Mars")
        self.assertIsNone(death["actor_slot"])
        self.assertEqual(death["target_slot"], 0)

    def test_ambiguous_targets_or_mismatched_player_hit_are_skipped(self):
        raw = example()
        raw["players"].append({"hero_id": 86, "player_slot": 130})
        self.assertEqual([e["kind"] for e in observed_events(raw, 0, HEROES, WINDOW)], ["death"])
        raw = example()
        raw["players"][0]["max_hero_hit"]["player_slot"] = 128
        self.assertEqual([e["kind"] for e in observed_events(raw, 0, HEROES, WINDOW)], ["death"])
        raw["players"][0]["deaths_log"][0]["key"] = "npc_dota_goodguys_tower2_top"
        raw["players"][2].pop("kills_log")
        self.assertIsNone(observed_events(raw, 0, HEROES, WINDOW)[0]["actor"])

    def test_measured_death_expires_at_respawn_and_buyback(self):
        raw = example()
        self.assertFalse(known_dead_at(raw, 0, HEROES, 2244, before=True))
        self.assertTrue(known_dead_at(raw, 0, HEROES, 2244))
        self.assertTrue(known_dead_at(raw, 0, HEROES, 2250, before=True))
        self.assertFalse(known_dead_at(raw, 0, HEROES, 2324))
        raw["players"][0]["buyback_log"] = [{"time": 2248, "player_slot": 0}]
        self.assertTrue(known_dead_at(raw, 0, HEROES, 2247))
        self.assertFalse(known_dead_at(raw, 0, HEROES, 2248))
        self.assertEqual(observed_events(raw, 0, HEROES, WINDOW)[-1]["kind"], "buyback")
        raw["players"][0].pop("deaths_log")
        self.assertTrue(known_dead_at(raw, 0, HEROES, 2244))
        self.assertFalse(known_dead_at(raw, 0, HEROES, 2245))

    def test_inventory_snapshot_must_be_latest_matching_and_at_most_30s_old(self):
        raw = example()
        snapshot = {"player_slot": 0, "team_slot": 0, "inventory_snapshot": [
            {"game_time": 2220, "item_id": [116, 50]}, {"game_time": 2250, "item_id": [50]}]}
        raw["metadata"] = {"error": None, "data": {"match_id": "9085649152",
                           "metadata": {"teams": [{"dota_team": 2, "players": [snapshot]}]}}}
        self.assertIn("37:00", inventory_evidence(raw, 0, "black_king_bar", 2244))
        self.assertIsNone(inventory_evidence(raw, 128, "black_king_bar", 2244))
        self.assertIsNone(inventory_evidence(raw, 0, "black_king_bar", 2219))
        self.assertIsNone(inventory_evidence(raw, 0, "black_king_bar", 2250))
        self.assertIsNone(inventory_evidence(raw, 0, "phase_boots", 2281))
        raw["metadata"]["data"]["match_id"] = "5"
        self.assertIsNone(inventory_evidence(raw, 0, "black_king_bar", 2244))
        raw["metadata"] = None
        self.assertIsNone(inventory_evidence(raw, 0, "black_king_bar", 2244))

    def test_absence_requires_all_six_slots_at_the_event_second(self):
        raw = example()
        buy = {"time": 1800, "key": "black_king_bar"}
        raw["players"][0]["purchase_log"] = [buy]
        items = [1, 50, 36, 178, 168, 17]
        row = {"game_time": 2250, "item_id": items, "backpack_item_id": [116]}
        snapshots = [row]
        meta_player = {"player_slot": 0, "team_slot": 0, "inventory_snapshot": snapshots}
        raw["metadata"] = {"data": {"match_id": "9085649152", "metadata": {
            "teams": [{"dota_team": 2, "players": [meta_player]}]}}}
        self.assertIs(item_present(raw, 0, "black_king_bar", 2250), False)
        self.assertIs(item_present(raw, 0, "blink", 2265), True)
        for sec in (2249, 2251, 2280, 2281):
            self.assertIsNone(item_present(raw, 0, "black_king_bar", sec))
        for incomplete in ([], items[:-1], items[:-1] + [None], items[:-1] + [999999], items + [116]):
            row["item_id"] = incomplete
            self.assertIsNone(item_present(raw, 0, "black_king_bar", 2250))
        row["item_id"] = items
        buy["time"] = 2250
        self.assertIsNone(item_present(raw, 0, "black_king_bar", 2250))
        buy["time"] = 1800
        snapshots.append(dict(row))
        self.assertIsNone(item_present(raw, 0, "black_king_bar", 2250))
        snapshots.pop()
        raw["players"][0].pop("purchase_log")
        self.assertIsNone(item_present(raw, 0, "black_king_bar", 2250))
