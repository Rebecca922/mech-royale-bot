"""Interpretable tactical estimates and uncertainty-aware enemy memory.

These are fixed heuristics, not learned probabilities or a trained model.
"""
import copy
import math


def distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def risk_at(x, y, enemy, reach=85):
    """Broaden stale tracks while reducing their peak cost. Not a probability."""
    uncertainty = enemy.get('_uncertainty', 0)
    width = reach+uncertainty
    confidence = enemy.get('_confidence', 1)
    d = math.hypot(x-enemy['pos'][0], y-enemy['pos'][1])
    return confidence*(reach/width)**2*max(0, 1-d/width)


class EnemyTracker:
    def __init__(self, settings, config):
        self.settings = settings
        self.speed = config['mech']['boost_speed']
        self.records = {}

    def update(self, obs):
        now = obs['t']
        # The server keeps reporting true coordinates for its own memory window.
        # Age starts only when those reports stop; don't extrapolate fresh reports.
        for m in obs['mechs']:
            if m['kind'] == 'mech':
                self.records[m['id']] = (copy.deepcopy(m), now)
        for event in obs['events']:
            if event['type'] == 'kill':
                self.records.pop(event['target'], None)
        tracks = []
        for identity, (record, when) in list(self.records.items()):
            age = max(0, now-when)
            if age > self.settings['memory_ttl_s']:
                del self.records[identity]
                continue
            m = copy.deepcopy(record)
            m['_age'] = age
            m['_confidence'] = math.exp(-age/self.settings['memory_decay_s'])
            m['_uncertainty'] = age*max(self.speed, math.hypot(*m.get('vel', [0, 0])[:2]))
            horizon = min(age, self.settings['prediction_cap_s'])
            velocity = m.get('vel', [0, 0, 0])
            m['pos'] = [record['pos'][0]+velocity[0]*horizon,
                        record['pos'][1]+velocity[1]*horizon, record['pos'][2]]
            tracks.append(m)
        return tracks


