# Correction: the "tier-1 regex wedges findings PRs" diagnosis was wrong

`notes/QUEUE-findings-to-file.md` (raised by the 2026-08-07T00:12Z tick, deleted
by this one) concluded that findings PRs wedge because
`TIER1_PATHS_REGEX='\.(po|pot|md)$|^README'` cannot match
`findings/items/F-XXXXXX.json`. Both of its findings have now been filed to the
ledger, but the first one's *diagnosis* did not survive checking.

Two things are wrong with it:

1. **The regex enforces nothing.** It is declared, parsed onto `Policy`, echoed
   by `findings.py show`, and asserted in tests — and read by no workflow, no
   shell script, and not by `validate_fork_pr`. A `safe-tier-1` PR touching any
   path validates `ok`. Filed as F-79d746.
2. **#1423 is blocked by a merge conflict**, not a label. `mergeable=CONFLICTING,
   mergeStateStatus=DIRTY`, green on every required check. The conflict is in
   `findings/INDEX.md`, which `findings.py` regenerates wholesale and commits, so
   any two findings branches cut from the same main collide. Filed as F-6e4ea6.

How it was caught, because the mechanism matters more than the conclusion: the
widening fix was written and a test added for it. One of the two new tests went
red without the fix and the other stayed green — `validate_fork_pr` passed a
findings PR under the *old* regex. A test that cannot go red is not evidence,
and here it was the only thing standing between a plausible story and a shipped
change that fixed nothing. See the memory rule "red tests must be red for the
right reason".

The proposed regex change was reverted. Nothing in the ledger's own storage
design is at fault — per-item JSON merges cleanly, as its docstring promises.
The index is what re-introduces the contention.
