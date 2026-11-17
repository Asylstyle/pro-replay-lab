from copy import deepcopy
import unittest

from pro_replay.mid_windows import mid_decision_windows, render_mid_windows


class MidWindowsTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"match_id":19,"duration":300,"players":[{"player_slot":s,"hero_id":i}
                    for s,i in zip([0,1,2,3,4,128,129,130,131,132], [34,1,2,4,5,39,6,7,8,9])]}
        self.matchup = {"status":"available","patch":"7.41f",
                        "self":{"slot":0,"id":34,"name":"Tinker"},"opponent":{"slot":128,"id":39,"name":"Queen of Pain"}}
        actor = {"hero_id":34,"x":0,"y":0,"alive":True,"unit_state":0,"level":3,"mana":135,"hp":450,"max_hp":500,
                 "buttons":{"tinker_laser":{"kind":"ability","level":2,"active":True,"cooldown":0,"mana_cost":105}}}
        target = {"hero_id":39,"x":350,"y":0,"alive":True,"unit_state":0,"level":2,"hp":265,"max_hp":500,
                  "takes_damage":True,"visible_to":{"2":True},"magic_resist_pct":25}
        creep = {"id":7641,"name":"npc_dota_creep_badguys_ranged","x":400,"y":0,"hp":140,"max_hp":300,
                 "takes_damage":True,"team":3,"unit_state":0,"visible_to":{"2":True},"magic_resist_pct":0}
        self.frame = {"time":100,"tick":3000,"game_time":100.02,"heroes":{"0":actor,"128":target},"ranged_creeps":[creep],
                      "mid_towers":[{"name":"dota_goodguys_tower1_mid","team":2,"x":-1200,"y":-200},
                                    {"name":"dota_badguys_tower1_mid","team":3,"x":1200,"y":150}]}
        self.timeline = {"format":"pro-replay-truth-v1","match_id":19,"events":[],"frames":[self.frame]}

    def review(self, timeline=None):
        return mid_decision_windows(self.raw,0,self.matchup,self.timeline if timeline is None else timeline)

    def test_single_visible_frame_yields_trade_and_skill_threshold(self):
        review = self.review()
        trade, creep = review["trades"][0], review["creeps"][0]
        self.assertEqual((trade["rank"],trade["level"],trade["opponent_level"]),(2,3,2))
        self.assertEqual((creep["time"],creep["damage"],creep["hp"]),(100,150,140))
        self.assertFalse(creep["alive_after_estimate"])
        next_frame = deepcopy(self.frame)
        next_frame.update(time=101,game_time=101.01,tick=3030)
        self.timeline["frames"].append(next_frame)
        self.assertTrue(self.review()["creeps"][0]["alive_after_estimate"])
        next_frame["ranged_creeps"][0]["id"] += 1
        self.assertFalse(self.review()["creeps"][0]["alive_after_estimate"])

    def test_uncertain_input_and_fog_suppress_candidate(self):
        for case in ("cooldown","mana","silence","unlearned","range","hidden","immune","unmapped_axis","wrong_hero"):
            with self.subTest(case=case):
                t = deepcopy(self.timeline)
                f = t["frames"][0]
                a, enemy, c = f["heroes"]["0"], f["heroes"]["128"], f["ranged_creeps"][0]
                spell = a["buttons"]["tinker_laser"]
                if case == "cooldown": spell["cooldown"] = .1
                elif case == "mana": a["mana"] = 100
                elif case == "silence": a["unit_state"] = 1 << 3
                elif case == "unlearned": spell["level"] = 0
                elif case == "range": enemy["x"] = c["x"] = 602
                elif case == "hidden": enemy["visible_to"] = c["visible_to"] = {"3":True}
                elif case == "immune": enemy["unit_state"] = c["unit_state"] = 1 << 8
                elif case == "unmapped_axis": f["mid_towers"] = []
                elif case == "wrong_hero": a["hero_id"] = 1
                review = self.review(t)
                self.assertFalse(review.get("trades") or review.get("creeps"))
        self.assertIn("reason", self.review(dict(self.timeline,match_id=20)))
        self.assertIn("reason", self.review(dict(self.timeline,patch_version="7.42")))

    def test_damage_type_resistance_and_enemy_only(self):
        self.frame["heroes"]["0"]["buttons"] = {"tinker_laser":{"kind":"ability","active":True,"level":1,
                                                                "mana_cost":105,"cooldown":0}}
        spec = {"tinker_laser":{"name":"Тест","max_rank":4,"cast_point":[.1],"proof":{
            "damage":[180],"shape":"line","damage_type":"magical","delay":[0],"speed":[1000],
            "reach":[600],"target":"both"}}}
        c = self.frame["ranged_creeps"][0]
        def sample():
            return mid_decision_windows(self.raw,0,self.matchup,self.timeline,catalog=spec)
        self.assertEqual(sample()["creeps"][0]["damage"],180)
        c["magic_resist_pct"] = 30
        self.assertFalse(sample()["creeps"])
        del c["magic_resist_pct"]
        self.assertFalse(sample()["creeps"])
        c.update(magic_resist_pct=0,team=2,name="npc_dota_creep_goodguys_ranged")
        self.assertFalse(sample()["creeps"])
        c.update(team=3,name="npc_dota_creep_badguys_ranged_mega")
        self.assertFalse(sample()["creeps"])

    def test_observed_cast_damage_and_last_hit_need_independent_evidence(self):
        base = {"time":100,"tick":3005,"actor_slot":0,"button_key":"tinker_laser"}
        ranged = self.frame["ranged_creeps"][0]["name"]
        self.timeline["events"] = [dict(base,kind="cast"),dict(base,kind="spell_damage",target_name=ranged,hp_after=0),
            dict(base,kind="spell_damage",target_slot=128,damage=150),dict(base,kind="ranged_last_hit",target_name=ranged),
            dict(base,kind="ranged_last_hit",target_name="npc_dota_creep_goodguys_ranged"),
            dict(base,kind="ranged_last_hit",target_name=ranged,button_key="witch_blade")]
        review = self.review()
        self.assertEqual(review["creeps"][0]["recorded_cast"],100)
        self.assertEqual(review["last_hits"],[{"time":100,"spell":"Laser"}])
        self.assertEqual(review["hero_hits"][0]["damage"],150)
        self.assertIn("Фактические добивания",render_mid_windows(review))
        self.timeline["events"] = [dict(base,kind="spell_damage",target_name=ranged,hp_after=0)]
        self.assertFalse(self.review()["last_hits"])

    def test_creep_cannot_stand_in_for_hero_advantage(self):
        enemy = self.frame["heroes"]["128"]
        enemy.update(level=4,hp=500)
        self.assertFalse(self.review()["trades"])
        enemy["visible_to"] = {"2":False}
        self.assertEqual(len(self.review()["creeps"]),1)

    def test_facing_aoe_requires_real_orientation_and_boundary_margin(self):
        actor = self.frame["heroes"]["0"]
        actor["buttons"] = {"nevermore_shadowraze3":{"level":1,"kind":"ability","active":True,"cooldown":0,"mana_cost":75}}
        spec = {"nevermore_shadowraze3":{"name":"Shadowraze","max_rank":4,"cast_point":[.55],"proof":{
            "shape":"forward_circle","offset":[700],"radius":[250],"reach":[250],"target":"both",
            "damage_type":"magical","damage":[180],"delay":[0],"speed":None}}}
        c = self.frame["ranged_creeps"][0]
        c.update(x=700)
        def windows():
            return mid_decision_windows(self.raw,0,self.matchup,self.timeline,catalog=spec)["creeps"]
        self.assertFalse(windows())
        actor["facing_yaw"] = 0
        self.assertTrue(windows())
        actor["facing_yaw"] = 180
        self.assertFalse(windows())
        actor["facing_yaw"] = 0
        c["x"] = 943
        self.assertFalse(windows())

    def test_attack_geometry_without_a_flat_nuke_or_unknown_attack_readiness_claim(self):
        actor = self.frame["heroes"]["0"]
        actor.update(attack_range=600,buttons={})
        review = self.review()
        self.assertEqual(review["attacks"][0]["level"],3)
        self.assertEqual(review["attacks"][0]["distance"],350)
        self.assertFalse(review["trades"])
        actor["unit_state"] = 1 << 1
        self.assertFalse(self.review()["attacks"])

    def test_two_targets_in_one_circle_excludes_hidden_rival_or_unit_target(self):
        spec = {"tinker_laser":{"name":"Nuke","max_rank":4,"cast_point":[.1],"proof":{
            "damage":[180],"shape":"self_radius","damage_type":"pure","delay":[0],"speed":None,
            "reach":[600],"target":"both"}}}
        def sample():
            return mid_decision_windows(self.raw,0,self.matchup,self.timeline,catalog=spec)["creeps"][0]["joint_hero"]
        self.assertTrue(sample())
        self.frame["heroes"]["128"]["visible_to"] = {"2":False}
        self.assertFalse(sample())
        self.frame["heroes"]["128"]["visible_to"] = {"2":True}
        spec["tinker_laser"]["proof"]["shape"] = "unit"
        self.assertFalse(sample())

    def test_creep_disappears_before_long_cast_completes(self):
        after = deepcopy(self.frame)
        after.update(time=101,game_time=101.02,tick=3030,ranged_creeps=[])
        self.timeline["frames"].append(after)
        spec = {"tinker_laser":{"name":"Nuke","max_rank":4,"cast_point":[1.4],"proof":{
            "damage":[180],"shape":"unit","damage_type":"pure","delay":[0],"speed":None,
            "reach":[600],"target":"both"}}}
        result = mid_decision_windows(self.raw,0,self.matchup,self.timeline,catalog=spec)
        self.assertFalse(result["creeps"])
