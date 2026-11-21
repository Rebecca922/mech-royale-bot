# Mech Royale — V6 finishing reload experiment

MIT-licensed contest bot. Runtime baseline: the original V4 online SARSA bot,
practice score 2.06 (submitted commit 76ed68b4e169). This candidate has no scored result yet.

The only runtime behavior change against V4 is the early-reload guard described
in V6_NOTES.md. V5's incoming-damage regression is removed and V4's original
SARSA updates and parameters are restored. The protocol-reported bot name stays
“Oval Sentinel v4 RL”; use the submitted commit SHA to identify this experiment.

Run locally from the repository root:

```sh
python3 -B -m unittest discover -s . -v
python3 check/check.py -- python3 -B -u bot.py
```

The second command checks JSON-lines conformance, not game strength.
For the scorer-equivalent Docker command and runtime mounts, see the starter
README bundled with the official kit. Native checks do not replace container validation.

Read START_HERE.md, V6_NOTES.md and V6_VALIDATION.md for the experiment and limits.
The original official kit supplies mechfight.py, arena, protocol and checker;
our earlier V1–V5 code is reused from this team's Git history. No pretrained model
or third-party policy is included. Python standard library only. See LICENSE.
