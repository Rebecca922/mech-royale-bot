"""Resource growth, focused combat, recovery and acceleration-aware steering.

Hand-designed controller; no trained model, policy table or claimed match score.
Short rollouts approximate local movement, not the complete server simulation.
"""
import math
import time


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def angle(v):
    return (v+math.pi)%math.tau-math.pi


def dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


distance = dist


def rollout(me, move, boost, config, horizon=0.6, dt=0.1):
    """Reachable horizontal trajectory with finite acceleration and laser drag.

    Vertical dynamics, collisions and server-specific wall sliding are not
    simulated. The caller separately checks the traversed static geometry.
    """
    s = me['stats']
    speed = s['boost_speed'] if boost and me['grounded'] else s['walk_speed']
    if any(w and w['kind'] == 'laser' and w.get('charge', 0) > 0 for w in me['weapons']):
        speed *= s.get('charge_move_mult', config['mech']['charge_move_mult'])
    accel = s.get('ground_accel' if me['grounded'] else 'air_accel',
                  config['mech']['ground_accel' if me['grounded'] else 'air_accel'])
    vx, vy = me['vel'][:2]
    x, y = me['pos'][:2]
    out = [(0.0, x, y)]
    n = max(1, round(horizon/dt))
    for i in range(n):
        dx, dy = move[0]*speed-vx, move[1]*speed-vy
        length = math.hypot(dx, dy)
        scale = min(1, accel*dt/max(1e-9, length))
        vx += dx*scale
        vy += dy*scale
        x += vx*dt
        y += vy*dt
        out.append(((i+1)*dt, x, y))
    return out


def projectile_cost(path, me, projectiles, config):
    """Swept relative-motion closest approach: a fast bullet cannot tunnel
    between 0.1 s trajectory samples. Vertical body extent is also checked.
    """
    cost = 0.0
    body = me['stats']['radius']+0.55
    foot = me['pos'][2]
    high = foot+me['stats']['height']
    for p in projectiles:
        best = 0.0
        mortar = p['kind'] == 'mortar'
        gravity = config['weapons']['mortar']['gravity'] if mortar else 0
        for (t0, x0, y0), (t1, x1, y1) in zip(path, path[1:]):
            px = p['pos'][0]+p['vel'][0]*t0
            py = p['pos'][1]+p['vel'][1]*t0
            rx, ry = px-x0, py-y0
            vx = p['vel'][0]-(x1-x0)/(t1-t0)
            vy = p['vel'][1]-(y1-y0)/(t1-t0)
            tau = clamp(-(rx*vx+ry*vy)/max(1e-9, vx*vx+vy*vy), 0, t1-t0)
            t = t0+tau
            sep = math.hypot(rx+vx*tau, ry+vy*tau)
            z = p['pos'][2]+p['vel'][2]*t-gravity*t*t/2
            if foot-0.5 <= z <= high+0.5 and sep < body+2:
                severity = clamp((body+2-sep)/2, 0, 1)
                damage = config['weapons']['mortar']['damage'] if mortar else (
                    config['weapons']['gun']['damage'] if p['kind'] == 'gun' else config['npc']['damage'])
                best = max(best, damage*severity)
            # Mortar blast near ground, even when it misses the body directly.
            if mortar and z <= foot+1.5 and p['vel'][2]-gravity*t < 0:
                splash = config['weapons']['mortar']['splash_radius']+me['stats']['radius']
                if sep < splash:
                    best = max(best, config['weapons']['mortar']['splash']*max(.3, 1-sep/splash))
        cost += best
    return cost


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


