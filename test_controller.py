"""Behavior and local-physics regression checks for the actual V7 entrypoint.
These are synthetic observations, not competitive matches or training data.
"""
import copy
import math
import unittest
from bot import Bot
from controller import rollout, projectile_cost
from navigation import Planner
from tactics import EnemyTracker
from test_bot import ROOT, fixture_world, messages, wall


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hello,_=messages();hello['map']['dir']=str(ROOT/'arena')
        cls.prototype=Bot(hello)

    def setUp(self):
        _,self.obs=messages()
        self.bot=copy.copy(self.prototype)
        self.bot.world=fixture_world();self.bot.planner=Planner(self.bot.world)
        self.bot.tracker=EnemyTracker(self.bot.settings,self.bot.config)
        self.bot.loot={};self.bot.goal=None;self.bot.last_pos=None
        self.bot.exposed=[];self.bot.focus=None;self.bot.focus_id=None
        self.bot._goal_sight={};self.bot._tick_started=0
        self.bot.decision={'mode':'survive','target':None}
        self.obs.update(t=20,tick=400,alive=16,mechs=[],projectiles=[],pickups=[],crates=[],cubes=[],events=[],drops=[],offer=None)
        self.obs['self'].update(pos=[0,0,0],vel=[0,0,0],aim=[0,-.025],hp=1400,max_hp=1400,energy=100,cubes=0,grounded=True,in_zone=True)
        self.obs['self']['weapons'][0].update(ammo=12,cooldown=0,reload=0)
        self.obs['zone'].update(center=[0,0],next_center=[0,0],radius=150,next_radius=100,shrink_start=60,shrink_end=100)
        self.enemy={'id':7,'kind':'mech','pos':[100,0,0],'vel':[0,0,0],'aim':[math.pi,0],'hp':1400,'max_hp':1400,'cubes':0,'weapons':['gun']}

    def weapon(self,kind,**extra):
        return dict(kind=kind,cooldown=0,stats=copy.deepcopy(self.bot.config['weapons'][kind]),**extra)

    def test_visible_laser_beats_idle_cover(self):
        self.obs['pickups']=[{'id':8010,'kind':'laser','pos':[60,0,0]}]
        act=self.bot.act(self.obs)
        self.assertEqual(self.bot.goal[2],'weapon')
        self.assertGreater(act['move'][0],.5)

    def test_visible_cube_is_pursued(self):
        self.obs['cubes']=[{'id':5010,'pos':[35,0,0]}]
        self.bot.act(self.obs)
        self.assertEqual(self.bot.goal[2],'cube')

    def test_crate_is_shot_while_safe(self):
        self.obs['crates']=[{'id':1010,'pos':[50,0,0],'hp':300}]
        self.obs['self']['aim']=[0,math.atan2(self.bot.config['crates']['height']/2-7,50)]
        act=self.bot.act(self.obs)
        self.assertEqual(self.bot.focus_kind,'crate')
        self.assertTrue(act['fire'][0])

    def test_old_track_behind_cover_does_not_disable_farming(self):
        m=copy.deepcopy(self.enemy);m['pos']=[130,0,0]
        self.bot.world=fixture_world([dict(wall(),ring=[80,-60,86,-60,86,60,80,60])])
        self.bot.tracker.records[7]=(m,19)
        self.obs['crates']=[{'id':1010,'pos':[35,0,0],'hp':300}]
        self.bot.act(self.obs)
        self.assertEqual(self.bot.focus_kind,'crate')

    def test_low_health_does_not_reset_regen_on_distant_enemy(self):
        self.obs['self']['hp']=500;self.obs['mechs']=[self.enemy]
        act=self.bot.act(self.obs)
        self.assertTrue(self.bot.recovering)
        self.assertFalse(any(act['fire']))

    def test_recovery_has_hysteresis(self):
        self.obs['self']['hp']=600;self.bot.act(self.obs)
        self.obs['self']['hp']=1000;self.bot.act(self.obs)
        self.assertTrue(self.bot.recovering)
        self.obs['self']['hp']=1300;self.bot.act(self.obs)
        self.assertFalse(self.bot.recovering)

    def test_low_health_still_takes_a_safe_finish(self):
        self.obs['self']['hp']=500;self.enemy['hp']=40
        self.obs['mechs']=[self.enemy]
        self.bot.act(self.obs)
        self.assertEqual(self.bot.focus_id,7)
        self.assertEqual(self.bot.decision['mode'],'finish')

    def test_turns_before_firing(self):
        self.obs['mechs']=[self.enemy];self.obs['self']['aim']=[math.pi,0]
        self.assertFalse(any(self.bot.act(self.obs)['fire']))

    def test_enemy_behind_wall_cannot_be_shot(self):
        self.bot.world=fixture_world([wall()]);self.bot.planner=Planner(self.bot.world)
        self.obs['self']['pos']=[-50,0,0];self.enemy['pos']=[50,0,0]
        self.obs['mechs']=[self.enemy]
        self.assertFalse(any(self.bot.act(self.obs)['fire']))

    def test_focus_sticks_to_damaged_opponent(self):
        self.obs['mechs']=[self.enemy,dict(self.enemy,id=8,pos=[105,5,0],hp=1100)]
        self.bot.act(self.obs);self.assertEqual(self.bot.focus_id,8)
        self.obs['mechs'][0]['hp']=1090
        self.bot.act(self.obs);self.assertEqual(self.bot.focus_id,8)

    def test_no_premature_reload_with_one_remaining_round(self):
        self.obs['self']['weapons'][0]['ammo']=1
        self.obs['mechs']=[dict(self.enemy,hp=20,pos=[150,0,0])]
        self.assertFalse(self.bot.act(self.obs).get('reload',False))

    def test_empty_gun_still_reloads(self):
        self.obs['self']['weapons'][0]['ammo']=0
        self.obs['mechs']=[self.enemy]
        self.assertTrue(self.bot.act(self.obs).get('reload',False))

    def test_second_weapon_is_taken_explicitly(self):
        self.obs['pickups']=[{'id':8010,'kind':'laser','pos':[2,0,0]}]
        self.assertEqual(self.bot.act(self.obs).get('take'),8010)

    def test_mortar_is_not_picked_up_as_passive_liability(self):
        self.obs['pickups']=[{'id':8010,'kind':'mortar','pos':[2,0,0]}]
        self.assertNotIn('take',self.bot.act(self.obs))

    def test_laser_charges_and_releases(self):
        self.obs['mechs']=[self.enemy]
        self.obs['self']['weapons'][1]=self.weapon('laser',charge=0)
        self.assertTrue(self.bot.act(self.obs)['fire'][1])
        self.obs['self']['weapons'][1]['charge']=.75
        self.assertFalse(self.bot.act(self.obs)['fire'][1])

    def test_lightning_preserves_escape_energy(self):
        self.obs['self']['weapons'][1]=self.weapon('lightning')
        self.obs['self']['energy']=3;self.enemy['pos']=[30,0,0]
        self.obs['mechs']=[self.enemy]
        self.assertFalse(self.bot.act(self.obs)['fire'][1])

    def test_far_outside_zone_moves_toward_safety(self):
        self.obs['self']['pos']=[125,0,0];self.obs['self']['in_zone']=False
        self.obs['zone'].update(radius=70,next_radius=40)
        act=self.bot.act(self.obs)
        self.assertLess(act['move'][0],-.3)
        self.assertTrue(act['boost'])

    def test_forecast_does_not_shrink_while_holding(self):
        self.assertEqual(self.bot.zone_at(self.obs,15),(0,0,150))
        self.obs.update(t=70);self.obs['zone'].update(radius=125)
        self.assertAlmostEqual(self.bot.zone_at(self.obs,15)[2],112.5)

    def test_damage_and_dealt_damage_are_both_recorded(self):
        self.obs['events']=[{'type':'damaged','amount':25,'bearing':1.2},{'type':'hit','target':7,'amount':45}]
        self.bot.act(self.obs)
        self.assertEqual(self.bot.last_damage,20);self.assertEqual(self.bot.last_dealt,20)

    def test_actual_entrypoint_uses_v7_and_no_sarsa_update(self):
        self.assertEqual(self.bot.name,'Oval Sentinel v7 Hunter')
        self.bot.act(self.obs);self.bot.on_end({'place':14})
        self.assertFalse(self.bot.settings['enable_rl'])
        self.assertEqual(self.bot.learner.updates,0)


