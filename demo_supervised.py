"""A supervised-regression mechanics demo, not mech training or a simulator."""
import json
from pathlib import Path
from damage_model import DamageCalibrator


def main():
    settings = json.loads((Path(__file__).parent/'strategy.json').read_text())
    model = DamageCalibrator(settings)
    obs = {'t': 0, 'self': {'id': 3}, 'events': []}
    model.observe(obs, 100)
    print('Teaching example: rule estimate=100 DPS, invented observed damage=50 DPS.')
    print('Not real mech data; no trained model is saved.')
    print('window  samples  applied_factor  next_prediction')
    for frame in range(1, 81):
        obs.update(t=frame/10, events=[{'type': 'damaged', 'amount': 5, 'weapon': 'gun', 'by': 7}])
        model.observe(obs, 100)
        if frame % 10 == 0:
            print(f'{frame//10:6}  {model.samples:7}  {model.factor():14.3f}  {model.predict(100):15.1f}')
    print('The first four windows keep the prior; later adjustments are limited to 0.8–1.5.')


if __name__ == '__main__':
    main()