class ControllerMixin:
    def __init__(self, hello):
        super().__init__(hello)
        self.recovering = False
        self.last_dealt = -100.0
        self.last_hurt_bearing = None
        self.focus_id = None
        self.focus = None
        self.focus_point = None
        self.focus_kind = None
        self.pressure = 0
        self.exposed = []
        self._goal_sight = {}
        self._orbit_phase = (hello['seed']%997)/997*math.tau
        self._goal_identity = None
        self._tick_started = 0
        self._planner_zone = None

    def zone_at(self, obs, future):
        z = obs['zone']
        remaining = z['shrink_end']-max(obs['t'], z['shrink_start'])
        f = clamp((obs['t']+future-max(obs['t'], z['shrink_start']))/max(.001, remaining), 0, 1)
        return (z['center'][0]+f*(z['next_center'][0]-z['center'][0]),
                z['center'][1]+f*(z['next_center'][1]-z['center'][1]),
                z['radius']+f*(z['next_radius']-z['radius']))

    def future_zone(self, obs):
        return self.zone_at(obs, 15)

    def _shot_point(self, me, enemy):
        p = self.target_point(enemy)
        origin = [*me['pos'][:2], me['pos'][2]+me['stats']['muzzle_height']]
        if self.world.visible(origin, p):
            return p
        p[2] += self.config['mech']['height']*.3 if enemy['kind']=='mech' else .6
        return p if self.world.visible(origin, p) else None

    def _effective_dps(self, me, target):
        d = dist(me['pos'], target['pos'])
        c = self.config['cubes']
        scale = 1+c['damage_per_cube']*min(me['cubes'], c['stat_cap'])
        value = 0.0
        for w in me['weapons']:
            if not w:
                continue
            s, kind = w['stats'], w['kind']
            if kind == 'gun' and d < s['range']-3:
                value += s['damage']*s['mag']/(s['mag']*s['cooldown']+s['reload'])*clamp(.96-d*.002, .45, .92)
            elif kind == 'laser' and d < s['range']-3:
                value += s['damage']/(s['charge']+s['cooldown'])*.72*(1+s.get('cookoff', .6)*('mortar' in target.get('weapons', [])))
            elif kind == 'lightning' and d < s['range']-2 and me['energy'] > 20:
                value += s['damage']/s['cooldown']*.85
        return value*scale

    def select_focus(self, obs, threats):
        me = obs['self']; pos = me['pos']
        self.exposed = []
        enemies = []
        for m in obs['mechs']:
            if m['kind'] != 'mech':
                continue
            point = self._shot_point(me, m)
            if point is None:
                continue
            d = dist(pos, m['pos'])
            if d < 245:
                self.exposed.append(m)
            dps = self._effective_dps(me, m)
            if dps <= 0:
                continue
            ttk = m.get('hp', m.get('max_hp', 1400))/max(1, dps)
            # Stick to one victim, especially after already investing damage.
            score = 45-ttk*2.1-d*.055+(13 if m['id']==self.focus_id else 0)
            if m.get('hp', 1400) <= 180:
                score += 24
            if d < 48:
                score += 14
            enemies.append((score, m, point, ttk))
        self.exposed.sort(key=lambda m: dist(pos, m['pos']))
        self.pressure = len(self.exposed)
        self.focus = self.focus_point = self.focus_kind = None
        self.decision = {'mode':'survive', 'target':None, 'ttk':None, 'danger_s':None}
        if enemies:
            _, m, point, ttk = max(enemies, key=lambda e:e[0])
            d = dist(pos, m['pos'])
            incoming, _ = self.tactician.incoming(obs, self.world, threats)
            danger = me['hp']/max(1, incoming)
            can_finish = ttk < 2.4 and danger > ttk*.9+.3
            # Cease dealing damage long enough to permit the actual regen timer.
            if not self.recovering or can_finish or d < 45:
                self.focus, self.focus_point, self.focus_kind = m, point, 'enemy'
                self.focus_id = m['id']
                engage = (not self.recovering and self.pressure <= 1 and me['in_zone'] and
                          (me['hp'] > m.get('hp', 1400)*.85 or any(w and w['kind']=='laser' for w in me['weapons'])))
                preferred = 105
                if any(w and w['kind']=='laser' for w in me['weapons']):
                    preferred = 150
                if 'lightning' in m.get('weapons', []):
                    preferred = max(preferred, 90)
                if (any(w and w['kind']=='lightning' for w in me['weapons']) and
                        'mortar' not in m.get('weapons', []) and not self.recovering and
                        self.pressure==1 and me['energy']>45 and me['hp']>m.get('hp',1400)*1.1):
                    preferred = 34
                mode = 'finish' if can_finish else ('engage' if engage else 'survive')
                self.decision = {'mode':mode,'target':m['id'],'ttk':ttk,'danger_s':danger,'range':preferred}
                return
        # Farming is allowed behind real cover; an old/distant track alone must
        # not prohibit all crates and drones for the rest of the match.
        if self.recovering or self.exposed or not me['in_zone']:
            return
        farms = []
        for m in obs['mechs']:
            if m['kind']=='npc' and dist(pos,m['pos'])<105:
                point = self._shot_point(me,m)
                if point is not None:
                    farms.append((32-dist(pos,m['pos'])*.12,m,point,'npc'))
        for crate in obs['crates']:
            m = dict(crate,kind='crate'); d=dist(pos,m['pos'])
            if d<115:
                point=self._shot_point(me,m)
                if point is not None:
                    farms.append((38-d*.15,m,point,'crate'))
        if farms:
            _,self.focus,self.focus_point,self.focus_kind=max(farms,key=lambda e:e[0])

    def _safe_loot(self, obs, threats):
        me=obs['self']; held={w['kind'] for w in me['weapons'] if w}
        for item, expiry, kind in self.loot.values():
            if expiry<obs['t'] or abs(item['pos'][2]-self.world.height(*item['pos'][:2]))>3:
                continue
            if kind=='cube':
                value=105 if me['cubes']<10 else 55
            elif kind=='weapon':
                # A carried mortar invites laser cook-off; prefer a direct-fire
                # secondary that can also be used alongside the arm gun.
                if item['kind'] in held or item['kind']=='gun':
                    continue
                if len(held)>=2 and not (item['kind']=='laser' and 'mortar' in held):
                    continue
                if item['kind']=='mortar':
                    continue
                value=145 if item['kind']=='laser' else 125
            else:
                value=73 if me['cubes']<6 else 40
            d=dist(me['pos'],item['pos'])
            if d>165:
                continue
            yield (*item['pos'][:2],kind,value,item['id'])

    def choose_goal(self, obs, threats):
        me=obs['self'];pos=me['pos'];t=obs['t']
        cx,cy,radius=self.future_zone(obs)
        hp=me['hp']/me['max_hp']
        candidates=[(*pos[:2],'cover',0,None)]
        if self.goal:
            candidates.append((*self.goal[:2],self.goal[2],0,self._goal_identity))
        for r in (18,40,75):
            for i in range(10):
                a=i*math.tau/10
                candidates.append((pos[0]+r*math.cos(a),pos[1]+r*math.sin(a),'cover',0,None))
        for r in (0,radius*.55,radius*.8):
            for i in range(1 if r==0 else 10):
                a=i*math.tau/10
                candidates.append((cx+r*math.cos(a),cy+r*math.sin(a),'cover',0,None))
        if not self.recovering:
            candidates.extend(self._safe_loot(obs,threats))
        for drop in obs.get('drops',[]):
            d=dist(pos,drop['pos'])
            if d<200 and drop['lands_in']<d/max(1,me['stats']['walk_speed'])+8:
                value=130+(1-hp)*180
                candidates.append((*drop['pos'][:2],'drop',value,drop['id']))
        target = self.focus if self.focus_kind=='enemy' and self.decision['mode'] in ('engage','finish') else None
        if target is not None:
            bearing=math.atan2(pos[1]-target['pos'][1],pos[0]-target['pos'][0])
            r=min(self.decision['range'],max(24,dist(pos,target['pos'])))
            for offset in (0,.22,-.22):
                candidates.append((target['pos'][0]+r*math.cos(bearing+offset),target['pos'][1]+r*math.sin(bearing+offset),'engage',22,target['id']))
        ranked=[];seen=set()
        for x,y,kind,value,identity in candidates:
            k=self.world.nearest(x,y)
            if (k,kind) in seen or self.world.blocked[k]:
                continue
            seen.add((k,kind))
            if kind=='cover':x,y=self.world.point(k)
            elif not self.world.clear(x,y,self.world.height(x,y),me['stats']['radius'],me['stats']['height']):
                continue
            travel=dist(pos,(x,y))
            margin=radius-dist((x,y),(cx,cy))
            score=-travel*.19+min(30,margin)*.1-max(0,min(8,radius*.2)-margin)*5
            score+=min(2,self.world.cover[k])*4
            if kind not in ('cover','engage'):
                score+=value/(1+travel/75)
            if self.goal and dist((x,y),self.goal)<7:
                score+=5
            if self.recovering and travel<4 and obs['t']-self.last_damage>1:
                score+=8
            if target is not None:
                preferred=self.decision['range']
                score-=abs(dist((x,y),target['pos'])-preferred)*.21
            for m in threats[:5]:
                d=dist((x,y),m['pos']);conf=m.get('_confidence',1)
                score-=conf*max(0,80-d)*(.6 if self.recovering or self.pressure>1 else .22)
                if 'lightning' in m.get('weapons',[]):score-=conf*max(0,65-d)*1.3
            ranked.append((score,x,y,kind,identity,k))
        ranked.sort(reverse=True,key=lambda e:e[0])
        # Use the actual line of fire to choose between the best geometrically
        # valid destinations; proximity-to-a-wall alone is not hard cover.
        best=None;best_score=-float('inf')
        nearby=[m for m in threats if m.get('_confidence',1)>.2 and dist(pos,m['pos'])<270][:4]
        for score,x,y,kind,identity,k in ranked[:18]:
            foot=self.world.height(x,y)
            point=[x,y,foot+me['stats']['height']*.65]
            exposure=0
            for m in nearby:
                scale=1+self.config['cubes']['size_per_cube']*m.get('cubes',0)
                origin=[*m['pos'][:2],m['pos'][2]+self.config['mech']['muzzle_height']*scale]
                d=dist((x,y),m['pos'])
                if d<245 and self.world.visible(origin,point):
                    exposure+=m.get('_confidence',1)*clamp((255-d)/120,.2,1.4)
            score-=exposure*(48 if self.recovering or self.pressure>1 else 22)
            if kind=='drop' and any(dist((x,y),m['pos'])<70 for m in nearby):
                score-=90
            if score>best_score:
                best_score=score;best=(x,y,kind);self._goal_identity=identity
            if self._tick_started and time.perf_counter()-self._tick_started>.022:
                break
        self.goal=best or (cx,cy,'cover');self.goal_time=t

    def movement(self, obs, threats):
        me=obs['self'];pos=me['pos'];s=me['stats'];t=obs['t']
        cx,cy,r=self.future_zone(obs)
        changed = self.goal is None or (self.goal[2]=='engage' and self.decision['mode']=='survive')
        if changed or t-self.goal_time>=.9:
            self.choose_goal(obs,threats)
        goal=self.goal[:2]
        direct=self.world.walk_segment(pos,goal,s['radius'],s['height'])
        if direct:
            waypoint=goal
        else:
            goal_cell=self.world.nearest(*goal)
            refresh=(self.planner.target!=goal_cell or self.planner.failed or
                     (t-self.last_plan>2.5 and not self.planner.heap))
            if refresh:
                self.planner.start(pos,goal,(cx,cy,r),threats);self.last_plan=t
            # Avoid planning work consuming the remaining decision deadline.
            remaining=max(0,.027-(time.perf_counter()-self._tick_started)) if self._tick_started else .003
            if remaining>.0002:self.planner.advance(min(.003,remaining))
            waypoint=self.planner.waypoint(pos,s['radius'],s['height']) or goal
        if self.last_pos is not None:
            dt=clamp(t-self.last_t,0,.3)
            self.stuck=self.stuck+dt if dist(pos,self.last_pos)<.12 and self.last_move>.5 else max(0,self.stuck-dt)
        base=math.atan2(waypoint[1]-pos[1],waypoint[0]-pos[0])
        if self.stuck>1.1:
            self.escape_angle=base+self.orbit_side*1.8;self.escape_until=t+1.0
            self.orbit_side*=-1;self.stuck=0
        if t<self.escape_until:base=self.escape_angle
        near=min((dist(pos,m['pos']) for m in threats if m.get('_confidence',1)>.5),default=999)
        urgent=not me['in_zone'] or dist(pos,(cx,cy))>r-8
        retreat=self.recovering or self.pressure>1 or self.decision['mode']=='survive'
        want_boost=urgent or (near<130 and retreat) or (dist(pos,goal)>40 and near>150)
        threshold=24 if getattr(self,'boosting',False) else (40 if near<100 or urgent else 75)
        boost=bool(want_boost and me['energy']>threshold)
        if self.decision['mode']=='engage' and not urgent:boost=False
        if self.decision['mode']=='finish' and (self.decision['ttk'] or 0)<1 and not urgent:boost=False
        directions=[(math.cos(base+o),math.sin(base+o)) for o in (0,.35,-.35,.7,-.7,1.15,-1.15,1.6,-1.6,2.3,-2.3,math.pi)]
        directions.append((0.,0.))
        if dist(pos,goal)<5:
            directions.append((math.cos(base)*.35,math.sin(base)*.35))
        for m in self.exposed[:1]:
            a=math.atan2(pos[1]-m['pos'][1],pos[0]-m['pos'][0])
            directions.extend([(math.cos(a+d),math.sin(a+d)) for d in (0,math.pi/2,-math.pi/2)])
        bullets=sorted(obs['projectiles'],key=lambda p:dist(pos,p['pos'])/max(1,math.hypot(*p['vel'][:2])))[:24]
        best=None
        circle_short=self.zone_at(obs,.6)
        dodge_side=1 if math.sin(t*2.1+self._orbit_phase)>0 else -1
        for ux,uy in directions:
            trial_boost=boost and math.hypot(ux,uy)>.5
            path=rollout(me,(ux,uy),trial_boost,self.config)
            _,x,y=path[-1]
            # Check both the transient and terminal route with current body size.
            mid=path[3]
            if not self.world.walk_segment(pos,(mid[1],mid[2]),s['radius'],s['height']):continue
            if not self.world.walk_segment((mid[1],mid[2],max(pos[2],self.world.height(mid[1],mid[2]))),(x,y),s['radius'],s['height']):continue
            progress=dist(pos,waypoint)-dist((x,y),waypoint)
            score=progress*1.45
            if dist(pos,goal)<3 and not self.exposed and not self.recovering:
                score-=dist((x,y),goal)*1.5
            score-=max(0,dist((x,y),circle_short[:2])-circle_short[2]+min(3,circle_short[2]*.1))*4
            score-=projectile_cost(path,me,bullets,self.config)*(.16 if self.recovering else .12)
            if ux or uy:score-=abs(angle(math.atan2(uy,ux)-self.move_angle))*.25
            if not ux and not uy and self.exposed:score-=6
            for m in self.exposed[:3]:
                old=dist(pos,m['pos']);new=dist((x,y),m['pos'])
                if self.decision['target']==m['id'] and self.decision['mode'] in ('engage','finish'):
                    preferred=min(old,self.decision['range']) if self.decision['mode']=='finish' else self.decision['range']
                    score+=(abs(old-preferred)-abs(new-preferred))*.7
                else:
                    score+=(new-old)*clamp((150-old)/90,0,1)*(1.0 if self.recovering else .5)
                if new<40 and 'lightning' in m.get('weapons',[]):score-=(40-new)*1.5
                if new<s['radius']+self.config['mech']['radius']+1:score-=20
                a=math.atan2(pos[1]-m['pos'][1],pos[0]-m['pos'][0])
                facing=abs(angle(a-m['aim'][0]))<.7
                if facing:
                    lateral=-(x-pos[0])*math.sin(a)+(y-pos[1])*math.cos(a)
                    score+=abs(lateral)*.32+lateral*dodge_side*.08
                if m.get('charging'):
                    line=abs(angle(math.atan2(y-m['pos'][1],x-m['pos'][0])-m['aim'][0]))
                    score-=max(0,.12-line)*100
            # Use line-of-fire gain as a local escape guide, not only distance.
            if (self.recovering or self.pressure>1) and self.exposed:
                m=self.exposed[0]
                origin=[*m['pos'][:2],m['pos'][2]+self.config['mech']['muzzle_height']*(1+self.config['cubes']['size_per_cube']*m.get('cubes',0))]
                end=[x,y,max(pos[2],self.world.height(x,y))+s['height']*.65]
                if not self.world.visible(origin,end):score+=24
            if best is None or score>best[0]:best=(score,[ux,uy],trial_boost)
        if best is None:
            move=[0.,0.];boost=False
        else:
            _,move,boost=best
        self.last_pos=list(pos);self.last_t=t;self.last_move=math.hypot(*move)
        if self.last_move:self.move_angle=math.atan2(move[1],move[0])
        self.boosting=boost
        jump=bool(t<self.escape_until and me['grounded'] and me['energy']>45)
        if t<self.escape_until and not me['grounded'] and me['energy']>22:boost=True
        return move,boost,jump

    def fire_at(self, obs, act, target, point):
        me = obs['self']
        pos, stats = me['pos'], me['stats']
        origin = [pos[0], pos[1], pos[2]+stats['muzzle_height']]
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
        if (mode == 'gun' and mortar is not None and primary is not None and weapons[primary].get('reload', 0) > .25 and 35 < dist < 180 and weapons[mortar]['cooldown'] <= .05):
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
                                      dist < wp['range']-1 and me['energy'] >= wp['energy']+15)
        elif mode == 'laser':
            charge = w.get('charge', 0)
            full = wp['charge']
            # Release once charge is enough for a likely finishing hit, otherwise full charge.
            mult = 1+self.config['cubes']['damage_per_cube']*min(me['cubes'],self.config['cubes']['stat_cap'])
            mult *= 1+wp.get('cookoff', .6)*('mortar' in target.get('weapons', []))
            need = target.get('hp', 1400)*1.15/max(1,wp['damage']*mult)
            full = max(wp.get('min_charge', .15), full*clamp(need, wp.get('min_frac', .35), 1))
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
            if dist < lw['range']-1 and abs(angle(direct-me['aim'][0])) < math.radians(lw['cone_deg'])*0.8 and me['energy'] > lw['energy']+15:
                act['fire'][lightning] = True
        if primary is not None:
            gun = weapons[primary]
            if gun.get('ammo', 0) == 0:
                act['reload'] = True

    def combat(self, obs, act, threats):
        me=obs['self']
        if self.focus is None:
            act['fire']=[False,False]
            for w in me['weapons']:
                if w and w['kind']=='gun' and w.get('ammo',0)<w['stats']['mag'] and not w.get('reload',0):
                    act['reload']=True
            if self.exposed:
                m=self.exposed[0]
                act['aim']=[math.atan2(m['pos'][1]-me['pos'][1],m['pos'][0]-me['pos'][0]),0]
            elif self.last_hurt_bearing is not None and obs['t']-self.last_damage<1.2:
                act['aim']=[self.last_hurt_bearing,0]
            else:
                self.scan=angle(self.scan+.19)
                act['aim']=[self.scan,0]
            return
        self.fire_at(obs,act,self.focus,self.focus_point)

    def act(self, obs):
        self._tick_started=time.perf_counter()
        me=obs['self'];t=obs['t']
        for event in obs['events']:
            if event['type']=='damaged':
                self.last_damage=t
                if event.get('bearing') is not None:self.last_hurt_bearing=event['bearing']
            elif event['type']=='hit':self.last_dealt=t
        hp=me['hp']/max(1,me['max_hp'])
        if hp<.62:self.recovering=True
        elif hp>.9:self.recovering=False
        threats=self.tracker.update(obs)
        threats.sort(key=lambda m:dist(m['pos'],me['pos']))
        for field,kind,ttl in [('cubes','cube',30),('pickups','weapon',90),('crates','crate',90)]:
            seen={p['id'] for p in obs[field]}
            for identity,(p,expiry,oldkind) in list(self.loot.items()):
                if expiry<t or (kind==oldkind and dist(me['pos'],p['pos'])<12 and identity not in seen):
                    del self.loot[identity]
            for p in obs[field]:self.loot[p['id']]=(p,t+ttl,kind)
        previous_mode=self.decision['mode']
        self.select_focus(obs,threats)
        if previous_mode!=self.decision['mode']:self.goal_time=-100
        act={'tick':obs['tick']}
        if obs['offer']:act['pick']=self.pick_upgrade(obs['offer'],me)
        move,boost,jump=self.movement(obs,threats)
        act.update(move=move,boost=boost,jump=jump)
        self.combat(obs,act,threats)
        held={w['kind'] for w in me['weapons'] if w}
        available=[p for p in obs['pickups'] if p['kind'] not in held and p['kind'] in ('laser','lightning') and
                   dist(me['pos'],p['pos'])<self.config['loot']['pickup_radius'] and abs(p['pos'][2]-me['pos'][2])<4]
        if available:
            p=max(available,key=lambda p:2 if p['kind']=='laser' else 1)
            if len(held)<2:act['take']=p['id']
            elif p['kind']=='laser' and 'mortar' in held:
                act.update(take=p['id'],drop=next(i for i,w in enumerate(me['weapons']) if w and w['kind']=='mortar'))
        return act

    def on_end(self, message):
        # No per-match exploratory learner participates in the V7 controller.
        return None
