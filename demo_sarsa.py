"""A toy one-step feedback demonstration, NOT the mech game or game training.

Run: python3 demo_sarsa.py
No learned table is saved or loaded by the submitted bot.
"""
import json
from pathlib import Path

from online_rl import OnlineSarsa


def main():
    settings = json.loads((Path(__file__).parent/'strategy.json').read_text())
    settings.update(rl_epsilon=0, rl_alpha=0.2)
    agent = OnlineSarsa(settings, 123)
    state = (1, 1, 1, 0, 0)
    print('Toy feedback only: accept gets -0.20, cautious gets +0.05.')
    print('These rewards are invented for teaching; this is NOT a mech simulator.')
    print('Each trial is one terminal transition. Nothing is saved to the bot.')
    print('trial  action     reward   Q(accept)  Q(cautious)')
    for trial in range(1, 9):
        action = agent.select(state, (0, 1))
        reward = -0.20 if action == 0 else 0.05
        agent.update(state, action, reward)
        q = agent.values(state)
        label = 'accept' if action == 0 else 'cautious'
        print(f'{trial:5}  {label:9} {reward:+.2f}     {q[0]:+.4f}     {q[1]:+.4f}')


if __name__ == '__main__':
    main()
