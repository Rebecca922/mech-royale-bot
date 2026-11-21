"""Geometry and decision regression tests. This is not a match simulator."""
import array
import copy
import gzip
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

from bot import LegacyBot as Bot, intercept
from navigation import World, Planner

ROOT = Path(__file__).resolve().parent


def messages():
    with gzip.open(ROOT/'check/stream.jsonl.gz', 'rt') as f:
        hello = json.loads(next(f))
        obs = next(json.loads(line) for line in f if '"obs"' in line)
    return hello, obs


def fixture_world(prisms=()):
    hello, _ = messages()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)/'v2'
        p.mkdir()
        world = {'bounds': {'minX': -160, 'maxX': 160, 'minY': -160, 'maxY': 160},
                 'terrain': {'minX': -160, 'minY': -160, 'nx': 2, 'ny': 2, 'step': 320, 'scale': 0.01},
                 'prisms': list(prisms)}
        (p/'world.json').write_text(json.dumps(world))
        (p/'terrain.i16').write_bytes(array.array('h', [0, 0, 0, 0]).tobytes())
        return World(d, hello['config'])


def wall(base=0, top=25, holes=()):
    return {'kind': 'building', 'base': base, 'top': top,
            'ring': [-8, -25, 8, -25, 8, 25, -8, 25], 'holes': list(holes)}


class GeometryTests(unittest.TestCase):
    def test_wall_blocks_walk_and_shots(self):
        w = fixture_world([wall()])
        self.assertFalse(w.walk_segment((-30, 0, 0), (30, 0)))
        self.assertFalse(w.visible((-30, 0, 7), (30, 0, 7)))
        self.assertTrue(w.visible((-30, 0, 30), (30, 0, 30)))

    def test_bridge_can_be_walked_and_shot_under(self):
        w = fixture_world([wall(base=12, top=16)])
        self.assertTrue(w.walk_segment((-30, 0, 0), (30, 0)))
        self.assertTrue(w.visible((-30, 0, 7), (30, 0, 7)))
        self.assertFalse(w.visible((-30, 0, 14), (30, 0, 14)))

    def test_hole_is_free_but_walls_remain(self):
        w = fixture_world([wall(holes=[[-6, -20, 6, -20, 6, 20, -6, 20]])])
        self.assertTrue(w.clear(0, 0, 0, radius=2))
        self.assertFalse(w.clear(7, 0, 0, radius=2))
        self.assertTrue(w.visible((0, -10, 7), (0, 10, 7)))

    def test_ray_clipping_tiny_polygon_at_bucket_corner(self):
        p = {'kind': 'building', 'base': 0, 'top': 20,
             'ring': [31, 31, 33, 31, 33, 33, 31, 33], 'holes': []}
        w = fixture_world([p])
        self.assertFalse(w.visible((0, 0, 7), (100, 100, 7)))

    def test_path_goes_around_wall(self):
        w = fixture_world([wall()])
        p = Planner(w)
        p.start((-50, 0, 0), (50, 0), (0, 0, 150), [])
        for _ in range(100):
            p.advance()
            if not p.heap:
                break
        self.assertTrue(p.path)
        points = [w.point(k) for k in p.path]
        self.assertGreater(max(abs(y) for x, y in points), 27)
        for a, b in zip(points, points[1:]):
            self.assertTrue(w.walk_segment((*a, 0), b))

    def test_intercept_meets_moving_target(self):
        origin, target, velocity, speed = [0, 0, 7], [100, 0, 7], [0, 15, 0], 252
        point = intercept(origin, target, velocity, speed)
        flight = math.dist(origin, point)/speed
        self.assertAlmostEqual(point[1], velocity[1]*flight, places=7)


class DecisionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        h, _ = messages()
        h['map']['dir'] = str(ROOT/'arena')
        cls.prototype = Bot(h)

    def setUp(self):
        _, self.obs = messages()
        self.bot = copy.copy(self.prototype)
        self.bot.settings = dict(self.prototype.settings, enable_rl=False)
        self.bot.world = fixture_world()
        self.bot.planner = Planner(self.bot.world)
        self.bot.loot = {}
        self.bot.memory = {}
        from tactics import EnemyTracker
        self.bot.tracker = EnemyTracker(self.bot.settings, self.bot.config)
        self.bot.decision = {'mode': 'survive', 'target': None}
        self.obs['self']['pos'] = [0, 0, 0]
        self.obs['self']['aim'] = [0, -0.025]
        self.obs['self']['vel'] = [0, 0, 0]
        self.obs['t'] = 20
        self.obs['zone'].update(center=[0, 0], next_center=[0, 0], radius=150, next_radius=100)
        self.enemy = {'id': 7, 'kind': 'mech', 'pos': [100, 0, 0], 'vel': [0, 0, 0],
                      'hp': 1000, 'max_hp': 1400, 'weapons': ['gun'], 'aim': [math.pi, 0]}
        self.obs['mechs'] = [self.enemy]

    def weapon(self, kind, **extra):
        return {'kind': kind, 'cooldown': 0, 'stats': copy.deepcopy(self.bot.config['weapons'][kind]), **extra}

    def test_aligned_gun_fires(self):
        act = {'boost': False}
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertTrue(act['fire'][0])

    def test_turn_before_firing(self):
        self.obs['self']['aim'] = [math.pi, 0]
        act = {'boost': False}
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertFalse(any(act['fire']))

    def test_tracked_enemy_behind_wall_is_not_shot(self):
        self.bot.world = fixture_world([wall()])
        self.obs['self']['pos'] = [-50, 0, 0]
        self.enemy['pos'] = [50, 0, 0]
        act = {'boost': False}
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertFalse(any(act['fire']))

    def test_laser_charges_then_releases(self):
        self.obs['self']['weapons'][1] = self.weapon('laser', charge=0)
        act = {'boost': False}
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertTrue(act['fire'][1])
        self.obs['self']['weapons'][1]['charge'] = 0.75
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertFalse(act['fire'][1])

    def test_recovery_does_not_shoot_distant_enemy(self):
        self.obs['self']['hp'] = 500
        act = {'boost': False}
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertFalse(any(act['fire']))

    def test_lightning_preserves_energy_reserve(self):
        self.obs['self']['weapons'][1] = self.weapon('lightning')
        self.enemy['pos'] = [30, 0, 0]
        self.obs['self']['energy'] = 3
        act = {'boost': False}
        self.bot.combat(self.obs, act, [self.enemy])
        self.assertFalse(act['fire'][1])

    def test_weapon_pickup_requests_take(self):
        self.obs['mechs'] = []
        self.obs['pickups'] = [{'id': 8001, 'kind': 'lightning', 'pos': [2, 0, 0]}]
        self.assertEqual(self.bot.act(self.obs)['take'], 8001)

    def test_zone_forecast_is_current_during_hold(self):
        z = self.obs['zone']
        z.update(shrink_start=60, shrink_end=110, center=[0, 0], next_center=[20, 0], radius=150, next_radius=100)
        self.obs['t'] = 20
        self.assertEqual(self.bot.future_zone(self.obs), (0, 0, 150))
        self.obs['t'] = 55
        self.assertLess(self.bot.future_zone(self.obs)[2], 150)


if __name__ == '__main__':
    unittest.main()
