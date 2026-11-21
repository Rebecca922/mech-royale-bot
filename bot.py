"""Campus survival bot: cover, zone timing, resource collection and aimed fire."""
import math
import json
import os
import random
import time

import mechfight as mf
from navigation import World, Planner
from tactics import EnemyTracker, Tactician, risk_at
from online_rl import OnlineSarsa


def clamp(v, a, b):
    return max(a, min(b, v))


def angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def intercept(origin, target, velocity, speed):
    """Constant-velocity intercept without assuming projectile inherits shooter velocity."""
    r = [target[i]-origin[i] for i in range(3)]
    a = sum(v*v for v in velocity)-speed*speed
    b = 2*sum(r[i]*velocity[i] for i in range(3))
    c = sum(v*v for v in r)
    roots = []
    if abs(a) < 1e-8:
        if abs(b) > 1e-8:
            roots = [-c/b]
    else:
        d = b*b-4*a*c
        if d >= 0:
            roots = [(-b-math.sqrt(d))/(2*a), (-b+math.sqrt(d))/(2*a)]
    t = min((v for v in roots if v > 0), default=math.sqrt(c)/max(1, speed))
    return [target[i]+velocity[i]*clamp(t, 0, 2.5) for i in range(3)]


class LegacyBot:
    name = 'Oval Sentinel v4 RL'

    def __init__(self, hello):
        self.config = hello['config']
        with open(os.path.join(os.path.dirname(__file__), 'strategy.json')) as f:
            self.settings = json.load(f)
        for key in ('enable_tactics', 'enable_memory'):
            if not isinstance(self.settings[key], bool):
                raise ValueError(f'{key} must be a JSON boolean')
        for key in ('memory_ttl_s', 'memory_decay_s', 'prediction_cap_s',
                    'finish_max_s', 'finish_safety_ratio', 'duel_min_hp'):
            if not isinstance(self.settings[key], (int, float)) or not math.isfinite(self.settings[key]) or self.settings[key] <= 0:
                raise ValueError(f'{key} must be a positive finite number')
        self.tracker = EnemyTracker(self.settings, self.config)
        self.tactician = Tactician(self.config, self.settings)
        self.decision = {'mode': 'survive', 'target': None}
        self.learner = OnlineSarsa(self.settings, hello['seed'], int(self.config['match']['mechs']))
        self.rl_summary = None
        self.orbit_side = 1 if hello['slot'] % 2 else -1
        self.rng = random.Random(hello['seed'])
        directory = hello['map']['dir']
        if not os.path.isfile(os.path.join(directory, 'v2/world.json')):
            # Local replay tools sometimes pass a relative map path.
            directory = os.path.join(os.path.dirname(__file__), 'arena')
        self.world = World(directory, self.config)
        self.planner = Planner(self.world)
        self.goal = None
        self.goal_time = -100
        self.last_plan = -100
        self.last_damage = -100
        self.last_pos = None
        self.last_t = 0
        self.stuck = 0
        self.escape_until = -100
        self.escape_angle = 0
        self.scan = self.rng.uniform(-math.pi, math.pi)
        self.move_angle = self.scan
        self.last_move = 0
        self.memory = {}
        self.loot = {}
        self.drop_wait = False
        self.upgrades = {u['id']: u for u in self.config['upgrades']}

    def pick_upgrade(self, offer, me):
        priorities = {'nanites': 100, 'servos': 93, 'armor': 88, 'plating': 86,
                      'jets': 80, 'reactor': 76, 'gun_rate': 63, 'gun_damage': 61,
                      'gun_mag': 65, 'gun_velocity': 60, 'capacitor': 57,
                      'lightning_range': 72, 'lightning_efficiency': 64,
                      'laser_charge': 64, 'laser_damage': 60, 'gyros': 52,
                      'sensors': 45, 'mortar_splash': 44, 'mortar_reload': 42,
                      'mortar_velocity': 40, 'lightning_chain': 43, 'laser_pierce': 38}
        def score(choice):
            value = priorities.get(choice['id'], 35)
            if choice['id'] == 'plating' and me['hp']/me['max_hp'] < 0.45:
                value += 20
            if choice['id'] == 'nanites' and me['hp']/me['max_hp'] < 0.8:
                value += 10
            return value
        return max(range(len(offer['choices'])), key=lambda i: score(offer['choices'][i]))

    def future_zone(self, obs):
        z = obs['zone']
        t = obs['t']
        # Predict only the next ~18 seconds. obs.center/radius are already current.
        remain = z['shrink_end'] - max(t, z['shrink_start'])
        progress = clamp((t+18-max(t, z['shrink_start']))/max(1, remain), 0, 1)
        center = [z['center'][i]*(1-progress)+z['next_center'][i]*progress for i in range(2)]
        radius = z['radius']*(1-progress)+z['next_radius']*progress
        return center[0], center[1], radius

    def choose_goal(self, obs, threats):
        me = obs['self']
        pos = me['pos']
        cx, cy, radius = self.future_zone(obs)
        hp = me['hp']/me['max_hp']
        candidates = [(pos[0], pos[1], 'cover', 0)]
        if self.goal:
            candidates.append((*self.goal[:2], 'cover', 0))
        # Cover candidates both nearby and in the upcoming circle.
        for r in (22, 50, 90):
            for i in range(12):
                a = i*math.tau/12
                candidates.append((pos[0]+r*math.cos(a), pos[1]+r*math.sin(a), 'cover', 0))
        for r in (0, radius*0.5, radius*0.78):
            for i in range(1 if r == 0 else 12):
                a = i*math.tau/12
                candidates.append((cx+r*math.cos(a), cy+r*math.sin(a), 'cover', 0))
        held = {w['kind'] for w in me['weapons'] if w}
        for item, expiry, kind in list(self.loot.values()):
            if expiry < obs['t'] or abs(item['pos'][2]-self.world.height(*item['pos'][:2])) > 3:
                continue
            d = distance(pos, item['pos'])
            if d > 150:
                continue
            if kind == 'cube':
                value = 64
            elif kind == 'weapon':
                if item['kind'] in held or (len(held) >= 2 and not
                        (item['kind'] == 'lightning' and 'mortar' in held)):
                    continue
                value = {'lightning': 52, 'laser': 38, 'mortar': 20, 'gun': 0}[item['kind']]
            else:
                value = 28 if d < 85 else 14
            candidates.append((*item['pos'][:2], kind, value))
        self.drop_wait = False
        for drop in obs.get('drops', []):
            d = distance(pos, drop['pos'])
            enemies = sum(distance(m['pos'], drop['pos']) < 100 for m in threats)
            if d < 180 and enemies == 0 and drop['lands_in'] < d/max(1, me['stats']['walk_speed'])+9:
                value = 75 + (1-hp)*80
                candidates.append((*drop['pos'][:2], 'drop', value))
        best = None
        best_score = -float('inf')
        visited = set()
        for x, y, kind, value in candidates:
            k = self.world.nearest(x, y)
            if (k, kind) in visited or self.world.blocked[k]:
                continue
            visited.add((k, kind))
            nx, ny = self.world.point(k)
            # Reach the actual pickup/circle center, not just the center of its grid cell.
            exact = kind != 'cover' or radius < 20
            if not exact or not self.world.clear(x, y, self.world.height(x, y),
                                                 me['stats']['radius'], me['stats']['height']):
                x, y = nx, ny
            travel = distance(pos, (x, y))
            if kind != 'cover' and distance((x, y), (candidates[0][0], candidates[0][1])) > 160:
                continue
            margin = radius-distance((x, y), (cx, cy))
            # Stay close enough to reach the following circle, without blindly congregating at center.
            score = -travel*0.16 + min(margin, 32)*0.12 - max(0, 14-margin)*5
            score += min(2, self.world.cover[k])*8
            if not threats and hp < 0.9 and travel < 6 and obs['t']-self.last_damage < 6:
                score += 20
            near_danger = False
            for m in threats[:4]:
                d = distance((x, y), m['pos'])
                if self.settings['enable_memory'] and m.get('_age', 0) > 0:
                    # An uncertain old position must not masquerade as a current
                    # visible attacker. Spread and discount its area cost.
                    score -= 54.4*risk_at(x, y, m, reach=170)
                    if risk_at(x, y, m, reach=70) > 0.25:
                        near_danger = True
                    continue
                if d < 220:
                    eye = [m['pos'][0], m['pos'][1], m['pos'][2]+8*(1+0.05*m.get('cubes', 0))]
                    pt = [x, y, self.world.ground[k]+me['stats']['eye_height']]
                    exposed = self.world.visible(eye, pt)
                    score -= max(0, 170-d)*(0.32 if exposed else 0.035)
                    if d < 70:
                        score -= (70-d)*0.55
                        near_danger = True
            if kind != 'cover':
                score += value * (0.3 if near_danger else 1) / (1+travel/65)
                if kind in ('crate', 'weapon') and hp < 0.5:
                    score -= 25
            if self.goal and distance((x, y), self.goal) < 8:
                score += 7
            if score > best_score:
                best_score = score
                best = (x, y, kind)
        self.goal = best or (cx, cy, 'cover')
        self.goal_time = obs['t']

    def movement(self, obs, threats):
        me = obs['self']
        pos, stats = me['pos'], me['stats']
        t = obs['t']
        cx, cy, radius = self.future_zone(obs)
        radius_body = stats['radius']
        height = stats['height']
        oldgoal = self.goal
        if self.goal is None or t-self.goal_time >= 1.6:
            self.choose_goal(obs, threats)
        if self.settings['enable_tactics'] and self.decision['target'] is not None:
            tactical_goal = self.tactician.goal(obs, self.world, self.decision, self.orbit_side)
            if tactical_goal is not None:
                self.goal = tactical_goal
            elif self.goal[2] == 'engage':
                self.choose_goal(obs, threats)
        elif self.goal[2] == 'engage':
            # Stop following a pursuit goal as soon as its safety checks fail.
            self.choose_goal(obs, threats)
        goal = self.goal[:2]
        self.drop_wait = False
        if self.goal[2] == 'drop':
            for d in obs.get('drops', []):
                if distance(pos, d['pos']) < d['radius']-2 and distance(goal, d['pos']) < 8:
                    self.drop_wait = True
        need_plan = self.planner.target is None or (oldgoal and distance(oldgoal, goal) > 10)
        need_plan |= t-self.last_plan > 3 and not self.planner.heap
        if need_plan:
            self.planner.start(pos, goal, (cx, cy, radius), threats)
            self.last_plan = t
        self.planner.advance()
        waypoint = self.planner.waypoint(pos, radius_body, height)
        if self.world.walk_segment(pos, goal, radius_body, height):
            waypoint = goal
        elif waypoint is None:
            waypoint = goal
        dx, dy = waypoint[0]-pos[0], waypoint[1]-pos[1]
        base = math.atan2(dy, dx)
        if self.last_pos is not None:
            dt = clamp(t-self.last_t, 0, 0.3)
            if distance(pos, self.last_pos) < 0.15 and self.last_move > 0.5:
                self.stuck += dt
            else:
                self.stuck = max(0, self.stuck-dt*2)
        if self.stuck > 0.9:
            self.escape_angle = base+self.rng.choice((-1, 1))*math.pi*0.6
            self.escape_until = t+1.1
            self.stuck = 0
        if t < self.escape_until:
            base = self.escape_angle
        # Score short movement candidates for threats, projectiles and the zone.
        danger = [m for m in threats if distance(pos, m['pos']) < 180]
        best = (-float('inf'), base, 0)
        arrival = 1.5 if self.goal[2] in ('cube', 'weapon') or radius < 20 else 3.5
        stationary_ok = distance(pos, goal) < arrival or self.drop_wait
        for offset in (0, 0.4, -0.4, 0.85, -0.85, 1.4, -1.4, 2.2, -2.2, math.pi, None):
            a = base if offset is None else base+offset
            speed = 0 if offset is None else 1
            ux, uy = math.cos(a)*speed, math.sin(a)*speed
            length = 5.5 if speed else 0
            x, y = pos[0]+ux*length, pos[1]+uy*length
            if speed and not self.world.walk_segment(pos, (x, y), radius_body, height):
                continue
            progress = distance(pos, waypoint)-distance((x, y), waypoint)
            score = progress*1.3
            score -= max(0, distance((x, y), (cx, cy))-radius+6)*2.2
            if speed:
                score -= abs(angle(a-self.move_angle))*0.3
            elif stationary_ok:
                score += 9
            else:
                score -= 5
            for m in danger:
                old = distance(pos, m['pos'])
                new = distance((x, y), m['pos'])
                if self.settings['enable_memory'] and m.get('_age', 0) > 0:
                    score += 18*(risk_at(*pos[:2], m)-risk_at(x, y, m))
                    continue
                safe = 105 if 'lightning' in m.get('weapons', []) else 85
                if self.decision['target'] == m['id'] and self.goal[2] == 'engage':
                    preferred = self.decision['range']
                    if self.decision['mode'] == 'finish':
                        preferred = min(preferred, old)
                    score += (abs(old-preferred)-abs(new-preferred))*0.4
                else:
                    score += (new-old)*clamp((safe+50-old)/80, 0, 1)*1.7
                if m.get('charging'):
                    bearing = math.atan2(y-m['pos'][1], x-m['pos'][0])
                    if abs(angle(bearing-m['aim'][0])) < 0.10:
                        score -= 12
            # Closest approach of bullets to the moving body over the next half-second.
            for p in obs['projectiles'][:32]:
                rx, ry = p['pos'][0]-pos[0], p['pos'][1]-pos[1]
                vx, vy = p['vel'][0]-ux*stats['walk_speed'], p['vel'][1]-uy*stats['walk_speed']
                tt = clamp(-(rx*vx+ry*vy)/max(1, vx*vx+vy*vy), 0, 0.55)
                sep = math.hypot(rx+tt*vx, ry+tt*vy)
                z = p['pos'][2]+p['vel'][2]*tt
                if p['kind'] == 'mortar':
                    z -= self.config['weapons']['mortar']['gravity']*tt*tt/2
                if pos[2]-3 < z < pos[2]+height+3 and sep < radius_body+5:
                    score -= (radius_body+5-sep)*2
            if score > best[0]:
                best = score, a, speed
        _, a, speed = best
        if not math.isfinite(best[0]):
            a, speed = base, 1
        move = [math.cos(a)*speed, math.sin(a)*speed]
        if speed and distance(pos, goal) < 2.5 and not danger and not self.drop_wait:
            move = [v*0.3 for v in move]
        self.move_angle = a
        self.last_move = math.hypot(*move)
        self.last_pos, self.last_t = list(pos), t
        nearby = min((distance(pos, m['pos']) for m in threats if m.get('_confidence', 1) > 0.5), default=999)
        urgent = not me['in_zone'] or distance(pos, obs['zone']['center']) > obs['zone']['radius']-15
        fleeing = nearby < 90 or (t-self.last_damage < 2 and nearby < 180)
        # Hysteresis avoids energy stutter and leaves a reserve for escape.
        was_boosting = getattr(self, 'boosting', False)
        self.boosting = bool(speed and (urgent or fleeing) and me['energy'] > (12 if was_boosting else 42))
        if self.goal[2] == 'engage' and self.decision['mode'] == 'finish' and self.decision['ttk'] < 1 and not urgent:
            self.boosting = False
        jump = bool(t < self.escape_until and me['grounded'] and me['energy'] > 35)
        if not me['grounded'] and t < self.escape_until and me['energy'] > 20:
            self.boosting = True
        return move, self.boosting, jump

    def target_point(self, m):
        p = m['pos']
        if m.get('kind') == 'npc':
            h = self.config['npc']['height']/2
        elif m.get('kind') == 'crate':
            h = self.config['crates']['height']/2
        else:
            h = self.config['mech']['height']*(1+self.config['cubes'].get('size_per_cube', 0.05)*m.get('cubes', 0))/2
        return [p[0], p[1], p[2]+h]

    def combat(self, obs, act, threats):
        me = obs['self']
        pos, stats = me['pos'], me['stats']
        origin = [pos[0], pos[1], pos[2]+stats['muzzle_height']]
        healing = me['hp'] < me['max_hp']*0.67
        targets = []
        for m in obs['mechs']:
            d = distance(pos, m['pos'])
            hostile = m['kind'] == 'mech'
            if not hostile and (threats or healing or d > 100):
                continue
            committed = self.settings['enable_tactics'] and self.decision['target'] == m['id']
            if hostile and healing and d > 85 and obs['t']-self.last_damage > 2 and not committed:
                continue
            point = self.target_point(m)
            if not self.world.visible(origin, point):
                # A low wall may cover the center but leave the upper body exposed.
                point[2] += 2.0
                if not self.world.visible(origin, point):
                    continue
            score = (110 if hostile else 5)-d*0.32
            score += max(0, 450-m.get('hp', 1400))*0.08
            if committed:
                score += 100
            if hostile and d < 55:
                score += 30
            targets.append((score, m, point))
        if not targets and not threats and not healing:
            for crate in obs['crates']:
                d = distance(pos, crate['pos'])
                if d > 105:
                    continue
                m = dict(crate, kind='crate')
                point = self.target_point(m)
                if self.world.visible(origin, point):
                    targets.append((20-d*0.25, m, point))
        if not targets:
            for w in me['weapons']:
                if w and w['kind'] == 'gun' and w.get('ammo', 0) < w['stats']['mag'] and not w.get('reload', 0):
                    act['reload'] = True
            if threats:
                p = threats[0]['pos']
                act['aim'] = [math.atan2(p[1]-pos[1], p[0]-pos[0]), 0]
            else:
                self.scan = angle(self.scan+0.055)
                act['aim'] = [self.scan, 0]
            act['fire'] = [False, False]
            return
        _, target, point = max(targets, key=lambda v: v[0])
        velocity = list(target.get('vel', [0, 0, 0]))
        # Grounded motion has no ballistic fall. Do not extrapolate a jumping target forever.
        velocity[2] *= 0.35
        dist = math.dist(origin, point)
        weapons = me['weapons']
        # A charged laser or ready mortar owns the shared torso aim for this tick.
        primary = next((i for i, w in enumerate(weapons) if w and w['kind'] == 'gun'), None)
        laser = next((i for i, w in enumerate(weapons) if w and w['kind'] == 'laser'), None)
        mortar = next((i for i, w in enumerate(weapons) if w and w['kind'] == 'mortar'), None)
        lightning = next((i for i, w in enumerate(weapons) if w and w['kind'] == 'lightning'), None)
        mode = 'gun'
        chosen = primary
        if laser is not None:
            w = weapons[laser]
            if w.get('charge', 0) > 0 or (w['cooldown'] <= 0.05 and dist < w['stats']['range'] and
                    (not act['boost'] or target.get('hp', 1400) < 300)):
                mode, chosen = 'laser', laser
        committed = self.settings['enable_tactics'] and self.decision['target'] == target['id']
        if mode == 'gun' and not committed and mortar is not None and 30 < dist < 180 and weapons[mortar]['cooldown'] <= 0.05:
            mode, chosen = 'mortar', mortar
        if lightning is not None and dist < weapons[lightning]['stats']['range'] and mode != 'laser':
            mode, chosen = 'lightning', lightning
        if chosen is None:
            chosen = next((i for i, w in enumerate(weapons) if w), None)
            mode = weapons[chosen]['kind'] if chosen is not None else 'gun'
        if chosen is None:
            return
        w = weapons[chosen]
        wp = w['stats']
        aimpoint = point
        pitch = None
        ballistic_valid = mode != 'mortar'
        if mode == 'gun':
            aimpoint = intercept(origin, point, velocity, wp['speed'])
        elif mode == 'mortar':
            origin = [pos[0], pos[1], pos[2]+stats['height']+0.5]
            aimpoint = list(point)
            for _ in range(4):
                d = distance(origin, aimpoint)
                dz = aimpoint[2]-origin[2]
                v, g = wp['speed'], wp['gravity']
                disc = v**4-g*(g*d*d+2*dz*v*v)
                if disc < 0 or d < 15:
                    ballistic_valid = False
                    break
                pitch = math.atan((v*v-math.sqrt(disc))/(g*d))
                ballistic_valid = True
                flight = d/max(1, v*math.cos(pitch))
                aimpoint = [point[i]+velocity[i]*min(3, flight) for i in range(3)]
        dx, dy, dz = [aimpoint[i]-origin[i] for i in range(3)]
        yaw = math.atan2(dy, dx)
        if pitch is None:
            pitch = math.atan2(dz, math.hypot(dx, dy))
        pitch_valid = stats['min_pitch'] <= pitch <= stats['max_pitch']
        act['aim'] = [yaw, clamp(pitch, stats['min_pitch'], stats['max_pitch'])]
        act['fire'] = [False, False]
        yaw_error = abs(angle(yaw-me['aim'][0]))
        pitch_error = abs(pitch-me['aim'][1])
        body_r = self.config['crates']['radius'] if target['kind'] == 'crate' else (
            self.config['npc']['radius'] if target['kind'] == 'npc' else
            self.config['mech']['radius']*(1+0.05*target.get('cubes', 0)))
        tolerance = math.atan2(body_r+0.35, max(1, dist))
        aligned = yaw_error < tolerance+0.018 and pitch_error < max(tolerance, 0.035)
        if mode == 'gun':
            if aligned and dist < wp['range'] and not w.get('reload', 0) and pitch_valid:
                act['fire'][chosen] = True
        elif mode == 'lightning':
            act['fire'][chosen] = bool(yaw_error < math.radians(wp['cone_deg'])*0.8 and
                                      dist < wp['range']-1 and me['energy'] >= wp['energy']+8)
        elif mode == 'laser':
            charge = w.get('charge', 0)
            full = wp['charge']
            # Hold until charged and aligned; release actually fires the beam.
            act['fire'][chosen] = bool(charge < full or not aligned)
            if charge == 0 and (act['boost'] or not pitch_valid):
                act['fire'][chosen] = False
        elif mode == 'mortar' and ballistic_valid and pitch_valid and pitch is not None:
            # Check the arc, not a direct ray, before launching near buildings.
            d = distance(origin, aimpoint)
            flight = d/max(1, wp['speed']*math.cos(pitch))
            previous = origin
            clear = True
            for i in range(1, 9):
                tt = flight*i/8
                arc = [origin[0]+wp['speed']*math.cos(pitch)*math.cos(yaw)*tt,
                       origin[1]+wp['speed']*math.cos(pitch)*math.sin(yaw)*tt,
                       origin[2]+wp['speed']*math.sin(pitch)*tt-wp['gravity']*tt*tt/2]
                if not self.world.visible(previous, arc):
                    clear = False
                    break
                previous = arc
            act['fire'][chosen] = bool(clear and yaw_error < 0.035 and pitch_error < 0.035 and dist > 25)
        # Gun and lightning can share a direct aim; mortar trajectories cannot.
        if (mode == 'laser' or (mode == 'lightning' and self.settings['enable_tactics'])) and primary is not None:
            gw = weapons[primary]
            lead = intercept([pos[0], pos[1], pos[2]+stats['muzzle_height']], point, velocity, gw['stats']['speed'])
            gy = math.atan2(lead[1]-pos[1], lead[0]-pos[0])
            gp = math.atan2(lead[2]-pos[2]-stats['muzzle_height'], distance(lead, pos))
            if abs(angle(gy-me['aim'][0])) < tolerance+0.01 and abs(gp-me['aim'][1]) < max(0.035, tolerance) and dist < gw['stats']['range']:
                act['fire'][primary] = True
        if lightning is not None and mode != 'mortar':
            lw = weapons[lightning]['stats']
            direct = math.atan2(point[1]-pos[1], point[0]-pos[0])
            if dist < lw['range']-1 and abs(angle(direct-me['aim'][0])) < math.radians(lw['cone_deg'])*0.8 and me['energy'] > lw['energy']+8:
                act['fire'][lightning] = True
        if primary is not None:
            gun = weapons[primary]
            if gun.get('ammo', 0) == 0 or (gun.get('ammo', 0) < 4 and dist > 145):
                act['reload'] = True

    def act(self, obs):
        me, t = obs['self'], obs['t']
        for event in obs['events']:
            if event['type'] == 'damaged':
                self.last_damage = t
            if event['type'] == 'pickup':
                for key in list(self.loot):
                    item, _, kind = self.loot[key]
                    if kind == 'cube' and distance(item['pos'], me['pos']) < 5:
                        del self.loot[key]
        if self.settings['enable_memory']:
            threats = self.tracker.update(obs)
        else:
            for m in obs['mechs']:
                if m['kind'] == 'mech':
                    self.memory[m['id']] = (m, t)
            threats = [m for m, when in self.memory.values() if t-when < 7]
        threats.sort(key=lambda m: distance(m['pos'], me['pos']))
        for key, (_, when) in list(self.memory.items()):
            if t-when > 10:
                del self.memory[key]
        for field, kind in (('cubes', 'cube'), ('pickups', 'weapon'), ('crates', 'crate')):
            seen = {p['id'] for p in obs[field]}
            for key, (p, expiry, oldkind) in list(self.loot.items()):
                if expiry < t or (oldkind == kind and distance(p['pos'], me['pos']) < 12 and key not in seen):
                    del self.loot[key]
            for p in obs[field]:
                self.loot[p['id']] = (p, t+15, kind)
        act = {'tick': obs['tick']}
        if obs['offer']:
            act['pick'] = self.pick_upgrade(obs['offer'], me)
        if self.settings['enable_tactics']:
            self.decision = self.tactician.choose(obs, self.world, threats, self.future_zone(obs),
                                                  self.decision['target'])
        else:
            self.decision = {'mode': 'survive', 'target': None}
        if self.settings['enable_rl'] and self.settings['enable_tactics']:
            choice = self.learner.step(obs, self.decision)
            if choice == OnlineSarsa.CAUTIOUS:
                # Only decline active pursuit. Movement, retreat shooting,
                # pickups, zone checks and aiming remain rule-controlled.
                self.decision = {'mode': 'survive', 'target': None}
        move, boost, jump = self.movement(obs, threats)
        act.update(move=move, boost=boost, jump=jump)
        self.combat(obs, act, threats)
        held = {w['kind'] for w in me['weapons'] if w}
        near = [p for p in obs['pickups'] if p['kind'] not in held and
                distance(p['pos'], me['pos']) < self.config['loot']['pickup_radius'] and
                abs(p['pos'][2]-me['pos'][2]) < 4]
        if near:
            p = max(near, key=lambda p: {'lightning': 3, 'laser': 2, 'mortar': 1, 'gun': 0}[p['kind']])
            if len(held) < 2:
                act['take'] = p['id']
            elif p['kind'] == 'lightning' and 'mortar' in held:
                act['take'] = p['id']
                act['drop'] = next(i for i, w in enumerate(me['weapons']) if w and w['kind'] == 'mortar')
        return act

    def on_end(self, message):
        if self.settings['enable_rl'] and self.settings['enable_tactics']:
            self.rl_summary = self.learner.finish(message['place'])


from controller import ControllerMixin


class Bot(ControllerMixin, LegacyBot):
    name = 'Oval Sentinel v7 Hunter'


if __name__ == '__main__':
    mf.run(Bot)
