# V7 validation

- 73 unit tests passed: 48 legacy-controller/geometry/learning regression tests
  explicitly import LegacyBot; 25 new tests exercise V7 behavior and local physics.
- Active V7 entrypoint official checker: 300/300 observations answered on time,
  zero late/dropped/wrong-tick/bad lines. PASS.
- Pinned-to-one-CPU native checker with 512 MiB address-space limit:
  ready 1313 ms; p50 1.5 ms, p90 3.6 ms, p99 8.1 ms, max 10.7 ms.
- Constructed stress case, 15 enemy records + 128 projectiles on the real map:
  120 decisions, p50 8.90 ms, p99 22.61 ms, max 27.36 ms; zero over 40/50 ms.
  Peak host-reported RSS 19400 KiB. This is not a match or empirical strength test.
- Flat-ground closed-loop motion sanity check: a pickup 60 m away was approached
  and a take action issued at distance 3.81 m after 6.3 s in the approximate model.
  This checks progress/no oscillation in one simple scene, not official physics.
- Native process command: prlimit --as=536870912 --nproc=128 -- taskset -c 0 python3 -B -u bot.py
- Docker was unavailable locally. Native checks do not reproduce read-only mounts,
  network isolation, the full container CPU quota or UID 65534. Scorer build/check
  remains the complete container validation. See README for Docker command.
- No V7 practice score, no complete local match simulator, and no claim that V7
  outperforms the previous best 2.06 or the user's reported leaderboard best 5.58.
- Applying the packaged update to a committed V6 fixture succeeded; every output
  file matched the candidate, the V6-only test was removed, HEAD/remote stayed
  unchanged, all 73 tests passed and the installed entrypoint checker passed
  (300/300 on time, max 6.2 ms on that run). A dirty tree was refused unchanged.
