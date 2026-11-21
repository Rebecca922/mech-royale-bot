# Oval Sentinel v5 SL — Mech Royale

A Python standard-library bot for the Buildathon Mech Royale track.
The Dockerfile is at the repository root. No third-party packages or models
are downloaded during the build; runtime needs no network or writable files.

## Current experiment

V5 adds online supervised calibration of the existing incoming-mech-damage
estimate, starting from V4-F (RL updates frozen). One regression coefficient
starts at 1, is fitted with a regularized squared-error objective, and is used
only after five valid observation windows. Its applied range is [0.8, 1.5].
Labels come from subsequent mech-weapon damage events. Zone damage, NPC damage,
self-damage, unknown sources and observation gaps are handled explicitly.
The model only calibrates the tactical danger estimate; map navigation, weapon
execution, memory and upgrades remain rule-based. Details: V5_NOTES.md.

This is within-match learning, not offline pretraining. Each new bot starts
from its initial values. No model or trajectory is persisted across matches.
The preceding online SARSA module remains frozen (rl_updates=false).

User-reported scores: V1 1.73, V3 1.85, V4 2.06, V4-F 2.00.
V4-F is the direct control for this experiment. V5 has no measured match score.

## Local checks

Python 3.9 or newer (the image uses a fixed Python 3.13.0 version):

```sh
python3 -B -m unittest discover -s . -v
python3 check/check.py -- python3 -B -u bot.py
python3 demo_supervised.py
```

The demo uses invented labels for teaching and does not train the submission.
The official conformance replay is not a simulator: actions do not affect it.
See V5_VALIDATION.md for performed checks and their limits.

## Docker scoring sandbox check

```sh
docker build --platform linux/amd64 -t oval-sentinel:v5 .
python3 check/check.py --map-dir /arena -- docker run --rm -i \
  --platform linux/amd64 --network none --cpus 1 --memory 512m \
  --memory-swap 512m --pids-limit 128 --read-only \
  --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/arena:/arena:ro" oval-sentinel:v5
```

The image copies the runtime Python files and strategy.json only. The map is
mounted by the scorer at /arena. arena/ is included here for local checks.
No Docker executable was available during development; the full image has
not been built or run here. The Python base image uses a version tag rather
than an immutable digest.

## Configuration and comparison

strategy.json controls the candidate. enable_sl=false bypasses calibration
and reproduces V4-F on the 300 official recorded observations.
Keep rl_updates=false when isolating supervised calibration. Enabling it
would create a separate combined-learning experiment.
Commit and push every configuration change before submitting its new SHA.
Keep all submitted commits public until results are published.

## Limitations

The grid is primarily ground navigation, motion prediction is approximate,
and labels are observational rather than controlled interventions. Unknown
attacks and missing terminal damage can bias calibration. Short matches may
not yield five samples. Better prediction error does not ensure better rank.
Internal error statistics are not promised to be downloadable from the scorer.
No claim of convergence, an optimal policy, or improved tournament score is made.

## Attribution and license

mechfight.py originated in the official kit, with an optional terminal callback
added for learning experiments. check/, mechfight.schema.json, protocol.md,
and arena/ are official competition kit assets. Original bot, navigation,
tactics, learning modules and tests were developed with OpenAI Codex assistance.
Original code is MIT licensed; upstream kit assets retain applicable organizer
terms. No external Python dependencies are used.
