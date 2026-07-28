# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for the README's "Reverse proxy with a prefix" section
(added in #1152 by @chloeroform, hardened here).

The section tells users to paste an nginx `location` block. As originally
written it ended with `include proxy_params;`, which is a Debian/Ubuntu
*package* file — it is not part of upstream nginx and is absent from the
official `nginx` and `nginx:alpine` Docker images. Verified 2026-07-28:

    $ docker run --rm -v ./nginx.conf:/etc/nginx/nginx.conf:ro nginx:alpine nginx -t
    nginx: [emerg] open() "/etc/nginx/proxy_params" failed (2: No such file
    or directory) in /etc/nginx/nginx.conf:8
    nginx: configuration file /etc/nginx/nginx.conf test failed

nginx does not start at all — so a user who follows the README on a
containerised nginx (the deployment style the rest of this README assumes)
takes their whole proxy down, and the failure points at nginx rather than at
the docs that caused it. The repo's own reference config,
`examples/nginx-reverse-proxy.conf`, already writes the headers out inline;
the README now matches it.

These tests pin the portability invariant, not the prose:

* the documented snippet carries the forwarding headers inline
* it does not depend on a distro-only `include`
* `PROXY_SCRIPT_NAME` is documented without a trailing slash, because
  `ReverseProxied.__call__` assigns it straight to `SCRIPT_NAME` and WSGI
  builds URLs as `SCRIPT_NAME + PATH_INFO` (`cps/reverseproxy.py`)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"

HEADING = "### Reverse proxy with a prefix"

# Headers Debian's proxy_params provides, which the snippet must supply itself.
REQUIRED_HEADERS = (
    "proxy_set_header Host",
    "proxy_set_header X-Real-IP",
    "proxy_set_header X-Forwarded-For",
    "proxy_set_header X-Forwarded-Proto",
)


@pytest.fixture(scope="module")
def readme() -> str:
    assert README.exists(), f"README.md is missing at {README}"
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prefix_section(readme: str) -> str:
    """The body of the prefix section, up to the next same-level heading."""
    start = readme.find(HEADING)
    assert start != -1, (
        f"README no longer contains {HEADING!r}. If the section was renamed, "
        f"update this test — but the prefix deployment recipe must stay "
        f"documented somewhere; it is one of the most common support asks."
    )
    rest = readme[start + len(HEADING):]
    end = rest.find("\n### ")
    return rest if end == -1 else rest[:end]


@pytest.fixture(scope="module")
def nginx_block(prefix_section: str) -> str:
    """Just the fenced nginx snippet — the part users copy and paste.

    Scoped deliberately: the surrounding prose *names* `include proxy_params;`
    to explain why it is avoided, so a whole-section substring check would
    flag the explanation as the defect it warns about.
    """
    blocks = re.findall(r"```[a-z]*\n(.*?)```", prefix_section, re.DOTALL)
    for block in blocks:
        if "location " in block and "proxy_pass" in block:
            return block
    raise AssertionError(
        "The prefix section must contain a fenced nginx block with a "
        "`location` + `proxy_pass` pair — that snippet is the recipe."
    )


@pytest.mark.unit
class TestPrefixProxySnippetIsPortable:
    def test_snippet_does_not_include_distro_only_proxy_params(self, nginx_block):
        assert "include proxy_params" not in nginx_block, (
            "The prefix nginx snippet must not use `include proxy_params;`. "
            "That file ships only with the Debian/Ubuntu nginx package; the "
            "official nginx Docker images do not have it and nginx REFUSES TO "
            "START when an include is missing (emerg, not a warning). Write "
            "the proxy_set_header lines out inline instead, the way "
            "examples/nginx-reverse-proxy.conf does."
        )

    @pytest.mark.parametrize("header", REQUIRED_HEADERS)
    def test_snippet_sets_forwarding_header_inline(self, nginx_block, header):
        assert header in nginx_block, (
            f"The prefix nginx snippet must set `{header}` inline. Without the "
            f"X-Forwarded-* headers CWA cannot reconstruct external URLs, and "
            f"without Host the vhost is wrong."
        )

    def test_proxy_pass_keeps_trailing_slash(self, nginx_block):
        # `proxy_pass http://host:8083/` (with slash) strips the location
        # prefix. Without it nginx forwards /cwa/foo and the app, which is
        # already mounted at SCRIPT_NAME=/cwa, would look for /cwa/cwa/foo.
        assert re.search(r"proxy_pass\s+http://[^\s;]+/;", nginx_block), (
            "The documented proxy_pass must end in a trailing slash so nginx "
            "strips the location prefix before forwarding."
        )


@pytest.mark.unit
class TestPrefixEnvVarGuidance:
    def test_script_name_documented_without_trailing_slash(self, prefix_section):
        values = re.findall(r"PROXY_SCRIPT_NAME=(\S+)", prefix_section)
        assert values, (
            "The section must show a concrete PROXY_SCRIPT_NAME=... example; "
            "it is the half of the recipe that lives on the CWA side."
        )
        for value in values:
            assert not value.endswith("/"), (
                f"PROXY_SCRIPT_NAME={value} has a trailing slash. "
                f"ReverseProxied assigns the value straight to SCRIPT_NAME and "
                f"WSGI builds every URL as SCRIPT_NAME + PATH_INFO, so a "
                f"trailing slash yields doubled-slash URLs like /cwa//book/1."
            )

    def test_section_is_linked_from_the_table_of_contents(self, readme):
        assert "#reverse-proxy-with-a-prefix" in readme, (
            "The section must stay reachable from the README table of "
            "contents — it is long-document content users navigate to."
        )
