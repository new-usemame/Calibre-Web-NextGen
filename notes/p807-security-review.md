# CWNG #908 — Auth/session security review

Re-reviewed current `HEAD` (`cf51774f4`) on 2026-07-15 after the hostile-prefix correction. No production files were changed during this re-review; this re-review updates only this note.

## Gate result

**Clean for the reviewed hostile-prefix redirect gate.**

**OBSERVED:** both redirects in scope now call the same sanitized, app-owned URL constructor:

| Redirect | Current call site | Result with `SCRIPT_NAME=//evil.example` |
| --- | --- | --- |
| Preferred classic index | `cps/web.py:1331-1332` | `/app/` |
| Preferred anonymous login | `cps/web.py:2651-2655` | `/app/` |

`spa.spa_shell_url()` returns `f"{_mount_prefix()}/app/"` at `cps/spa.py:152-161`. `_mount_prefix()` accepts only the strict path-prefix allowlist and returns `""` for an invalid prefix (`cps/spa.py:61-79`). Therefore neither reviewed redirect reflects a hostile prefix or `next` as its destination.

## Change since the prior review

**OBSERVED:** `git diff 55a8e8a4f..cf51774f4` contains two files, 18 additions and 2 deletions:

* `cps/web.py`: the classic-index preferred-UI redirect changed from `url_for("spa.spa_shell")` to `spa.spa_shell_url()` (`cps/web.py:1331-1332`).
* `tests/unit/test_739_sticky_new_ui.py`: the minimal classic-index wiring was updated to match production and `test_classic_index_redirect_rejects_hostile_proxy_prefix` was added (`tests/unit/test_739_sticky_new_ui.py:227-240`). It asserts a `302` with exactly `Location: /app/` for `SCRIPT_NAME=//evil.example`.

The previous High classic-index scheme-relative redirect is therefore remediated at current HEAD. The new regression test directly exercises the hostile-prefix result; the production call-site equivalence is additionally established by source inspection. The test fixture is a minimal Flask mirror of the index route, so full authenticated classic-index dispatch is **ASSUMED**, not independently end-to-end exercised here.

## Focused regression evidence

**OBSERVED:** ran:

```
pytest -q tests/unit/test_739_sticky_new_ui.py tests/unit/test_571_reverse_proxy_prefix.py
```

Result: **44 passed in 0.51s**.

Coverage relevant to this gate includes:

* preferred classic index rejects `//evil.example` (`tests/unit/test_739_sticky_new_ui.py:227-240`);
* anonymous login rejects four hostile prefixes, including `//evil.example`, `/../evil.example`, whitespace, and quote/script input (`tests/unit/test_739_sticky_new_ui.py:355-375`);
* anonymous login preserves a valid `/cwa` mount and ignores supplied `next` (`tests/unit/test_739_sticky_new_ui.py:305-334`);
* reverse-proxy asset/prefix tests cover direct and `X-Script-Name` subpath handling plus malformed-prefix rejection (`tests/unit/test_571_reverse_proxy_prefix.py`).

## Verified flow

| Flow | Evidence |
| --- | --- |
| Write | **OBSERVED:** `GET /app` stamps only `cwng_prefer_spa=1`, one-year lifetime, at the sanitized mount path; `Secure` and `SameSite` mirror session configuration; it is deliberately non-HttpOnly. `cps/spa.py:100-113`; session defaults `cps/__init__.py:75-83`. |
| Preferred classic index | **OBSERVED:** an SPA-enabled HTML request with cookie value exactly `1` uses the sanitized app-owned shell URL. `cps/web.py:1319-1334`; `cps/spa.py:124-161`. |
| Preferred anonymous login | **OBSERVED:** after the authenticated-user branch, an SPA-enabled HTML request with the cookie uses the same sanitized app-owned shell URL and ignores `next`. `cps/web.py:2643-2655`; `cps/spa.py:137-161`. |
| Valid proxy mount | **OBSERVED:** a valid `/cwa` prefix yields `/cwa/app/`; focused test coverage is at `tests/unit/test_739_sticky_new_ui.py:305-334`. |
| Clear | **OBSERVED:** the `cwng_feedback` classic-index path deletes the preference using the same sanitized path helper. `cps/web.py:1323-1326`; `cps/spa.py:116-121`. |
| Logout / authentication | **OBSERVED:** logout clears local authentication state but not the UI-preference cookie; `/app` remains a shell, and `/api/v1/auth/me` returns 401 when unauthenticated. `cps/logout.py:11-24`; `cps/web.py:2831-2844`; `cps/spa.py:195-206`; `cps/api/auth.py:171-175`. |
| Legacy `next` paths | **OBSERVED:** post-login and anonymous-browse logout retain the pre-existing `get_redirect_location` validator. `cps/web.py:2605,2837-2844`; `cps/redirect.py:52-57`. Those paths were not changed by `cf51774f4`. |

## Low residual — root-path preference cookie can affect a subpath instance

**OBSERVED:** the preference cookie is host-only and scoped to `_mount_prefix()` or `/` (`cps/spa.py:91-121`). A root-mounted instance therefore writes `Path=/`; a subpath instance reads the same cookie name (`cps/spa.py:137-149`) and deletes only its own mount path (`cps/spa.py:116-121`). Sibling subpaths such as `/a` and `/b` use distinct paths.

**ASSUMED:** under normal browser cookie path matching, a root `Path=/` preference cookie from one CWNG instance on a host is sent to another instance at `/cwa`; the subpath instance can then select the SPA, while clearing only `Path=/cwa` leaves the root cookie. This requires separate instances on the same host at both `/` and a subpath.

Impact remains limited to the non-authentication UI preference: it can select the SPA and make “Back to classic” appear ineffective while the root cookie remains. It does not grant a session or alter authorization.

## Deliberately not claimed

**ASSUMED:** reverse proxies overwrite or strip client-supplied prefix headers before forwarding. The application-level sanitizer protects these two redirect destinations even if that deployment assumption fails; proxy trust boundaries themselves were not deployed or browser-tested in this re-review.
