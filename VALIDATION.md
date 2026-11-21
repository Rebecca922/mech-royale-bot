# Validation of the first submission candidate

These are development checks, not tournament results.

| Check | Result |
| --- | --- |
| Official protocol check, 300 recorded observations | PASS; zero late answers, wrong ticks or bad lines |
| Same check with bot pinned to one CPU and 512 MiB address-space limit | PASS; zero late answers; median 1.7 ms, p99 7.3 ms, maximum 10.3 ms |
| Geometry and decision regression tests | 14 passed |
| Synthetic crowd: 15 mechs, 32 projectiles, 16 weapon combinations | Schema-valid finite outputs in all scenarios; maximum decision 20.79 ms on this host |
| Approximate process peak resident memory during recorded stream | 16.6 MiB before final minor revisions; not a container measurement |
| Uploaded protocol versus kit protocol | Identical |
| Docker build and fully sandboxed container check | NOT RUN: Docker is not installed in the development environment |
| Real matches / practice leaderboard | NOT RUN: requires tournament submission |

The CPU affinity and virtual-memory check does not reproduce Docker CPU quotas,
read-only mounts, the exact scorer hardware, or UID 65534. Development ran as
an unprivileged UID 1000 user. Timing measurements are host-specific, not a
guarantee of scorer latency. Run the Docker command in README.md before submitting
when Docker is available; the scorer also performs its own conformance check.

Base-image registry digest lookup was blocked by the development network. The
Dockerfile fixes the Python version and Debian variant by tag. No packages are
installed and no code is downloaded during the image build.
