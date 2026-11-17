from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from pro_replay.mid_exchange import render_exchange_decisions
from pro_replay.mid_windows import mid_decision_windows


def spell(key, damage=120, shape="unit"):
    return {"id":101, "key":key, "name":key, "max_rank":4, "rank_gates":[1,3,5,7], "range":[600],
            "mechanic":"direct", "cast_point":[0.2], "proof":{"damage":[damage], "shape":shape, "damage_type":"pure",
            "reach":[600], "delay":[0], "speed":None, "target":"both"}}


def button(cd=0, mana=50):
    return {"kind":"ability", "level":1, "active":True, "cooldown":cd, "mana_cost":mana}


class ExchangeTests(TestCase):
    def setUp(self):
        self.actor = {"x":0, "y":0, "hero_id":34, "level":2, "hp":600, "max_hp":600, "alive":True,
                      "mana":200, "unit_state":0, "takes_damage":True, "visible_to":{"2":True, "3":True},
                      "magic_resist_pct":25, "armor":0, "agility_total":18, "intellect_total":22,
                      "attack_min":45, "attack_max":50, "attack_bonus":0, "attack_range":100,
                      "buttons":{"our_nuke":button()}}
        self.enemy = dict(deepcopy(self.actor), hero_id=86, x=350, buttons={"their_nuke":button()})
        self.creep = {"id":99, "name":"npc_dota_creep_badguys_ranged", "team":3, "hp":65, "max_hp":300,
                      "x":355, "y":10, "armor":0, "magic_resist_pct":0, "unit_state":0, "takes_damage":True,
                      "visible_to":{"2":True}}
        self.frame = {"time":100, "tick":3000, "game_time":100.05, "heroes":{"0":self.actor, "128":self.enemy},
                      "ranged_creeps":[self.creep], "mid_towers":[{"team":2,"x":-1300,"y":0,"name":"dota_goodguys_tower1_mid"},
                      {"team":3,"x":1500,"y":0,"name":"dota_badguys_tower1_mid"}]}
        self.own_kit={"our_nuke":spell("our_nuke")}
        self.enemy_kit={"their_nuke":spell("their_nuke",200)}
        self.matchup={"status":"available","patch":"7.41f","self":{"id":34,"slot":0,"name":"A"},
                      "opponent":{"id":86,"slot":128,"name":"B"}}
        self.raw={"match_id":33,"duration":600,"players":[{"hero_id":n,"player_slot":s}
                  for s,n in zip((0,1,2,3,4,128,129,130,131,132),(34,1,2,4,5,86,6,7,8,9))]}

    def cards(self, with_next=False):
        frames=[self.frame]
        if with_next:
            later=deepcopy(self.frame)
            later.update(time=101,tick=3030,game_time=101.05)
            frames.append(later)
        timeline={"format":"pro-replay-truth-v1", "match_id":33, "frames":frames}
        with patch("pro_replay.mid_windows.spell_specs", return_value=self.enemy_kit):
            return mid_decision_windows(self.raw,0,self.matchup,timeline,catalog=self.own_kit)["exchange_decisions"]

    def test_equal_hp_and_levels_change_verdict_with_the_opponents_cooldown(self):
        first=self.cards()[0]
        self.assertEqual((first["action"],first["own"]["spell"],first["creep"]["hp"]),("creep","our_nuke",65))
        self.assertEqual(first["reply"]["best"]["damage_max"],200)
        self.enemy["buttons"]["their_nuke"]["cooldown"]=5
        second=self.cards()[0]
        self.assertEqual(second["action"],"trade")
        self.assertEqual(second["reply"]["blocked"],[{"spell":"their_nuke","cooldown":5}])
        self.assertEqual(second["margin_pp"],20)
        self.assertIn("восстановится через 5.0",render_exchange_decisions([second],"B"))
        self.enemy["buttons"]["their_nuke"].update(cooldown=0,mana_cost=300)
        self.assertEqual(self.cards()[0]["reply"]["blocked"][0]["mana_short"],True)

    def test_fog_other_player_unknown_ready_cast_and_imminent_response(self):
        self.enemy["buttons"]["their_nuke"]["cooldown"]=0.3
        c=self.cards()[0]
        self.assertEqual(c["action"],"creep")
        self.assertIsNone(c["incoming_pct"])
        self.enemy["buttons"]["their_nuke"]["cooldown"]=5
        self.enemy["visible_to"]["2"]=False
        self.assertEqual(self.cards(),[])
        self.enemy["visible_to"]["2"]=True
        self.actor["visible_to"]["3"]=False
        self.assertEqual(self.cards(),[])
        self.actor["visible_to"]["3"]=True
        self.frame["heroes"]["129"]=dict(self.enemy,hero_id=6)
        self.assertNotEqual(self.cards()[0]["action"],"trade")

    def test_unknown_enemy_damage_is_not_zero_and_in_range_control_prefers_cs(self):
        self.enemy_kit["their_nuke"]["proof"]=None
        c=self.cards()[0]
        self.assertIn("their_nuke",c["caution"][0])
        self.assertIsNone(c["margin_pp"])
        self.enemy_kit={"rubick_telekinesis":dict(spell("Telekinesis"),proof=None)}
        self.enemy["buttons"]={"rubick_telekinesis":button()}
        c=self.cards()[0]
        self.assertEqual(c["reply"]["controls"],["Telekinesis"])
        self.assertEqual(c["action"],"creep")

    def test_double_hit_requires_same_skill_no_control_and_verified_creep_survival(self):
        self.own_kit["our_nuke"]["proof"]["shape"]="self_radius"
        self.own_kit["other"]=spell("other",300)
        self.actor["buttons"]["other"]=button()
        self.enemy["buttons"]["their_nuke"]["cooldown"]=5
        self.assertNotEqual(self.cards()[0]["action"],"both")
        c=self.cards(with_next=True)[0]
        self.assertEqual((c["action"],c["own"]["key"],c["creep"]["key"]),("both","our_nuke","our_nuke"))
        self.enemy_kit={"rubick_telekinesis":dict(spell("Telekinesis"),proof=None)}
        self.enemy["buttons"]={"rubick_telekinesis":button()}
        self.assertNotEqual(self.cards(with_next=True)[0]["action"],"both")

    def test_ultimate_last_hit_and_unobserved_swing_are_not_ranked_as_free_actions(self):
        self.own_kit["our_nuke"]["ultimate"]=True
        self.enemy["buttons"]["their_nuke"]["cooldown"]=5
        self.assertIsNone(self.cards()[0]["creep"])
        self.actor.update(attack_range=600,attack_min=200,attack_max=260,buttons={"our_nuke":button(cd=5)})
        self.frame["ranged_creeps"]=[]
        c=self.cards()[0]
        self.assertEqual(c["own"]["key"],"attack")
        self.assertIsNone(c["own"]["ready"])
        self.assertEqual(c["action"],"uncertain")

    def test_marginal_damage_edge_yields_to_cs_when_one_button_must_choose(self):
        self.enemy_kit["their_nuke"]["proof"]["damage"]=[102]
        c=self.cards()[0]
        self.assertEqual((c["margin_pp"], c["action"]),(3.0,"creep"))
        self.frame["ranged_creeps"]=[]
        self.assertEqual(self.cards()[0]["action"],"trade")

    def test_only_prior_periodic_damage_can_withhold_positive_trade(self):
        self.enemy_kit={"huskar_burning_spear":dict(spell("Burning Spear"),proof=None,
                        parameters=[{"field":"duration","values":[9]}])}
        self.enemy["buttons"]={"huskar_burning_spear":button(cd=5)}
        def decision(tick):
            e={"kind":"spell_damage","actor_slot":128,"target_slot":0,"button_key":"huskar_burning_spear",
               "time":100,"tick":tick}
            timeline={"format":"pro-replay-truth-v1","match_id":33,"events":[e],"frames":[self.frame]}
            with patch("pro_replay.mid_windows.spell_specs",return_value=self.enemy_kit):
                return mid_decision_windows(self.raw,0,self.matchup,timeline,catalog=self.own_kit)["exchange_decisions"][0]
        self.assertEqual(decision(2998)["action"],"creep")
        self.assertEqual(decision(3010)["action"],"trade")
