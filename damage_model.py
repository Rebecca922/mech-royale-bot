"""Online supervised calibration of the existing incoming-mech-damage estimate.

Fit one coefficient w around the human prior w=1 using ridge regression:
    min sum((y-w*x)**2) + lambda*(w-1)**2
where x,y are window-average DPS divided by 100. No fabricated game labels,
neural network, dependency, offline-trained weights or cross-match persistence.
"""
import math


class DamageCalibrator:
    def __init__(self, settings, mech_count=16):
        self.settings = settings
        self.mech_count = mech_count
        self.samples = 0
        self.sum_xx = 0.0
        self.sum_xy = 0.0
        self.window_t = 0.0
        self.window_base = 0.0
        self.window_prediction = 0.0
        self.window_damage = 0.0
        self.last_t = None
        self.last_base = 0.0
        self.last_prediction = 0.0
        self.baseline_sse = 0.0
        self.model_sse = 0.0
        self.scored_windows = 0
        self.gaps = 0
        self.closed = False
        self.last_sample = None
        self._validate()

    def _validate(self):
        if not isinstance(self.settings['enable_sl'], bool):
            raise ValueError('enable_sl must be a JSON boolean')
        for key in ('sl_window_s', 'sl_prior_strength', 'sl_factor_min', 'sl_factor_max', 'sl_min_base_dps'):
            value = self.settings[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be a positive finite number')
        n = self.settings['sl_min_samples']
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError('sl_min_samples must be a positive integer')
        if not self.settings['sl_factor_min'] <= 1 <= self.settings['sl_factor_max']:
            raise ValueError('Calibration limits must contain the human prior 1')

    def factor(self):
        if self.samples < self.settings['sl_min_samples']:
            return 1.0
        prior = self.settings['sl_prior_strength']
        w = (self.sum_xy+prior)/(self.sum_xx+prior)
        return max(self.settings['sl_factor_min'], min(self.settings['sl_factor_max'], w))

    def predict(self, base_dps):
        return max(0.0, base_dps)*self.factor()

    def _reset_window(self):
        self.window_t = self.window_base = self.window_prediction = self.window_damage = 0.0

    def observe(self, obs, base_dps):
        if self.closed:
            return
        now = obs['t']
        if not math.isfinite(base_dps) or base_dps < 0:
            raise ValueError('Base DPS must be nonnegative and finite')
        if self.last_t is not None:
            dt = now-self.last_t
            if dt <= 0 or dt > 0.250001:
                # Queued/skipped observations can lose per-observation events.
                # Do not silently interpret missing damage labels as zero.
                self._reset_window()
                self.gaps += 1
            else:
                self.window_t += dt
                self.window_base += self.last_base*dt
                # This prediction was made BEFORE the current outcome arrived.
                self.window_prediction += self.last_prediction*dt
                for event in obs['events']:
                    identity = event.get('by')
                    if (event['type'] == 'damaged' and event.get('weapon') not in ('zone', 'npc') and
                            isinstance(identity, (int, float)) and 0 <= identity < self.mech_count and
                            identity != obs['self']['id']):
                        self.window_damage += max(0, event['amount'])
                if self.window_t+1e-9 >= self.settings['sl_window_s']:
                    base = self.window_base/self.window_t
                    truth = self.window_damage/self.window_t
                    prediction = self.window_prediction/self.window_t
                    # Without estimated exposure, a multiplicative model cannot
                    # explain damage. Do not fit these as ordinary samples.
                    if base >= self.settings['sl_min_base_dps']:
                        self.baseline_sse += (base-truth)**2
                        self.model_sse += (prediction-truth)**2
                        self.scored_windows += 1
                        x, y = base/100, truth/100
                        self.sum_xx += x*x
                        self.sum_xy += x*y
                        self.samples += 1
                        self.last_sample = {'base_dps': base, 'actual_dps': truth,
                                            'predicted_dps': prediction}
                    self._reset_window()
        self.last_t = now
        self.last_base = base_dps
        self.last_prediction = self.predict(base_dps)

    def finish(self):
        # A death may hide the final damage interval. Do not manufacture a
        # fatal-damage label or train on an incomplete terminal window.
        self.closed = True
        self._reset_window()
        return self.summary()

    def summary(self):
        return {'samples': self.samples, 'factor': self.factor(), 'skipped_gaps': self.gaps,
                'baseline_prequential_mse': self.baseline_sse/self.scored_windows if self.scored_windows else None,
                'model_prequential_mse': self.model_sse/self.scored_windows if self.scored_windows else None}
