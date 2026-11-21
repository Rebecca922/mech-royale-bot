# Oval Sentinel v3 — Mech Royale

A Python standard-library bot for the Buildathon Mech Royale track. The
Dockerfile is at the repository root. No package installation, model download,
network request or writable working directory is needed at runtime.

## Strategy

- Build a clearance grid from the authoritative map's terrain and solid prisms.
  Respect building courtyards and bridge clearance; use bounded incremental A*
  with diagonal corner checks and short local collision checks.
- Predict near-term zone shrink, seek nearby cover, avoid concentrations of
  enemies, and retreat when health is low so passive regeneration can start.
- Collect nearby cubes and weapons; consider uncontested supply drops for healing.
  Prefer regeneration, mobility and defensive upgrades.
- Intercept moving targets with the gun. Check shot obstruction because tracked
  enemies may be behind cover. Hold/release the laser, solve low mortar arcs,
  and conserve energy when using lightning.
- Face threats while moving independently, dodge approaching projectiles,
  reserve boost energy and attempt a jump/jet recovery when stuck.

## Local checks

Python 3.9 or newer; runtime image uses a fixed Python 3.13.0 version.

```sh
python3 -B -m unittest discover -s . -p 'test_*.py' -v
python3 check/check.py -- python3 -B -u bot.py
```

The supplied `check.py` is **not a simulator**: recorded observations do not
respond to the bot's actions. Passing proves protocol compliance, not strength.
This kit contains no game engine for local matches.

## Build and check the scoring container

Run from this directory with Docker installed:

```sh
docker build --platform linux/amd64 -t oval-sentinel .
python3 check/check.py --map-dir /arena -- docker run --rm -i \
  --platform linux/amd64 --network none --cpus 1 --memory 512m \
  --memory-swap 512m --pids-limit 128 --read-only \
  --tmpfs /tmp:rw,size=64m --user 65534:65534 \
  -v "$PWD/arena:/arena:ro" oval-sentinel
```

The image copies `bot.py`, `navigation.py`, `mechfight.py`, `tactics.py`, and `strategy.json`. The arena
is mounted by the game server; the copy here is just for local checks.
The runtime writes no files. Docker's base image is version-tagged rather than
digest-pinned, so the exact OS layer bytes are not guaranteed immutable.

## Submit

1. Unzip the package. Put **the contents** of this directory at the root of a
   public GitHub repository. `Dockerfile` must be at that root, not nested.
2. Keep the MIT `LICENSE` and this attribution. Commit and push the files.
3. Run the Docker check above, then submit the repository URL and commit on the
   Builders — Mech Royale track page.
4. Keep every submitted commit public until results are published. Use the
   first 48-match practice score to decide what to change next.

## Verification and limitations

See `V3_VALIDATION.md` for the current checks and `V3_NOTES.md` for the strategy changes.
`VALIDATION.md` records the original V1 checks.
No Docker executable or match engine was available there, so neither a Docker
build nor real game performance has been verified. No leaderboard score is claimed.

The clearance grid plans on ground level. Roof spawns use local collision
checks to leave the roof; there is no full multi-level navigation graph. Wide
mechs may need more room than the initial grid reserves. Enemy motion prediction
is approximate and cannot anticipate sudden turns. Map geometry is read during
hello and invalid/missing map files are logged by the kit's wrapper; an arena
mount is required for useful play.

## Attribution and tools

`mechfight.py`, `check/`, `mechfight.schema.json`, `protocol.md`, and `arena/`
come from the official `mechfight-kit.tar.gz` supplied for the competition.
`bot.py`, `navigation.py`, and `test_bot.py` are new for this entry and were
developed with OpenAI Codex assistance. No external Python packages are used.

V3 additionally adds `tactics.py`, fixed parameters in `strategy.json`, and
`test_tactics.py`. This version is not trained and does not update policy weights
during a match. Both feature switches can be disabled independently; see
`V3_NOTES.md` for the controlled comparisons.
The MIT grant covers this entry's original code; upstream kit assets remain
subject to the organizer's applicable terms.
