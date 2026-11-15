# Dev/release image builds failing: python-build-standalone 404 from Actions

**Status as of 2026-06-28 ~12:50 ET:** every GHCR image build (dev + release)
fails. the household host is stuck on the 06-27 `:dev` image (no new UI banner) because
no newer `:dev` ever published.

## Symptom
`[dependencies 3/8]` Python install step dies:
```
curl: (22) The requested URL returned error: 404
URL: https://github.com/astral-sh/python-build-standalone/releases/download/<rel>/cpython-<ver>+<rel>-<arch>-install_only.tar.gz
```

## Root cause (evidence-based, NOT our code)
GitHub-hosted Actions runners get a hard **404 from the GitHub release-asset CDN
(`objects.githubusercontent.com`)** for this download. Confirmed differential:
- Same URL returns **200** from the operator's machine.
- In the SAME Docker build, the **lsof** download from `codeload.github.com`
  (source archive) **succeeds** — only the **release-asset** download 404s.
- GitHub status: "All systems operational", no incident posted.

## Ruled out (with evidence)
- ❌ Billing/quota — repo is **public → Actions free**; jobs run, only the
  download dies (quota failures block job start, not mid-build).
- ❌ self-hosted runner host — builds run on GitHub-hosted
  `ubuntu-latest` + `ubuntu-24.04-arm`.
- ❌ Stale/missing asset — bumped pin 20260414/3.13.13 → 20260623/3.13.14; both
  200 elsewhere.
- ❌ Intermittent — 10× retry loop, every attempt 404.
- ❌ Rate-limit/auth — `GITHUB_TOKEN` Bearer auth: zero difference.
- ❌ IPv6 — `curl -4`: zero difference.

## Already merged (non-fragile resilience, keep these)
- `#544` pin bump to 20260623 / 3.13.14.
- `#545` 10-attempt retry loop with `--retry-all-errors` (rides out transient
  CDN blips automatically — the durable safeguard).
- `#546` optional `GITHUB_TOKEN` BuildKit secret + Bearer header on the download.

## Conclusion
This is a **GitHub-side release-CDN delivery failure to the Actions egress** —
the kind that usually clears on its own in hours. The retry loop is the correct
non-fragile fix for that. **When GitHub recovers, re-run the dev build (or the
next merge auto-rebuilds) and `:dev` publishes normally.**

Re-run: `gh run list --repo new-usemame/Calibre-Web-NextGen --workflow "Build & Push - Dev - Split Strategy" --limit 1` then `gh run rerun <id> --failed`,
or dispatch on a ref: `gh workflow run docker-image-build-dev.yml --ref main`.

## If it does NOT recover (escalation options — each has a tradeoff)
Operator flagged: avoid regressions + fragile/rigid deterministic processes.
1. **Runner-side fetch** — `gh release download astral-sh/python-build-standalone`
   in each arch job (uses api.github.com, which works from runners), COPY the
   tarball into the build with the curl path kept as fallback. Most robust; adds
   build-context coupling.
2. **uv-managed Python** (`uv python install`, `UV_PYTHON_INSTALL_MIRROR`) —
   official astral tooling; changes the venv setup (regression risk).
3. **deadsnakes/apt python3.13** — reverts the documented move off deadsnakes.
