# V5-SL development validation

| Check | Result |
| --- | --- |
| Geometry, tactics, RL and supervised-model tests | 62 passed |
| Official 300-observation check with one-core affinity and 512 MiB virtual-address cap | PASS; all timely; p50 1.4 ms, p99 6.6 ms, max 6.8 ms on this host |
| 16 synthetic crowd/weapon cases, 15 mechs and 32 projectiles | Schema-valid finite outputs; max 33.52 ms, mean 22.1 ms on this host |
| Supervised hook disabled | All 300 recorded actions exactly match V4-F |
| Label exclusions, gap handling and terminal-window handling | Passed regression tests |
| Prediction scored before fitting current label | Passed chronological-evaluation test |
| A calibrated risk estimate can change the tactical decision | Passed controlled integration test |
| RL updates remain frozen | Passed integration check |
| Docker build / full Docker sandbox | Not run; Docker unavailable here |
| Real V5 matches / leaderboard | Not run; user submission required |
| Offline pretraining | Not performed; no pretrained artifact supplied |

Recorded observations do not respond to actions. Synthetic tests and the
teaching demo are not a game simulator, and do not establish match strength.
CPU affinity/address-space limits differ from Docker CPU and memory accounting;
UID 1000 development also differs from scorer UID 65534. Host timing is not a
latency guarantee. Online labels can be noisy and samples are selected by the
current policy and by which observations arrive; no causal or convergence
claim is made. Each match resets the calibration statistics.

## Recorded-trajectory prediction audit (negative result)

On the unchanged official recording, the label pipeline collected 12 eligible
windows and skipped 3 observation gaps. Before-update mean squared error was
17857.36 for the baseline and 18896.56 for the calibrated forecasts (DPS squared).
The calibration did not improve this small recorded example. The ending factor
was approximately 1.0223. These are not results from a reactive game controlled
by V5, not independent held-out match evaluation, and no trained parameter was
exported from this recording into the submitted bot. No calibration parameters
were tuned to force an improvement on this recording.
