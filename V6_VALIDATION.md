# V6 validation

- 59 unit tests passed (48 original V4 tests + 11 finishing/reload tests).
- Official checker: 300/300 observations answered, zero late answers, wrong ticks,
  bad lines or dropped stale answers; PASS.
- Checker command: python3 check/check.py -- prlimit --as=536870912 --nproc=128 -- taskset -c 0 python3 -B -u bot.py
- Recorded candidate check: p50 1.4 ms, p90 4.0 ms, p99 6.2 ms, max 6.7 ms;
  ready in 1196 ms. Host-specific figures, not promised scorer timing.
- This is a CPU-affinity and address-space-limited native process check.
  Docker was unavailable here; read-only mounts, network isolation, UID 65534
  and the complete scorer container limits have not been reproduced locally.
- Original V4 runtime/config files are byte-identical except bot.py's helper
  and reload guard; display name intentionally unchanged.
- Protocol confirmation: weapon stats include upgrades; cube damage is an
  additional multiplier. Enemy armor and missed shots remain unknown.
- No reactive match simulator or scored V6 result is available. This validation
  does not establish an improvement over V4's 2.06.
- Update integration against a committed V5 fixture passed: dirty-tree and
  customized-file refusal without mutations, injected write/deletion failures
  with byte-for-byte rollback, unchanged HEAD and remote, removal of only the
  three intended V5 files, exact candidate installation, 59 tests and checker PASS.
- Checker after applying the update: 300/300 on time; max 7.3 ms on this host.
