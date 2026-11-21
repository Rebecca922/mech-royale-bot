# Oval Sentinel V7 Hunter

A complete strategy revision for Mech Royale. MIT License, Python standard
library only. This candidate has no scored practice result yet.

The active entrypoint is `bot.py:Bot`, which combines ControllerMixin with shared
V4 initialization, static geometry and upgrade selection. The original V4
controller is retained as LegacyBot for regression tests and comparison.
Online SARSA and V5 supervised calibration do not participate in V7 decisions.

## Run and submit

```sh
python3 -B -m unittest discover -s . -v
python3 check/check.py -- python3 -B -u bot.py
```

Expected: 73 tests and checker PASS. Tests include 48 legacy algorithm tests
(explicitly importing LegacyBot) and 25 tests of the active V7 controller/physics.

```sh
docker build --platform linux/amd64 -t mech-v7 .
python3 check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "$PWD/arena:/arena:ro" mech-v7
```

Push this repository, then submit its full new commit SHA. Do not delete old
submitted commits. Prior best V4 scored 2.06; V6 scored 1.92 according to the team.
The contest preserves the best practice submission unless another is selected.

Read V7_NOTES.md for algorithm choices and V7_VALIDATION.md for exact evidence
and limitations. START_HERE.md provides the short Chinese overview.

## Reused code and license

The official Mechfight kit supplies protocol/schema, mechfight.py, checker and
arena assets. Navigation, original tactics, SARSA module and weapon aiming reuse
this team's V1–V6 work. V7 movement and high-level decisions are hand-designed;
no pretrained weights, external team code or patch-to-answer tables are included.
All project code is released under MIT; see LICENSE. Historical version notes
remain as history; the active version is described in V7_NOTES.md.
