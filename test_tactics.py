"""New behavior tests exercise uncertainty, tactical tradeoffs and feature switches."""
import copy
import math
import unittest

from bot import LegacyBot as Bot
from navigation import Planner
from tactics import EnemyTracker, Tactician, risk_at
from test_bot import ROOT, fixture_world, messages


class TrackerTests(unittest.TestCase):
    def setUp(self):
        import json
        self.hello, self.obs = messages()
        with open(ROOT/'strategy.json') as f:
            self.settings = json.load(f)
        self.tracker = EnemyTracker(self.settings, self.hello['config'])
        self.enemy = {'id': 8, 'kind': 'mech', 'pos': [0, 0, 0], 'vel': [10, 0, 0],
                      'aim': [0, 0], 'hp': 1000, 'weapons': ['gun']}
        self.obs.update(t=0, mechs=[self.enemy], events=[])

    def test_fresh_reports_are_not_extrapolated(self):
        self.tracker.update(self.obs)
        self.obs['t'] = 2
        self.obs['mechs'][0]['pos'] = [17, 0, 0]
        m = self.tracker.update(self.obs)[0]
        self.assertEqual(m['pos'], [17, 0, 0])
        self.assertEqual(m['_confidence'], 1)
        self.assertEqual(m['_uncertainty'], 0)

    def test_missing_track_loses_confidence_and_expands(self):
        fresh = self.tracker.update(self.obs)[0]
        self.obs.update(t=3, mechs=[])
        stale = self.tracker.update(self.obs)[0]
        self.assertLess(stale['_confidence'], fresh['_confidence'])
        self.assertGreater(stale['_uncertainty'], 0)
        self.assertLess(risk_at(0, 0, stale), risk_at(0, 0, fresh))
        self.assertGreater(risk_at(100, 0, stale), risk_at(100, 0, fresh))
        self.assertEqual(self.enemy['pos'], [0, 0, 0])

    def test_identity_keeps_separate_records(self):
        other = dict(self.enemy, id=9, pos=[80, 0, 0])
        self.obs['mechs'].append(other)
        self.assertEqual({m['id'] for m in self.tracker.update(self.obs)}, {8, 9})

    def test_ttl_and_confirmed_kills_remove_tracks(self):
        self.tracker.update(self.obs)
        self.obs.update(t=1, mechs=[], events=[{'type': 'kill', 'target': 8}])
        self.assertEqual(self.tracker.update(self.obs), [])
        self.obs.update(t=2, mechs=[self.enemy], events=[])
        self.tracker.update(self.obs)
        self.obs.update(t=20, mechs=[])
        self.assertEqual(self.tracker.update(self.obs), [])


class TacticalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        h, _ = messages()
        h['map']['dir'] = str(ROOT/'arena')
        cls.prototype = Bot(h)

    def setUp(self):
        _, self.obs = messages()
        self.bot = copy.copy(self.prototype)
        self.bot.settings = dict(self.prototype.settings)
        # Exercise features even when the submitted configuration disables one
        # for an ablation. Individual switch tests override these explicitly.
        self.bot.settings.update(enable_tactics=True, enable_memory=True, enable_rl=False)
        self.bot.world = fixture_world()
        self.bot.planner = Planner(self.bot.world)
        self.bot.tracker = EnemyTracker(self.bot.settings, self.bot.config)
        self.bot.tactician = Tactician(self.bot.config, self.bot.settings)
        self.bot.decision = {'mode': 'survive', 'target': None}
        self.bot.loot = {}
        self.bot.memory = {}
        self.obs['self'].update(pos=[-50, 0, 0], aim=[0, -0.025], vel=[0, 0, 0])
        self.obs.update(t=20, events=[], mechs=[], pickups=[], crates=[], cubes=[])
        self.obs['zone'].update(center=[0, 0], next_center=[0, 0], radius=150, next_radius=100)
        self.enemy = {'id': 7, 'kind': 'mech', 'pos': [50, 0, 0], 'vel': [0, 0, 0],
                      'hp': 40, 'max_hp': 1400, 'weapons': ['gun'], 'aim': [math.pi, 0]}
        self.obs['mechs'] = [self.enemy]

    def choose(self):
        return self.bot.tactician.choose(self.obs, self.bot.world, self.obs['mechs'],
                                         self.bot.future_zone(self.obs))

    def test_safe_last_shot_is_finished(self):
        self.obs['self']['hp'] = 500
        self.assertEqual(self.choose()['mode'], 'finish')

    def test_lethal_crossfire_is_not_chased(self):
        self.obs['self']['hp'] = 250
        self.enemy['hp'] = 120
        self.enemy['pos'] = [-15, 0, 0]
        self.enemy['weapons'] = ['gun', 'lightning']
        for i in range(3):
            self.obs['mechs'].append(dict(self.enemy, id=10+i, hp=1400, pos=[-20, 4*i, 0]))
        self.assertEqual(self.choose()['mode'], 'survive')

    def test_zone_escape_overrides_finish(self):
        self.obs['self']['in_zone'] = False
        self.assertEqual(self.choose()['mode'], 'survive')

    def test_walled_target_is_not_a_finishing_opportunity(self):
        from test_bot import wall
        self.bot.world = fixture_world([wall()])
        self.assertIsNone(self.choose()['target'])

    def test_empty_magazine_increases_kill_time(self):
        me = self.obs['self']
        loaded = self.bot.tactician.gun_kill_time(me, self.enemy)
        me['weapons'][0]['ammo'] = 0
        empty = self.bot.tactician.gun_kill_time(me, self.enemy)
        self.assertGreaterEqual(empty-loaded, 2.39)

    def test_finishing_overrides_distant_low_health_ceasefire(self):
        self.obs['self']['hp'] = 500
        act = self.bot.act(self.obs)
        self.assertEqual(self.bot.decision['target'], 7)
        self.assertTrue(act['fire'][0])

    def test_stale_memory_is_not_used_as_a_shooting_target(self):
        self.bot.act(self.obs)
        self.obs.update(t=21, tick=420, mechs=[])
        act = self.bot.act(self.obs)
        self.assertFalse(any(act['fire']))
        self.assertIsNone(self.bot.decision['target'])

    def test_gun_and_lightning_fire_together_if_both_aligned(self):
        self.enemy['pos'] = [-20, 0, 0]
        self.obs['self']['aim'] = [0, math.atan2(-2.5, 30)]
        self.obs['self']['weapons'][1] = {'kind': 'lightning', 'cooldown': 0,
            'stats': copy.deepcopy(self.bot.config['weapons']['lightning'])}
        act = self.bot.act(self.obs)
        self.assertEqual(act['fire'], [True, True])

    def test_tactics_switch_disables_finishing_override(self):
        self.bot.settings['enable_tactics'] = False
        self.obs['self']['hp'] = 500
        act = self.bot.act(self.obs)
        self.assertFalse(any(act['fire']))
        self.assertIsNone(self.bot.decision['target'])


if __name__ == '__main__':
    unittest.main()
