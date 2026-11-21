"""Supervised-label, chronological evaluation and tactical integration tests.

Hand-constructed transitions test the code, not mech-game performance.
"""
import copy
import json
import unittest

from bot import Bot
from damage_model import DamageCalibrator
from navigation import Planner
from online_rl import OnlineSarsa
from tactics import EnemyTracker, Tactician
from test_bot import ROOT, fixture_world, messages


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        with open(ROOT/'strategy.json') as f:
            self.settings = json.load(f)
        _, self.obs = messages()
        self.obs.update(t=0, events=[])
        self.model = DamageCalibrator(self.settings)
        self.model.observe(self.obs, 100)
        self.frame = 0

    def feed(self, seconds=1, base=100, actual=50, events=None):
        for _ in range(round(seconds*10)):
            self.frame += 1
            self.obs['t'] = round(self.frame/10, 6)
            self.obs['events'] = events if events is not None else [
                {'type': 'damaged', 'amount': actual/10, 'weapon': 'gun', 'by': 7}]
            self.model.observe(self.obs, base)

    def test_manual_prior_is_used_until_enough_samples(self):
        self.feed(seconds=4)
        self.assertEqual(self.model.samples, 4)
        self.assertEqual(self.model.predict(100), 100)
        self.feed(seconds=1)
        self.assertLess(self.model.predict(100), 100)

    def test_closed_form_fit_includes_prior(self):
        self.settings['sl_min_samples'] = 1
        self.feed(seconds=1, actual=50)
        self.assertAlmostEqual(self.model.factor(), (4+0.5)/(4+1))

    def test_labels_exclude_zone_npc_self_and_unknown_sources(self):
        own_id = self.obs['self']['id']
        events = [{'type': 'damaged', 'amount': 10, 'weapon': 'gun', 'by': 7},
                  {'type': 'damaged', 'amount': 50, 'weapon': 'zone', 'by': None},
                  {'type': 'damaged', 'amount': 50, 'weapon': 'npc', 'by': 105},
                  {'type': 'damaged', 'amount': 50, 'weapon': 'mortar', 'by': own_id},
                  {'type': 'damaged', 'amount': 50, 'weapon': 'gun', 'by': None}]
        self.feed(seconds=1, events=events)
        self.assertAlmostEqual(self.model.last_sample['actual_dps'], 100)

    def test_prediction_is_scored_before_current_label_is_fitted(self):
        self.settings['sl_min_samples'] = 1
        self.feed(seconds=1, actual=200)
        self.assertAlmostEqual(self.model.last_sample['predicted_dps'], 100)
        self.assertAlmostEqual(self.model.summary()['model_prequential_mse'], 10000)
        self.assertAlmostEqual(self.model.factor(), 1.2)
        self.feed(seconds=1, actual=200)
        self.assertAlmostEqual(self.model.last_sample['predicted_dps'], 120)

    def test_learned_values_remain_bounded(self):
        self.feed(seconds=10, actual=1000)
        self.assertEqual(self.model.factor(), self.settings['sl_factor_max'])
        self.model = DamageCalibrator(self.settings)
        self.frame = 0
        self.obs.update(t=0, events=[])
        self.model.observe(self.obs, 100)
        self.feed(seconds=10, actual=0)
        self.assertEqual(self.model.factor(), self.settings['sl_factor_min'])

    def test_low_exposure_is_not_used_to_fit_a_scale(self):
        self.model = DamageCalibrator(self.settings)
        self.model.observe(self.obs, 0)
        self.feed(seconds=3, base=0, actual=50)
        self.assertEqual(self.model.samples, 0)
        self.assertEqual(self.model.predict(0), 0)

    def test_missing_observations_discard_incomplete_window(self):
        self.feed(seconds=0.5)
        self.obs.update(t=2, events=[])
        self.model.observe(self.obs, 100)
        self.assertEqual(self.model.gaps, 1)
        self.assertEqual(self.model.samples, 0)
        self.assertEqual(self.model.window_t, 0)

    def test_terminal_window_is_not_given_fabricated_damage(self):
        self.feed(seconds=0.5)
        summary = self.model.finish()
        self.assertEqual(summary['samples'], 0)
        self.assertTrue(self.model.closed)
        self.feed(seconds=1)
        self.assertEqual(self.model.samples, 0)

    def test_new_match_resets_learned_statistics(self):
        self.feed(seconds=5)
        self.assertGreater(self.model.samples, 0)
        new = DamageCalibrator(self.settings)
        self.assertEqual(new.samples, 0)
        self.assertEqual(new.factor(), 1)

    def test_invalid_config_is_rejected(self):
        for key, value in [('sl_min_samples', 0), ('sl_prior_strength', 0),
                           ('sl_factor_min', 2), ('sl_factor_max', 0.5),
                           ('enable_sl', 'yes'), ('sl_window_s', float('nan'))]:
            s = dict(self.settings)
            s[key] = value
            with self.assertRaises(ValueError):
                DamageCalibrator(s)


class SupervisedIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hello, _ = messages()
        hello['map']['dir'] = str(ROOT/'arena')
        cls.prototype = Bot(hello)

    def setUp(self):
        _, self.obs = messages()
        self.bot = copy.copy(self.prototype)
        self.bot.settings = dict(self.prototype.settings, rl_epsilon=0)
        self.bot.world = fixture_world()
        self.bot.planner = Planner(self.bot.world)
        self.bot.tracker = EnemyTracker(self.bot.settings, self.bot.config)
        self.bot.tactician = Tactician(self.bot.config, self.bot.settings)
        self.bot.damage_model = DamageCalibrator(self.bot.settings)
        self.bot.tactician.damage_model = self.bot.damage_model
        self.bot.learner = OnlineSarsa(self.bot.settings, 123)
        self.bot.decision = {'mode': 'survive', 'target': None}
        self.bot.loot, self.bot.memory = {}, {}
        self.bot.goal, self.bot.last_pos = None, None
        self.obs['self'].update(pos=[-50, 0, 0], aim=[0, -0.025], vel=[0, 0, 0], hp=250)
        self.obs.update(t=0, alive=16, events=[], mechs=[], pickups=[], crates=[], cubes=[])
        self.obs['zone'].update(center=[0, 0], next_center=[0, 0], radius=150, next_radius=100)
        self.enemy = {'id': 7, 'kind': 'mech', 'pos': [50, 0, 0], 'vel': [0, 0, 0],
                      'hp': 80, 'max_hp': 1400, 'weapons': ['gun'], 'aim': [3.14, 0]}
        self.obs['mechs'] = [self.enemy]

    def test_calibration_can_change_finish_safety_decision(self):
        controller = self.bot.tactician
        controller.incoming = lambda *args: (80, 1)
        initial = controller.choose(self.obs, self.bot.world, [self.enemy], self.bot.future_zone(self.obs))
        self.assertEqual(initial['mode'], 'finish')
        self.bot.damage_model.samples = 10
        self.bot.damage_model.sum_xx = 10
        self.bot.damage_model.sum_xy = 30
        self.obs['t'] = 0.1
        changed = controller.choose(self.obs, self.bot.world, [self.enemy], self.bot.future_zone(self.obs))
        self.assertIsNone(changed['target'])

    def test_match_end_closes_model_and_rl_remains_frozen(self):
        self.bot.act(self.obs)
        self.bot.on_end({'type': 'end', 'place': 14, 'kills': 0})
        self.assertTrue(self.bot.damage_model.closed)
        self.assertIsNotNone(self.bot.sl_summary)
        self.assertEqual(self.bot.learner.updates, 0)

    def test_zone_safety_still_overrides_attack(self):
        self.obs['self']['in_zone'] = False
        self.bot.act(self.obs)
        self.assertIsNone(self.bot.decision['target'])


if __name__ == '__main__':
    unittest.main()