class PhysicsTests(unittest.TestCase):
    def setUp(self):
        self.hello,self.obs=messages();self.me=self.obs['self']
        self.me.update(pos=[0,0,0],vel=[0,0,0],grounded=True)

    def test_initial_acceleration_is_finite(self):
        path=rollout(self.me,(1,0),False,self.hello['config'])
        self.assertGreater(path[1][1],0)
        self.assertLess(path[1][1],self.me['stats']['walk_speed']*.1)

    def test_reversing_cannot_instantly_reverse_momentum(self):
        self.me['vel']=[15,0,0]
        path=rollout(self.me,(-1,0),False,self.hello['config'])
        self.assertGreater(path[1][1],0)

    def test_fast_bullet_is_detected_between_samples(self):
        path=rollout(self.me,(0,0),False,self.hello['config'])
        bullet={'kind':'gun','pos':[-10,0,4],'vel':[252,0,0]}
        self.assertGreater(projectile_cost(path,self.me,[bullet],self.hello['config']),40)

    def test_bullet_above_body_is_not_a_hit(self):
        path=rollout(self.me,(0,0),False,self.hello['config'])
        bullet={'kind':'gun','pos':[-10,0,40],'vel':[252,0,0]}
        self.assertEqual(projectile_cost(path,self.me,[bullet],self.hello['config']),0)

    def test_laser_charge_reduces_predicted_motion(self):
        a=rollout(self.me,(1,0),False,self.hello['config'])[-1][1]
        self.me['weapons'][1]={'kind':'laser','charge':.2}
        b=rollout(self.me,(1,0),False,self.hello['config'])[-1][1]
        self.assertLess(b,a)


if __name__=='__main__':unittest.main()
