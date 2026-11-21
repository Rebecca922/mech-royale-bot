# V4 RL development validation

| Check | Result |
| --- | --- |
| Geometry, tactics, SARSA, reward accounting and terminal transport tests | 48 passed |
| Official 300-observation conformance check, CPU affinity to one core and 512 MiB address-space cap | PASS; all 300 timely; p50 1.5 ms, p99 8.2 ms, max 13.4 ms on this host |
| 16 synthetic weapon/crowd cases, 15 enemies and 32 projectiles, one-core affinity | Valid finite outputs; max 36.47 ms, mean 28.07 ms on this host |
| RL disabled | All 300 recorded actions exactly match V3 |
| Negative feedback changes Q and later choice | Passed implementation test using controlled synthetic transitions |
| Terminal return accounting | Passed ties, time-limit rank correction and initial skipped progress cases |
| Updates frozen | Q values do not change; test passed |
| Wrapper end callback | Delivered once without extra protocol stdout; test passed |
| Toy feedback demo | Initial ACCEPT switches to CAUTIOUS after negative feedback; no model saved |
| Docker build / full Docker sandbox | Not run; Docker unavailable here |
| Real V4 matches / leaderboard | Not run; user submission required |
| Offline pretraining on real game trajectories | Not performed; no interactive simulator or exported trajectories available |

The official conformance replay does not respond to actions. Its results do not
demonstrate an improvement in match performance. Controlled transition tests
and demo_sarsa.py test/teach the algorithm and are not a game simulator.

CPU affinity and a virtual-address-space cap do not reproduce Docker CPU quotas,
container memory accounting, scorer hardware, read-only mounts or UID 65534.
Development runs as unprivileged UID 1000. Host latency is not a scorer guarantee.

User-reported baselines: V1 1.73, V3 1.85. V4 is an experimental online learner;
its values reset for each new bot instance, so it can worsen performance and
does not accumulate experience over the practice batch or repeated submissions.
