# How AI is used in this project

Asked for in [#1631](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1631). It's a fair
question and it deserves a permanent answer rather than a reply buried in a thread.

**Short version: most of the code you run was written by AI; none of it calls one at runtime.**

Those are two different questions and it is worth keeping them apart, because answering only the
second one is misleading. *Authorship*: the majority of this fork's own code, tests and prose are
AI-written. *Runtime*: the shipped application has no model dependency, makes no inference call, and
sends your library nowhere. If AI-authored code is the thing you are worried about, the runtime
answer is not a reassurance and is not offered as one.

## In the software you run: none

- No AI or LLM dependency in `requirements.txt` or `optional-requirements.txt`.
- No inference API is called from anywhere in `cps/`.
- No telemetry, and no AI feature.

Your library, your metadata and your reading data are not sent to a model by this application. You
can check this yourself — those are two files and one directory, and the repository is public.

## In development: most of it

- **Tracker automation.** Intake acknowledgements, labelling and follow-up nudges are posted by
  automation. They carry an HTML marker such as `<!-- cwng:intake-ack -->` in the comment source, so
  you can always tell one from a written reply.
- **Patches and tests.** The majority of this fork's own fixes are written by an AI assistant
  working from a written brief, including the regression tests that accompany them.
- **Bookkeeping.** Upstream backports, changelog entries, release notes and translation refreshes.
- **Issue replies.** Substantive replies are drafted the same way, from the actual code, and go out
  under the maintainer's name because the maintainer owns what they say.

## What gates it

Not "a human reads every line" — that would be the untrue version of this page. The actual gates:

1. **Full CI on the change's own head** — unit tests, Docker integration tests, frontend build.
2. **A regression test that is verified to fail without the fix.** Not merely one that passes with
   it. This is the check an AI cannot satisfy by being confident, which is why it is the one relied
   on most.
3. **A human merges anything from an outside contributor.** Community PRs are never merged
   automatically, regardless of how green they are.
4. **A human merges anything that adds a dependency, changes a licence, or introduces an external
   service URL.** Automation is not permitted to do any of those.
5. Anything that cannot meet the above is labelled `needs-review` and waits for a person.

## Where this has failed

v4.1.37 shipped a startup repair task that could never finish, which put some users into a container
restart loop ([#1696](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1696)). It was found
by a user, not by the process. The fix for it was itself sent back once by an independent review
pass that found the completion marker could silently fail to save.

That is the honest shape of the risk: this process catches a great deal and it does not catch
everything. Patch releases stay small and `:vX.Y.Z` tags are immutable so you can pin or roll back,
and reports like #1696 are why regressions get found at all.

## Verifying any of this

The repository is public, every merge is a squash commit naming its PR, and CI runs are public. If
something here stops being accurate, that is a bug in this page — please open an issue.
