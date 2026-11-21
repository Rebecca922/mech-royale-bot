"""Static geometry, clearance grid and bounded incremental A*; standard library only."""
import array
import heapq
import json
import math
import os
import sys
import time
from tactics import risk_at


def inside(x, y, ring):
    hit = False
    ax, ay = ring[-2:]
    for i in range(0, len(ring), 2):
        bx, by = ring[i:i + 2]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            hit = not hit
        ax, ay = bx, by
    return hit


def edge_distance2(x, y, ring):
    best = float('inf')
    ax, ay = ring[-2:]
    for i in range(0, len(ring), 2):
        bx, by = ring[i:i + 2]
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / max(1e-10, dx * dx + dy * dy)))
        best = min(best, (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2)
        ax, ay = bx, by
    return best


class World:
    step = 4.0

    def __init__(self, directory, config):
        with open(os.path.join(directory, 'v2/world.json')) as f:
            w = json.load(f)
        self.bounds = w['bounds']
        self.terrain = w['terrain']
        self.heights = array.array('h')
        with open(os.path.join(directory, 'v2/terrain.i16'), 'rb') as f:
            self.heights.frombytes(f.read())
        if sys.byteorder != 'little':
            self.heights.byteswap()
        self.prisms = w['prisms']
        self.buckets = {}
        for k, p in enumerate(self.prisms):
            r = p['ring']
            p['_box'] = (min(r[::2]), min(r[1::2]), max(r[::2]), max(r[1::2]))
            a, b, c, d = p['_box']
            for j in range(math.floor(b / 32), math.floor(d / 32) + 1):
                for i in range(math.floor(a / 32), math.floor(c / 32) + 1):
                    self.buckets.setdefault((i, j), []).append(k)
        self.x0 = self.bounds['minX'] + 2
        self.y0 = self.bounds['minY'] + 2
        self.nx = int((self.bounds['maxX'] - self.x0) / self.step) + 1
        self.ny = int((self.bounds['maxY'] - self.y0) / self.step) + 1
        n = self.nx * self.ny
        self.blocked = bytearray(n)
        self.ground = array.array('f', [0]) * n
        # Inflate solids for the body and small grid discretization errors.
        for k in range(n):
            x, y = self.point(k)
            z = self.height(x, y)
            self.ground[k] = z
            self.blocked[k] = not self.clear(x, y, z, config['mech']['radius'] + 1.4,
                                           config['mech']['height'])
        self.cover = bytearray(n)
        for k in range(n):
            if self.blocked[k]:
                continue
            i, j = k % self.nx, k // self.nx
            self.cover[k] = sum(self.blocked[(j + dy) * self.nx + i + dx]
                                for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3))
                                if 0 <= i + dx < self.nx and 0 <= j + dy < self.ny)
        self.slope = math.tan(math.radians(config['mech']['max_slope_deg']))

    def height(self, x, y):
        t = self.terrain
        a = max(0, min(t['nx'] - 1.00001, (x - t['minX']) / t['step']))
        b = max(0, min(t['ny'] - 1.00001, (y - t['minY']) / t['step']))
        i, j = int(a), int(b)
        u, v = a - i, b - j
        k = j * t['nx'] + i
        h = self.heights
        return ((h[k] * (1-u) + h[k+1] * u) * (1-v) +
                (h[k+t['nx']] * (1-u) + h[k+t['nx']+1] * u) * v) * t.get('scale', 0.01)

    def point(self, k):
        return self.x0 + (k % self.nx) * self.step, self.y0 + (k // self.nx) * self.step

    def cell(self, x, y):
        i = max(0, min(self.nx - 1, round((x - self.x0) / self.step)))
        j = max(0, min(self.ny - 1, round((y - self.y0) / self.step)))
        return j * self.nx + i

    def nearest(self, x, y):
        k = self.cell(x, y)
        if not self.blocked[k]:
            return k
        i, j = k % self.nx, k // self.nx
        for r in range(1, 20):
            possible = []
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r or not (0 <= i+dx < self.nx and 0 <= j+dy < self.ny):
                        continue
                    q = (j+dy) * self.nx + i+dx
                    if not self.blocked[q]:
                        a, b = self.point(q)
                        possible.append(((a-x)**2 + (b-y)**2, q))
            if possible:
                return min(possible)[1]
        return k

    def nearby(self, x, y, radius=0):
        ids = set()
        for j in range(math.floor((y-radius)/32), math.floor((y+radius)/32)+1):
            for i in range(math.floor((x-radius)/32), math.floor((x+radius)/32)+1):
                ids.update(self.buckets.get((i, j), ()))
        return ids

    def clear(self, x, y, z, radius=2.5, height=9):
        b = self.bounds
        if not (b['minX']+radius <= x <= b['maxX']-radius and b['minY']+radius <= y <= b['maxY']-radius):
            return False
        for k in self.nearby(x, y, radius):
            p = self.prisms[k]
            if p['top'] <= z + 1.0 or p['base'] >= z + height + 0.3:
                continue
            a, b, c, d = p['_box']
            if not (a-radius <= x <= c+radius and b-radius <= y <= d+radius):
                continue
            if any(inside(x, y, h) and edge_distance2(x, y, h) > radius**2 for h in p['holes']):
                continue
            if inside(x, y, p['ring']) or edge_distance2(x, y, p['ring']) < radius**2:
                return False
        return True

    def walk_segment(self, a, b, radius=2.5, height=9):
        dx, dy = b[0]-a[0], b[1]-a[1]
        n = max(1, math.ceil(math.hypot(dx, dy)/2))
        oldz = self.height(a[0], a[1])
        # Preserve the roof level until the feet can step off its edge.
        elevated = a[2] > oldz + 2.0
        for i in range(1, n+1):
            t = i/n
            x, y = a[0]+dx*t, a[1]+dy*t
            z = self.height(x, y)
            if not elevated and z-oldz > max(1.2, self.slope*math.hypot(dx, dy)/n):
                return False
            feet = max(a[2], z) if elevated else z
            if not self.clear(x, y, feet, radius, height):
                return False
            oldz = z
        return True

    def visible(self, a, b):
        """Exact prism intersections, plus terrain sampling. Foliage stops no shots."""
        dx, dy, dz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
        distance = math.hypot(dx, dy)
        n = max(1, math.ceil(distance/16))
        ids = set()
        for i in range(n+1):
            t = i/n
            # Include adjacent buckets: a segment can clip a bucket corner between samples.
            ids.update(self.nearby(a[0]+t*dx, a[1]+t*dy, 9))
        for k in ids:
            p = self.prisms[k]
            lo, hi = 0.0, 1.0
            if abs(dz) < 1e-9:
                if not p['base'] <= a[2] <= p['top']:
                    continue
            else:
                t0, t1 = (p['base']-a[2])/dz, (p['top']-a[2])/dz
                lo, hi = max(lo, min(t0, t1)), min(hi, max(t0, t1))
                if lo > hi:
                    continue
            cuts = [lo, hi]
            for r in [p['ring']] + p['holes']:
                ax, ay = r[-2:]
                for i in range(0, len(r), 2):
                    bx, by = r[i:i+2]
                    ex, ey = bx-ax, by-ay
                    cross = dx*ey-dy*ex
                    if abs(cross) > 1e-9:
                        rx, ry = ax-a[0], ay-a[1]
                        t = (rx*ey-ry*ex)/cross
                        u = (rx*dy-ry*dx)/cross
                        if lo <= t <= hi and 0 <= u <= 1:
                            cuts.append(t)
                    ax, ay = bx, by
            cuts.sort()
            for t0, t1 in zip(cuts, cuts[1:]):
                t = (t0+t1)/2
                x, y = a[0]+dx*t, a[1]+dy*t
                if inside(x, y, p['ring']) and not any(inside(x, y, h) for h in p['holes']):
                    return False
        n = max(1, math.ceil(distance/4))
        for i in range(1, n):
            t = i/n
            if self.height(a[0]+dx*t, a[1]+dy*t) > a[2]+dz*t-0.15:
                return False
        return True


class Planner:
    def __init__(self, world):
        self.w = world
        self.path = []
        self.heap = []
        self.target = None
        self.failed = False

    def start(self, pos, goal, zone, threats):
        w = self.w
        start = w.nearest(*pos[:2])
        self.target = w.nearest(*goal[:2])
        self.gx, self.gy = w.point(self.target)
        self.heap = [(math.hypot(self.gx-pos[0], self.gy-pos[1]), 0.0, start)]
        self.costs = {start: 0.0}
        self.parents = {}
        self.closed = set()
        self.zone = zone
        self.threats = threats[:6]
        self.failed = False
        self.path = []

    def advance(self, budget=0.006):
        w = self.w
        end = time.perf_counter()+budget
        expansions = 0
        while self.heap and expansions < 900:
            if expansions % 24 == 0 and time.perf_counter() > end:
                return
            _, g, k = heapq.heappop(self.heap)
            if k in self.closed:
                continue
            if k == self.target:
                path = [k]
                while k in self.parents:
                    k = self.parents[k]
                    path.append(k)
                self.path = list(reversed(path))
                self.heap = []
                return
            self.closed.add(k)
            expansions += 1
            i, j = k % w.nx, k // w.nx
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                if not (0 <= i+dx < w.nx and 0 <= j+dy < w.ny):
                    continue
                q = k + dy*w.nx+dx
                if w.blocked[q] or q in self.closed:
                    continue
                if dx and dy and (w.blocked[k+dx] or w.blocked[k+dy*w.nx]):
                    continue
                step = w.step*(1.41421356237 if dx and dy else 1)
                if w.ground[q]-w.ground[k] > step*w.slope:
                    continue
                x, y = w.point(q)
                cx, cy, radius = self.zone
                risk = max(0, math.hypot(x-cx, y-cy)-radius+8)*0.08
                risk += sum(0.765*risk_at(x, y, m) if '_confidence' in m else
                            max(0, 85-math.hypot(x-m['pos'][0], y-m['pos'][1]))*0.009
                            for m in self.threats)
                ng = g+step*(1+risk)
                if ng >= self.costs.get(q, float('inf')):
                    continue
                self.costs[q] = ng
                self.parents[q] = k
                heapq.heappush(self.heap, (ng+math.hypot(x-self.gx, y-self.gy), ng, q))
        if not self.heap and not self.path:
            self.failed = True

    def waypoint(self, pos, radius, height):
        if not self.path:
            return None
        # Drop reached nodes and select a safe short look-ahead.
        nearest = min(range(min(18, len(self.path))), key=lambda i: math.dist(pos[:2], self.w.point(self.path[i])))
        if nearest:
            self.path = self.path[nearest:]
        choice = self.w.point(self.path[0])
        for k in self.path[1:7]:
            p = self.w.point(k)
            if not self.w.walk_segment(pos, p, radius, height):
                break
            choice = p
        return choice
