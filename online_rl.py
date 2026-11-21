"""Small on-policy, undiscounted SARSA controller for V3 tactical choices.

No simulator-trained weights, hidden labels, cross-match persistence or neural
network. Fixed V3 rules propose feasible tactics; this learner can only accept
or decline a proposal. Updates use the actual next selected action, not max Q.
"""
import math
import random


class OnlineSarsa:
    ACCEPT = 0
    CAUTIOUS = 1

    def __init__(self, settings, seed, mech_count=16):
        self.settings = settings
        self.mech_count = mech_count
        self.rng = random.Random(seed ^ 0x5341525341)
        self.q = {}
        self.state = None
        self.action = None
        self.allowed = None
        self.decision_t = None
        self.previous_potential = None
        self.initial_potential = None
        self.rank_progress = 0
        self.pending_reward = 0.0
        self.shaped_return = 0.0
        self.updates = 0
        self.selections = 0
        self.explorations = 0
        self.finished = False
        self.terminal_score = None
        self._validate()

    def _validate(self):
        for key in ('enable_rl', 'rl_updates'):
            if not isinstance(self.settings[key], bool):
                raise ValueError(f'{key} must be a JSON boolean')
        for key in ('rl_alpha', 'rl_epsilon', 'rl_interval_s', 'rl_rule_prior', 'rl_switch_margin'):
            value = self.settings[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{key} must be a finite number')
        if not 0 < self.settings['rl_alpha'] <= 1:
            raise ValueError('rl_alpha must be in (0, 1]')
        if not 0 <= self.settings['rl_epsilon'] <= 0.1:
            raise ValueError('rl_epsilon must be in [0, 0.1]')
        if not 0.2 <= self.settings['rl_interval_s'] <= 5:
            raise ValueError('rl_interval_s must be in [0.2, 5]')
        if not 0 <= self.settings['rl_rule_prior'] <= 1 or not 0 <= self.settings['rl_switch_margin'] <= 1:
            raise ValueError('RL prior and switching margin must be in [0, 1]')

    def potential(self, obs):
        """Bounded heuristic potential; terminal potential is exactly zero.

        With gamma=1, differences telescope. These are NOT independent hit or
        survival bonuses: at termination all remaining potential is removed.
        """
        me = obs['self']
        health = max(0, min(1, me['hp']/max(1, me['max_hp'])))
        energy = max(0, min(1, me['energy']/max(1, me['max_energy'])))
        pressure = max((1-max(0, min(1, m.get('hp', m.get('max_hp', 1400))/
                                      max(1, m.get('max_hp', 1400))))
                        for m in obs['mechs'] if m['kind'] == 'mech'), default=0)
        return health+0.1*energy+0.1*int(me['in_zone'])+0.15*pressure

    def context(self, obs, proposal):
        if proposal.get('target') is None:
            return (-1, -1, -1, -1, -1), (self.ACCEPT,)
        me = obs['self']
        ttk = max(0.05, proposal.get('ttk') or 0.05)
        danger = proposal.get('danger_s') or 0
        state = (int(me['hp']/max(1, me['max_hp']) >= 0.6),
                 int(me['energy']/max(1, me['max_energy']) >= 0.4),
                 int(danger/ttk >= 3), int(ttk <= 1.2),
                 int(proposal['mode'] == 'finish'))
        return state, (self.ACCEPT, self.CAUTIOUS)

    def values(self, state):
        if state not in self.q:
            # The first policy favors V3. This is human initialization, not
            # pretrained game experience. There are at most 33 abstract states.
            prior = 0 if state[0] == -1 else self.settings['rl_rule_prior']
            self.q[state] = [prior, 0.0]
        return self.q[state]

    def select(self, state, allowed, explore=False):
        values = self.values(state)
        self.selections += 1
        if len(allowed) == 1:
            return allowed[0]
        if explore and self.rng.random() < self.settings['rl_epsilon']:
            self.explorations += 1
            return self.rng.choice(allowed)
        return (self.CAUTIOUS if values[self.CAUTIOUS] >
                values[self.ACCEPT]+self.settings['rl_switch_margin'] else self.ACCEPT)

    def update(self, state, action, reward, next_state=None, next_action=None):
        values = self.values(state)
        # Undiscounted finite match: gamma=1. Using selected next_action is SARSA.
        future = 0 if next_state is None else self.values(next_state)[next_action]
        td_error = reward+future-values[action]
        value = values[action]+self.settings['rl_alpha']*td_error
        # Bound numerical estimates only; do not clip the actual reward stream.
        values[action] = max(-20.0, min(20.0, value))
        self.updates += 1
        return td_error

    def step(self, obs, proposal):
        if self.finished:
            return self.ACCEPT
        state, allowed = self.context(obs, proposal)
        potential = self.potential(obs)
        progress = self.mech_count-obs['alive']
        if self.previous_potential is None:
            self.previous_potential = self.initial_potential = potential
            self.rank_progress = progress
            # Progress already observed before the first action is bookkeeping,
            # not attributed to a decision the learner never made.
            self.shaped_return = float(progress)
        else:
            reward = progress-self.rank_progress+potential-self.previous_potential
            self.pending_reward += reward
            self.shaped_return += reward
            self.rank_progress = progress
            self.previous_potential = potential
        boundary = (self.state is None or state != self.state or allowed != self.allowed or
                    obs['t']-self.decision_t >= self.settings['rl_interval_s'])
        if boundary:
            # Exploration is limited to V3-approved engagements with substantial
            # health and estimated time margin. It never invents unsafe pursuit.
            me = obs['self']
            ttk = proposal.get('ttk') or 0
            danger = proposal.get('danger_s') or 0
            explore = (len(allowed) == 2 and me['hp'] >= 0.7*me['max_hp'] and
                       danger > ttk*2.5+1)
            action = self.select(state, allowed, explore)
            if self.state is not None and self.settings['rl_updates']:
                self.update(self.state, self.action, self.pending_reward, state, action)
            self.state, self.action, self.allowed = state, action, allowed
            self.decision_t = obs['t']
            self.pending_reward = 0.0
        return self.action

    def finish(self, place):
        """Correct rank rewards for ties/time limit and do a terminal TD update.

        Full bookkeeping shaped return = official score - initial potential.
        This algebra does not imply convergence or better play in this POMDP.
        A terminal update cannot improve the match that has already ended.
        """
        if self.finished:
            return self.summary()
        self.finished = True
        self.terminal_score = self.mech_count-place
        if self.state is not None:
            correction = self.terminal_score-self.rank_progress-self.previous_potential
            self.pending_reward += correction
            self.shaped_return += correction
            if self.settings['rl_updates']:
                self.update(self.state, self.action, self.pending_reward)
        else:
            self.shaped_return = float(self.terminal_score)
        return self.summary()

    def summary(self):
        return {'updates': self.updates, 'states': len(self.q), 'selections': self.selections,
                'explorations': self.explorations, 'score': self.terminal_score,
                'shaped_return': self.shaped_return,
                'initial_potential': self.initial_potential}
