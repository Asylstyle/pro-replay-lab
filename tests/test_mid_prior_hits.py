from unittest import TestCase

from pro_replay.mid_prior_hits import annotate_prior_hits, lane_sample, render_prior_hits


class PriorPacketsTests(TestCase):
    def setUp(self):
        self.skill = {"key":"conditional_w","name":"Conditional W","max_rank":4,"mechanic":"conditional","proof":None}
        self.kits = {0:{}, 128:{"conditional_w":self.skill}}
        self.units = {
            0:{"hero_id":34,"name":"npc_dota_hero_tinker","alive":True,"unit_state":0,"mana":200,
               "visible_to":{"2":True,"3":True},"buttons":{}},
            128:{"hero_id":86,"name":"npc_dota_hero_rubick","alive":True,"unit_state":0,"mana":200,
                 "visible_to":{"2":True,"3":True},"buttons":{"conditional_w":{
                     "kind":"ability","active":True,"level":2,"mana_cost":60,"cooldown":0}}},
        }
        self.events = [{"kind":"cast","tick":305,"time":10,"game_time":10.2,"actor_slot":128,"button_key":"conditional_w"}]
        for second, damage in ((11,24),(12,40),(16,200)):
            self.events.append({"kind":"spell_damage","tick":30*second+8,"time":second,"game_time":second+.3,"direct_hero_source":True,
                 "actor_slot":128,"target_slot":0,"target_name":"npc_dota_hero_tinker","button_key":"conditional_w","damage":damage})

    def sample(self, sec):
        return lane_sample({"tick":30*sec,"game_time":sec+.03,"time":sec},self.units,self.kits,2)

    def history(self, samples, events=None):
        cards=[{"time":15,"tick":450,"action":"uncertain"}]
        annotate_prior_hits(cards,self.events if events is None else events,samples,0,128,self.kits)
        self.assertEqual(cards[0]["action"],"uncertain")
        return cards[0]["prior_hits"]

    def test_same_two_visible_miders_rank_and_preceding_packets_only(self):
        samples={i:self.sample(i) for i in range(10,18)}
        rows=self.history(samples)
        self.assertEqual([(r["source_slot"],r["target_slot"],r["rank"],r["count"],r["damage_min"],r["damage_max"],
                           r["last_time"]) for r in rows],[(128,0,2,2,24,40,12.3)])
        self.assertIn('по 24–40 HP',render_prior_hits(rows,"Rubick"))

    def test_fog_gap_changed_rank_stale_source_and_wrong_target_suppress_packets(self):
        samples={i:self.sample(i) for i in range(10,18)}
        self.units[128]["visible_to"]["2"]=False
        self.assertIsNone(self.sample(11))
        self.assertFalse(self.history({s:v for s,v in samples.items() if s!=11}))
        self.units[128]["visible_to"]["2"]=True
        self.units[128]["buttons"]["conditional_w"]["level"]=1
        self.assertFalse(self.history(samples | {10:self.sample(10)}))
        for change in ({"actor_slot":129},{"target_slot":1},{"target_name":"npc_dota_hero_other"},{"attacker_illusion":True},
                       {"actor_name":"npc_dota_forge_spirit"},{"direct_hero_source":False},{"direct_hero_source":None},
                       {"kind":"attack_damage"},{"damage":0}):
            self.assertFalse(self.history(samples,[self.events[0],dict(self.events[1],**change)]))
        self.assertFalse(self.history(samples,self.events[1:]))  # rank of the initiating cast cannot be verified

    def test_current_readiness_and_clone_ambiguity(self):
        samples={i:self.sample(i) for i in range(10,18)}
        self.units[128]["buttons"]["conditional_w"]["cooldown"]=8
        self.assertFalse(self.history(samples | {15:self.sample(15)}))
        self.units[128]["hero_id"]=113
        self.units[128]["buttons"]["arc_warden_tempest_double"]={"level":1}
        self.assertIsNone(self.sample(15))

    def test_missing_following_snapshot_and_same_tick_are_not_prior(self):
        samples={i:self.sample(i) for i in range(10,18)}
        hit=dict(self.events[1],time=15,tick=450,game_time=15.03)
        self.assertFalse(self.history(samples,[self.events[0],hit]))
        self.assertFalse(self.history({s:v for s,v in samples.items() if s!=12},self.events[:2]))

    def test_invoked_damage_requires_matching_orb_levels(self):
        u=self.units[128]
        u["hero_id"],u["name"]=74,"npc_dota_hero_invoker"
        u["buttons"]["invoker_cold_snap"]=dict(u["buttons"].pop("conditional_w"),level=1,slot=3)
        for orb in ("invoker_quas","invoker_wex","invoker_exort"):
            u["buttons"][orb]={"kind":"ability","level":2}
        self.kits[128]={"invoker_cold_snap":dict(self.skill,key="invoker_cold_snap")}
        events=[dict(e,button_key="invoker_cold_snap") for e in self.events]
        samples={i:self.sample(i) for i in range(10,18)}
        self.assertTrue(self.history(samples,events))
        u["buttons"]["invoker_quas"]["level"]=3
        self.assertFalse(self.history(samples | {15:self.sample(15)},events))
