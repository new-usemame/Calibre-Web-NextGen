# How a reply is judged

`scripts/progress_tracker.py queue` writes one JSON line per reply window: the
thread, the problem as first reported, what we delivered and the last thing we
said before it landed, and every comment after it up to the next delivery. A
model (or a person) reads each line and answers with one verdict. The verdicts
live in `verdicts.json` next to this file, each with the quote it rests on, and
`attempts.json` is the full ledger behind the chart. Correct a wrong verdict by
editing its entry in `verdicts.json` and running `render` again.

## Whose words count

The person is whoever opened the thread. Another user who had the same problem
counts too when they say whether the fix worked for them. Our own comments
(role `us`) are context only. Upstream maintainers discussing the fork, a
bystander's "+1" and bot output do not count.

## Verdicts

| Verdict | When |
|---|---|
| `fixed` | They say the delivered change works or the problem is gone. "Works now", "confirmed on 4.1.30", "can't reproduce anymore". |
| `content` | They are satisfied with the outcome without saying they tested it: thanks for merging or adopting their PR, "perfect, that's what I needed", agreeing it can be closed. |
| `partial` | Part of it works and part is still broken. |
| `not_fixed` | The problem is still there, the fix did not work, or the fix broke something else. |
| `annoyed` | Unhappy with how it was handled even if they say nothing about whether it works: closed too soon, ignored, talked past, unwanted. |
| `none` | Nothing in the replies speaks to whether it worked or how it went: a question, logs with no verdict, "thanks, I'll try it" before trying, an unrelated new problem, chatter from people who don't count. |

When the person changes their mind inside one window ("works", then later
"broke again"), their last word decides. A new, different bug reported
alongside "this one works" is still `fixed`.

## Answer format

One JSON object per line:

```json
{"id": "issue#123@2026-07-01T12:00:00Z", "hash": "0a1b2c3d4e5f6789", "verdict": "fixed",
 "quote": "Confirmed, works on 4.1.32. Thanks!", "evidence_url": "https://github.com/.../issues/123#issuecomment-1",
 "by": "model-or-person"}
```

`id` and `hash` are copied from the queue line; `hash` lets `render` notice when
a thread gained new replies after it was judged. `quote` is the shortest exact
text that decides the verdict (empty for `none`), and `evidence_url` is the
comment it came from.
