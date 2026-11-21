"""Reproduce the finishing/reload conflict and cover the intentionally narrow fix."""
import copy
import math
import unittest

from bot import Bot
from online_rl import OnlineSarsa
from test_bot import ROOT, fixture_world, messages


class FinishingReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hello, _ = messages()
        hello['map']['dir'] = str(ROOT/'arena')
        cls.prototype = Bot(hello)

    def setUp(self):
        _, self.obs = messages()
        self.bot = copy.copy(self.prototype)
        self.bot.settings = dict(self.prototype.settings)
        self.bot.world = fixture_world()
        self.bot.learner = OnlineSarsa(self.bot.settings, 123)
        self.obs['self'].update(pos=[-50, 0, 0], aim=[0, math.atan2(-2.5, 200)], hp=1400, cubes=0)
        self.obs['self']['weapons'][0].update(ammo=1, reload=0, cooldown=0)
        self.enemy = {'id': 7, 'kind': 'mech', 'pos': [150, 0, 0], 'vel': [0, 0, 0],
                      'hp': 20, 'max_hp': 1400, 'weapons': ['gun'], 'aim': [math.pi, 0]}
        self.obs.update(t=20, mechs=[self.enemy], events=[], crates=[], pickups=[], cubes=[])
        self.bot.decision = {'mode': 'finish', 'target': 7, 'ttk': 1, 'danger_s': 30, 'range': 100}

    def action(self):
        act = {'boost': False}
        self.bot.combat(self.obs, act, self.obs['mechs'])
        return act

    def test_one_round_finisher_keeps_shooting_without_early_reload(self):
        act = self.action()
        self.assertTrue(act['fire'][0])
        self.assertFalse(act.get('reload', False))

    def test_multiple_remaining_rounds_can_finish(self):
        self.obs['self']['weapons'][0]['ammo'] = 2
        self.enemy['hp'] = 40
        act = self.action()
        self.assertTrue(act['fire'][0])
        self.assertFalse(act.get('reload', False))

    def test_not_enough_damage_keeps_original_reload_rule(self):
        self.enemy['hp'] = 200
        self.assertTrue(self.action().get('reload', False))

    def test_empty_magazine_still_requests_reload(self):
        self.obs['self']['weapons'][0]['ammo'] = 0
        self.assertTrue(self.action().get('reload', False))

    def test_non_finishing_engagement_keeps_original_behavior(self):
        self.bot.decision['mode'] = 'engage'
        self.assertTrue(self.action().get('reload', False))

    def test_uncommitted_target_keeps_original_behavior(self):
        self.bot.decision = {'mode': 'survive', 'target': None}
        self.assertTrue(self.action().get('reload', False))

    def test_cube_bonus_is_counted_with_the_server_cap(self):
        me = self.obs['self']
        gun = me['weapons'][0]
        self.enemy['hp'] = 55
        self.assertFalse(self.bot.magazine_can_finish(me, self.enemy, gun))
        me['cubes'] = 6
        self.assertTrue(self.bot.magazine_can_finish(me, self.enemy, gun))
        me['cubes'] = 100
        self.enemy['hp'] = 64
        self.assertFalse(self.bot.magazine_can_finish(me, self.enemy, gun))

    def test_upgraded_weapon_damage_is_used(self):
        me = self.obs['self']
        gun = me['weapons'][0]
        gun['stats']['damage'] = 60
        self.enemy['hp'] = 50
        self.assertTrue(self.bot.magazine_can_finish(me, self.enemy, gun))

    def test_unknown_enemy_hp_does_not_authorize_preserving_ammo(self):
        del self.enemy['hp']
        self.assertFalse(self.bot.magazine_can_finish(self.obs['self'], self.enemy, self.obs['self']['weapons'][0]))

    def test_already_reloading_gun_is_not_assumed_ready(self):
        self.obs['self']['weapons'][0]['reload'] = 1.2
        act = self.action()
        self.assertFalse(act['fire'][0])
        self.assertTrue(act.get('reload', False))

    def test_no_target_keeps_idle_top_up(self):
        self.obs['mechs'] = []
        self.assertTrue(self.action().get('reload', False))


if __name__ == '__main__':
    unittest.main()
