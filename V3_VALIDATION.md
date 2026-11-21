# V3 development validation

| Check | Result |
| --- | --- |
| Original and new regression tests | 27 passed |
| Official 300-observation check, bot pinned to one CPU with 512 MiB address-space cap | PASS; all 300 timely; p50 1.2 ms, p99 6.1 ms, max 6.1 ms on this host |
| 16 weapon combinations with 15 enemies and 32 projectiles, CPU affinity to one core | Valid finite schema outputs; max 28.21 ms, mean 21.3 ms on this host |
| Both feature switches disabled | All 300 recorded actions exactly match original V1 actions |
| Docker build / full Docker sandbox | Not run; Docker unavailable here |
| Real V3 matches / leaderboard result | Not run; requires user submission |

CPU affinity is not a Docker CPU quota. The address-space cap is not the same
as the container memory limit. Tests ran as an unprivileged UID 1000 user,
not UID 65534. Host measurements do not guarantee scorer timing.
The recorded observations do not react to actions, so they prove conformance
and the tested behaviors rather than strength in real games.
User-reported original V1 practice score: 1.73. No V3 score is claimed.