class Tactician:
    def __init__(self, config, settings):
        self.config = config
        self.settings = settings
        self.damage_model = None

    def cube_multiplier(self, cubes):
        c = self.config['cubes']
        return 1+c['damage_per_cube']*min(cubes, c['stat_cap'])

    def gun_kill_time(self, me, enemy):
        """Expected-shot estimate including aim, cooldown, ammo and reloads."""
        gun = next((w for w in me['weapons'] if w and w['kind'] == 'gun'), None)
        if gun is None:
            return float('inf')
        d = distance(me['pos'], enemy['pos'])
        stats = gun['stats']
        if d > stats['range']-3:
            return float('inf')
        # A fixed accuracy assumption; it must be calibrated with actual outcomes.
        accuracy = max(0.45, min(0.9, 0.98-d*0.002))
        damage = stats['damage']*self.cube_multiplier(me.get('cubes', 0))*accuracy
        shots = math.ceil(enemy.get('hp', enemy.get('max_hp', 1400))/max(1, damage))
        if shots > 200:
            return float('inf')
        yaw = math.atan2(enemy['pos'][1]-me['pos'][1], enemy['pos'][0]-me['pos'][0])
        error = abs((yaw-me['aim'][0]+math.pi)%math.tau-math.pi)
        enemy_height = self.config['mech']['height']*(1+self.config['cubes']['size_per_cube']*enemy.get('cubes', 0))
        dz = enemy['pos'][2]+enemy_height/2-me['pos'][2]-me['stats']['muzzle_height']
        pitch = math.atan2(dz, max(1, d))
        aim_delay = max(error/max(0.1, me['stats']['turn_rate']),
                        abs(pitch-me['aim'][1])/max(0.1, me['stats']['pitch_rate']))
        delay = aim_delay+gun['cooldown']+d/stats['speed']+0.1
        ammo = gun.get('ammo', stats['mag'])
        if gun.get('reload', 0) > 0:
            delay += gun['reload']
            ammo = stats['mag']
        missing = max(0, shots-ammo)
        reloads = math.ceil(missing/max(1, stats['mag']))
        return delay+max(0, shots-1)*stats['cooldown']+reloads*stats['reload']

    def incoming(self, obs, world, tracks):
        """Estimate incoming weapon DPS; unknown enemy upgrades remain unknown."""
        me = obs['self']
        center = [me['pos'][0], me['pos'][1], me['pos'][2]+me['stats']['height']/2]
        total = 0.0
        attackers = 0
        for enemy in tracks:
            d = distance(me['pos'], enemy['pos'])
            confidence = enemy.get('_confidence', 1)
            if confidence < 0.15 or d > 270:
                continue
            # Only fresh reports get a precise line-of-fire claim. Stale tracks
            # contribute a weak area risk, not a certain attacker count.
            if enemy.get('_age', 0) > 0:
                total += 24*risk_at(*me['pos'][:2], enemy, reach=150)
                continue
            scale = 1+self.config['cubes']['size_per_cube']*enemy.get('cubes', 0)
            origin = [enemy['pos'][0], enemy['pos'][1], enemy['pos'][2]+self.config['mech']['muzzle_height']*scale]
            if not world.visible(origin, center):
                continue
            bearing = math.atan2(me['pos'][1]-enemy['pos'][1], me['pos'][0]-enemy['pos'][0])
            error = abs((bearing-enemy['aim'][0]+math.pi)%math.tau-math.pi)
            facing = 1 if error < math.pi/3 else 0.4
            dps = 0.0
            for kind in enemy.get('weapons', ['gun']):
                s = self.config['weapons'][kind]
                if kind == 'gun' and d < s['range']:
                    cycle = s['mag']*s['cooldown']+s['reload']
                    dps += s['damage']*s['mag']/cycle*max(0.25, 0.7-d*0.0015)*facing
                elif kind == 'lightning' and d < s['range']+5:
                    dps += s['damage']/s['cooldown']*0.8*facing
                elif kind == 'laser' and d < s['range']:
                    dps += s['damage']/(s['charge']+s['cooldown'])*0.5*facing
                elif kind == 'mortar' and 15 < d < s['speed']**2/s['gravity']:
                    dps += s['splash']/s['cooldown']*0.35
            if dps > 8 and d < 180:
                attackers += 1
            total += dps*self.cube_multiplier(enemy.get('cubes', 0))
        total *= 1-max(0, min(0.8, me['stats'].get('armor', 0)))
        return total, attackers

    def choose(self, obs, world, tracks, future_zone, previous_target=None):
        me = obs['self']
        default = {'mode': 'survive', 'target': None, 'ttk': None, 'danger_s': None}
        calibrated = None
        if self.damage_model is not None:
            raw_damage, attackers = self.incoming(obs, world, tracks)
            self.damage_model.observe(obs, raw_damage)
            calibrated = self.damage_model.predict(raw_damage), attackers
        if not me['in_zone']:
            return default
        cx, cy, radius = future_zone
        # Never initiate a chase into the imminent zone edge.
        if radius-distance(me['pos'], (cx, cy)) < min(12, radius*0.15):
            return default
        damage, attackers = calibrated if calibrated is not None else self.incoming(obs, world, tracks)
        danger_time = me['hp']/max(1, damage)
        default['danger_s'] = danger_time
        origin = [*me['pos'][:2], me['pos'][2]+me['stats']['muzzle_height']]
        best = None
        for enemy in obs['mechs']:
            if enemy['kind'] != 'mech':
                continue
            size = 1+self.config['cubes']['size_per_cube']*enemy.get('cubes', 0)
            center = [*enemy['pos'][:2], enemy['pos'][2]+self.config['mech']['height']*size/2]
            if not world.visible(origin, center):
                continue
            ttk = self.gun_kill_time(me, enemy)
            if not math.isfinite(ttk):
                continue
            # Crossing the current weapon range or flying blindly around a wall
            # is deliberately not part of this first tactical extension.
            remain = max(0, obs['zone']['shrink_end']-obs['t'])
            zone_distance = max(0, distance(me['pos'], obs['zone']['next_center'])-obs['zone']['next_radius']+10)
            leave_time = zone_distance/max(1, me['stats']['walk_speed'])*1.5+3
            if zone_distance > 10 and ttk+leave_time > remain:
                continue
            hp_ratio = me['hp']/me['max_hp']
            mode = None
            if (ttk <= self.settings['finish_max_s'] and
                    danger_time > ttk*self.settings['finish_safety_ratio']+1 and hp_ratio > 0.16):
                mode = 'finish'
            elif (attackers <= 1 and hp_ratio >= self.settings['duel_min_hp'] and
                  enemy.get('hp', 1400) < me['hp']*1.15 and ttk < 22 and danger_time > ttk*1.1):
                mode = 'engage'
            if mode is None:
                continue
            score = (25 if mode == 'finish' else 0)-ttk*1.2
            if enemy['id'] == previous_target:
                score += 2
            preferred = 100
            if 'lightning' in enemy.get('weapons', []):
                preferred = 120
            if any(w and w['kind'] == 'lightning' for w in me['weapons']) and hp_ratio > 0.75 and attackers <= 1:
                preferred = 36 if 'mortar' not in enemy.get('weapons', []) else 90
            if best is None or score > best[0]:
                best = (score, {'mode': mode, 'target': enemy['id'], 'ttk': ttk,
                                'danger_s': danger_time, 'range': preferred})
        return best[1] if best else default

    def goal(self, obs, world, decision, side):
        target = next((m for m in obs['mechs'] if m['id'] == decision['target']), None)
        if target is None:
            return None
        me = obs['self']
        d = distance(me['pos'], target['pos'])
        # A nearly completed shot is not worth a new movement commitment.
        if decision['mode'] == 'finish' and decision['ttk'] < 0.45:
            return (*me['pos'][:2], 'engage')
        bearing = math.atan2(me['pos'][1]-target['pos'][1], me['pos'][0]-target['pos'][0])
        preferred = decision['range']
        # Do not abandon a short, safe finishing shot solely to obtain ideal range.
        if decision['mode'] == 'finish':
            preferred = max(25, min(preferred, d))
        radius = min(preferred, max(25, d+18))
        for offset in (side*0.09, -side*0.09, 0):
            x = target['pos'][0]+radius*math.cos(bearing+offset)
            y = target['pos'][1]+radius*math.sin(bearing+offset)
            if distance(me['pos'], (x, y)) > 45:
                continue
            if distance((x, y), obs['zone']['center']) > obs['zone']['radius']-5:
                continue
            # Keep combat movement on directly traversable local ground/roof.
            if world.walk_segment(me['pos'], (x, y), me['stats']['radius'], me['stats']['height']):
                return x, y, 'engage'
        return None
