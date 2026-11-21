"""Algorithm, reward accounting, safety masking and integration tests.

Hand-built transitions here verify implementation; they are not a game
simulator, game training data or evidence of an improved tournament score.
"""
import copy
import json
import math
import subprocess
import sys
import unittest

from bot import LegacyBot as Bot
from navigation import Planner
from online_rl import OnlineSarsa
from tactics import EnemyTracker, Tactician
from test_bot import ROOT, fixture_world, messages


class SarsaTests(unittest.TestCase):
    def setUp(self):
        with open(ROOT/'strategy.json') as f:
            self.settings = json.load(f)
        self.settings.update(enable_rl=True, rl_updates=True)
        self.settings['rl_epsilon'] = 0
        _, self.obs = messages()
        self.obs.update(t=0, alive=16, mechs=[])
        self.proposal = {'target': 7, 'mode': 'engage', 'ttk': 2, 'danger_s': 20, 'range': 100}
        self.learner = OnlineSarsa(self.settings, seed=123)

    def test_initial_policy_prefers_v3(self):
        self.assertEqual(self.learner.step(self.obs, self.proposal), OnlineSarsa.ACCEPT)

    def test_sarsa_bootstraps_selected_action_not_maximum(self):
        self.settings['rl_alpha'] = 1
        learner = OnlineSarsa(self.settings, 123)
        a, b = (1, 1, 1, 0, 0), (1, 1, 1, 1, 0)
        learner.q[b] = [10, -2]
        learner.update(a, 0, 1, b, 1)
        self.assertEqual(learner.q[a][0], -1)

    def test_actual_feedback_changes_value_and_later_choice(self):
        self.learner.step(self.obs, self.proposal)
        self.obs['t'] = 1
        self.obs['self']['hp'] -= 300
        self.learner.step(self.obs, self.proposal)
        self.assertGreater(self.learner.updates, 0)
        self.obs['t'] = 2
        choice = self.learner.step(self.obs, self.proposal)
        self.assertEqual(choice, OnlineSarsa.CAUTIOUS)

    def test_low_level_observations_do_not_resample_every_tick(self):
        self.learner.step(self.obs, self.proposal)
        self.obs['t'] = 0.1
        self.learner.step(self.obs, self.proposal)
        self.assertEqual(self.learner.selections, 1)
        self.assertEqual(self.learner.updates, 0)

    def test_immediate_safety_mask_overrides_old_choice(self):
        state, _ = self.learner.context(self.obs, self.proposal)
        self.learner.q[state] = [-1, 1]
        self.assertEqual(self.learner.step(self.obs, self.proposal), 1)
        self.obs['t'] = 0.1
        choice = self.learner.step(self.obs, {'mode': 'survive', 'target': None})
        self.assertEqual(choice, OnlineSarsa.ACCEPT)
        self.assertEqual(self.learner.allowed, (OnlineSarsa.ACCEPT,))

    def test_exploration_never_creates_unapproved_pursuit(self):
        self.settings['rl_epsilon'] = 0.1
        learner = OnlineSarsa(self.settings, 123)
        for i in range(30):
            self.obs['t'] = i
            self.assertEqual(learner.step(self.obs, {'mode': 'survive', 'target': None}), 0)
        self.assertEqual(learner.explorations, 0)

    def test_terminal_reward_matches_official_score_with_ties(self):
        self.learner.step(self.obs, self.proposal)
        self.obs.update(t=1, alive=14)
        self.learner.step(self.obs, self.proposal)
        result = self.learner.finish(place=14)
        self.assertAlmostEqual(result['shaped_return'], 2-result['initial_potential'])

    def test_time_limit_hp_ranking_is_corrected(self):
        self.learner.step(self.obs, self.proposal)
        self.obs.update(t=1, alive=8)
        self.learner.step(self.obs, self.proposal)
        result = self.learner.finish(place=3)
        self.assertAlmostEqual(result['shaped_return'], 13-result['initial_potential'])

    def test_progress_before_first_observation_is_not_lost(self):
        self.obs['alive'] = 12
        self.learner.step(self.obs, self.proposal)
        result = self.learner.finish(place=12)
        self.assertAlmostEqual(result['shaped_return'], 4-result['initial_potential'])

    def test_kill_event_does_not_double_reward_a_shared_placement(self):
        self.learner.step(self.obs, self.proposal)
        self.obs.update(t=1, events=[{'type': 'kill', 'target': 7}])
        self.learner.step(self.obs, self.proposal)
        result = self.learner.finish(place=16)
        self.assertAlmostEqual(result['shaped_return'], -result['initial_potential'])

    def test_potential_cannot_generate_a_positive_reward_cycle(self):
        self.learner.step(self.obs, self.proposal)
        original_hp = self.obs['self']['hp']
        self.obs['t'] = 1
        self.obs['self']['hp'] -= 100
        self.learner.step(self.obs, self.proposal)
        self.obs['t'] = 2
        self.obs['self']['hp'] = original_hp
        self.learner.step(self.obs, self.proposal)
        self.assertAlmostEqual(self.learner.shaped_return, 0)

    def test_frozen_ablation_does_not_update_values(self):
        self.settings['rl_updates'] = False
        learner = OnlineSarsa(self.settings, 123)
        learner.step(self.obs, self.proposal)
        old = copy.deepcopy(learner.q)
        self.obs['t'] = 1
        self.obs['self']['hp'] -= 100
        learner.step(self.obs, self.proposal)
        learner.finish(place=14)
        self.assertEqual(learner.q, old)
        self.assertEqual(learner.updates, 0)

    def test_terminal_processing_is_idempotent(self):
        self.learner.step(self.obs, self.proposal)
        a = self.learner.finish(12)
        b = self.learner.finish(12)
        self.assertEqual(a, b)

    def test_new_match_starts_from_prior(self):
        self.learner.step(self.obs, self.proposal)
        self.learner.finish(12)
        new = OnlineSarsa(self.settings, 123)
        self.assertEqual(new.q, {})
        self.assertEqual(new.step(self.obs, self.proposal), 0)

    def test_seeded_selection_is_reproducible(self):
        self.settings['rl_epsilon'] = 0.1
        a, b = OnlineSarsa(self.settings, 456), OnlineSarsa(self.settings, 456)
        state = (1, 1, 1, 1, 1)
        self.assertEqual([a.select(state, (0, 1), True) for _ in range(100)],
                         [b.select(state, (0, 1), True) for _ in range(100)])

    def test_invalid_learning_parameters_are_rejected(self):
        for key, value in (('rl_alpha', 0), ('rl_epsilon', 0.5), ('rl_interval_s', 0),
                           ('rl_rule_prior', float('nan')), ('rl_updates', 'yes')):
            s = dict(self.settings)
            s[key] = value
            with self.assertRaises(ValueError):
                OnlineSarsa(s, 123)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        h, _ = messages()
        h['map']['dir'] = str(ROOT/'arena')
        cls.prototype = Bot(h)

    def setUp(self):
        _, self.obs = messages()
        self.bot = copy.copy(self.prototype)
        self.bot.settings = dict(self.prototype.settings, rl_epsilon=0, enable_rl=True, rl_updates=True)
        self.bot.world = fixture_world()
        self.bot.planner = Planner(self.bot.world)
        self.bot.tracker = EnemyTracker(self.bot.settings, self.bot.config)
        self.bot.tactician = Tactician(self.bot.config, self.bot.settings)
        self.bot.learner = OnlineSarsa(self.bot.settings, 123)
        self.bot.decision = {'mode': 'survive', 'target': None}
        self.bot.loot = {}
        self.bot.memory = {}
        self.bot.goal = None
        self.bot.last_pos = None
        self.obs['self'].update(pos=[-50, 0, 0], aim=[0, -0.025], vel=[0, 0, 0])
        self.obs.update(t=0, alive=16, events=[], mechs=[], pickups=[], crates=[], cubes=[])
        self.obs['zone'].update(center=[0, 0], next_center=[0, 0], radius=150, next_radius=100)
        self.enemy = {'id': 7, 'kind': 'mech', 'pos': [50, 0, 0], 'vel': [0, 0, 0],
                      'hp': 40, 'max_hp': 1400, 'weapons': ['gun'], 'aim': [math.pi, 0]}
        self.obs['mechs'] = [self.enemy]

    def test_learner_can_decline_tactical_proposal(self):
        proposal = self.bot.tactician.choose(self.obs, self.bot.world, [self.enemy], self.bot.future_zone(self.obs))
        self.assertEqual(proposal['mode'], 'finish')
        state, _ = self.bot.learner.context(self.obs, proposal)
        self.bot.learner.q[state] = [-1, 1]
        act = self.bot.act(self.obs)
        self.assertIsNone(self.bot.decision['target'])
        self.assertIn('move', act)

    def test_terminal_callback_updates_final_transition(self):
        self.bot.act(self.obs)
        self.bot.on_end({'type': 'end', 'place': 14, 'kills': 0})
        self.assertTrue(self.bot.learner.finished)
        self.assertGreater(self.bot.rl_summary['updates'], 0)
        self.assertEqual(self.bot.rl_summary['score'], 2)

    def test_disabled_rl_does_not_collect_or_learn(self):
        self.bot.settings['enable_rl'] = False
        self.bot.act(self.obs)
        self.bot.on_end({'type': 'end', 'place': 14, 'kills': 0})
        self.assertEqual(self.bot.learner.q, {})
        self.assertIsNone(self.bot.rl_summary)

    def test_rule_safety_rejection_cannot_be_overridden(self):
        self.obs['self']['in_zone'] = False
        self.bot.act(self.obs)
        self.assertIsNone(self.bot.decision['target'])
        self.assertEqual(self.bot.learner.allowed, (0,))

    def test_wrapper_delivers_terminal_message_without_extra_stdout(self):
        hello, _ = messages()
        code = ('import sys\nsys.path.insert(0, '+repr(str(ROOT))+')\n'
                'import mechfight as mf\n'
                'class Probe:\n'
                ' name = "terminal-probe"\n'
                ' def __init__(self, hello): pass\n'
                ' def act(self, obs): return {}\n'
                ' def on_end(self, msg): print("end-place="+str(msg["place"]), file=sys.stderr)\n'
                'mf.run(Probe)\n')
        stream = json.dumps(hello)+'\n'+json.dumps({'type': 'end', 'place': 14, 'kills': 0})+'\n'
        p = subprocess.run([sys.executable, '-B', '-u', '-c', code], input=stream,
                           capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('end-place=14', p.stderr)
        self.assertEqual([json.loads(s) for s in p.stdout.splitlines()],
                         [{'type': 'ready', 'name': 'terminal-probe'}])


if __name__ == '__main__':
    unittest.main()
