# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The OpenRouter client: the only part of Reflow that leaves the machine.

Three constraints are structural rather than stylistic.

*Explicit timeouts, always.* The app runs under gevent. A socket with no deadline
parks a worker until the process dies, and there is no global default that saves us.

*The cap is checked before the request.* A cap enforced after the response has
already billed the user is not a cap. ``edit_page`` reserves against the ledger
first and raises without opening a socket.

*An unparseable answer is an error.* An empty string would sail through a gate that
only counts what is in front of it, and the page's text would be silently deleted.
"""

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from .prompts import PROMPT_VERSION, structure_prompt, user_prompt

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Date the per-page and per-token prices below were measured (RESEARCH §3.4-3.6).
#: Prices move; a table with no date is a table nobody will ever revisit.
PRICE_TABLE_MEASURED = "2026-09-12"

#: Reflow identifies itself to OpenRouter so the spend is attributable.
REFERER = "https://github.com/new-usemame/Calibre-Web-NextGen"
TITLE = "Calibre-Web-NextGen Reflow"

RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)


@dataclass
class ModelSpec(object):
    model_id: str
    price_per_page: float          # measured $/page on a dense page with its raster
    prompt_usd_per_mtok: float
    completion_usd_per_mtok: float
    label: str = ""
    supports_images: bool = True


#: Measured on the acceptance book's densest pages, image + text, one page per call.
TIERS = {
    "cheap": ModelSpec("qwen/qwen3-vl-32b-instruct", 0.0009, 0.20, 0.60,
                       label="Cheapest"),
    "standard": ModelSpec("deepseek/deepseek-v4.1-flash", 0.0022, 0.28, 0.88,
                          label="Standard"),
    "quality": ModelSpec("openai/gpt-5.6-luna", 0.0031, 0.45, 1.60,
                         label="Best quality"),
}
DEFAULT_TIER = "standard"

_JSON_LINE = re.compile(r'^\s*\{\s*"uncertain"\s*:.*\}\s*$', re.M)
_HTML_TAG = re.compile(r"<\s*(h[1-6]|p|blockquote|aside|figure|table|ul|ol)\b", re.I)
_FENCE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.S)


class ModelError(Exception):
    """The provider refused, or answered something we cannot use."""


class CapExceeded(Exception):
    """Re-exported so callers need not import the ledger to catch the cap."""


try:  # keep one exception type across the package
    from .ledger import CapExceeded as _LedgerCapExceeded
    CapExceeded = _LedgerCapExceeded  # noqa: F811
except ImportError:  # pragma: no cover
    pass


@dataclass
class ModelResult(object):
    html: str
    uncertain: list = field(default_factory=list)
    notes: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_source: str = "price_table"
    attempts: int = 1
    prompt_version: str = PROMPT_VERSION
    dry_run: bool = False

    def to_dict(self):
        return {"model": self.model, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cost_usd": round(self.cost_usd, 6), "cost_source": self.cost_source,
                "attempts": self.attempts, "uncertain": self.uncertain,
                "notes": self.notes, "prompt_version": self.prompt_version,
                "dry_run": self.dry_run}


class OpenRouterClient(object):
    """One page in, one structure-edited HTML fragment out."""

    def __init__(self, api_key, tier=DEFAULT_TIER, timeout=(10.0, 180.0),
                 max_retries=3, backoff=1.5, dry_run=False, session=None,
                 model_id=None):
        self._api_key = api_key or None
        self.tier = tier if tier in TIERS else DEFAULT_TIER
        self.spec = TIERS[self.tier]
        self.model_id = model_id or self.spec.model_id
        self.timeout = tuple(timeout)
        self.max_retries = int(max_retries)
        self.backoff = float(backoff)
        self.dry_run = bool(dry_run) or not self._api_key
        self._session = session

    # A key in a repr ends up in a traceback, a log line and a task message.
    def __repr__(self):
        return "<OpenRouterClient %s tier=%s configured=%s dry_run=%s>" % (
            self.model_id, self.tier, bool(self._api_key), self.dry_run)

    __str__ = __repr__

    def describe(self):
        return {"model": self.model_id, "tier": self.tier,
                "configured": bool(self._api_key), "dry_run": self.dry_run,
                "prompt_version": PROMPT_VERSION,
                "priced_on": PRICE_TABLE_MEASURED}

    @property
    def configured(self):
        return bool(self._api_key)

    def price_per_page(self):
        return self.spec.price_per_page

    # ------------------------------------------------------------------- calling

    def edit_page(self, page_text, image_jpeg=None, ladder=(1, 2, 3, 4), hints=None,
                  page_label=None, ledger=None, max_tokens=4096):
        """Ask the model to mark up one page. Words in, the same words out."""
        if ledger is not None:
            ledger.reserve(self.spec.price_per_page)

        if self.dry_run:
            return self._dry_run_result(page_text)

        payload = self._payload(page_text, image_jpeg, ladder, hints, page_label,
                                max_tokens)
        data, attempts = self._post(payload)
        return self._parse(data, attempts)

    def _payload(self, page_text, image_jpeg, ladder, hints, page_label, max_tokens):
        content = [{"type": "text",
                    "text": user_prompt(page_text, ladder=ladder, hints=hints,
                                        page_label=page_label)}]
        if image_jpeg:
            content.insert(0, {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + _b64(image_jpeg)}})
        return {
            "model": self.model_id,
            "messages": [{"role": "system", "content": structure_prompt()},
                         {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": int(max_tokens),
            "usage": {"include": True},
        }

    def _post(self, payload):
        session = self._session or requests
        headers = {"Authorization": "Bearer %s" % self._api_key,
                   "Content-Type": "application/json",
                   "HTTP-Referer": REFERER, "X-Title": TITLE}
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = session.post(OPENROUTER_URL, json=payload, headers=headers,
                                        timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = "network error: %s" % exc
                if attempt == self.max_retries:
                    raise ModelError(last_error)
                self._sleep(attempt, response=None)
                continue

            if response.status_code == 200:
                try:
                    return response.json(), attempt
                except ValueError:
                    raise ModelError("the provider returned a non-JSON body")

            detail = _error_detail(response)
            if response.status_code not in RETRY_STATUS or attempt == self.max_retries:
                raise ModelError("OpenRouter %s: %s" % (response.status_code, detail))
            last_error = detail
            self._sleep(attempt, response)
        raise ModelError(last_error or "the request could not be completed")

    def _sleep(self, attempt, response):
        delay = self.backoff * (2 ** (attempt - 1))
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    delay = max(delay, float(header))
                except ValueError:
                    pass
        if delay > 0:
            # Jitter so a queue of pages does not retry in lockstep.
            time.sleep(delay * (0.75 + random.random() * 0.5))

    # ------------------------------------------------------------------ answering

    def _parse(self, data, attempts):
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ModelError("the provider's reply had no message content")

        content = _FENCE.sub("", content).strip()
        uncertain, notes, html = _split_contract(content)
        if not _HTML_TAG.search(html):
            raise ModelError("the model answered prose instead of an HTML fragment: %s"
                             % html[:160].replace("\n", " "))

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost, source = self._cost(usage, prompt_tokens, completion_tokens)

        return ModelResult(html=html, uncertain=uncertain, notes=notes,
                           model=data.get("model") or self.model_id,
                           prompt_tokens=prompt_tokens,
                           completion_tokens=completion_tokens,
                           cost_usd=cost, cost_source=source, attempts=attempts)

    def _cost(self, usage, prompt_tokens, completion_tokens):
        reported = usage.get("cost")
        if reported is not None:
            try:
                return float(reported), "provider"
            except (TypeError, ValueError):
                pass
        table = (prompt_tokens * self.spec.prompt_usd_per_mtok
                 + completion_tokens * self.spec.completion_usd_per_mtok) / 1e6
        return table, "price_table"

    def _dry_run_result(self, page_text):
        """Enough shape for the pipeline to run end to end with no key and no spend."""
        body = (page_text or "").strip()
        return ModelResult(html="<p>%s</p>" % body, uncertain=[], notes="dry run",
                           model=self.model_id, cost_usd=0.0, cost_source="dry_run",
                           dry_run=True)


def _split_contract(content):
    """Peel the required trailing JSON line off the HTML fragment."""
    match = None
    for match in _JSON_LINE.finditer(content):
        pass  # the contract says LAST line; take the last match
    if match is None:
        return [], "", content.strip()
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return [], "", (content[:match.start()] + content[match.end():]).strip()
    html = (content[:match.start()] + content[match.end():]).strip()
    uncertain = parsed.get("uncertain") or []
    if isinstance(uncertain, dict):
        uncertain = [uncertain]
    return list(uncertain), str(parsed.get("notes") or ""), html


def _error_detail(response):
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:200]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:200]
    return str(error or body)[:200]


def _b64(data):
    import base64

    return base64.b64encode(data).decode("ascii")


def tier_choices():
    """What the Reflow page shows in its cost card."""
    return [{"tier": name, "model": spec.model_id, "label": spec.label,
             "price_per_page": spec.price_per_page}
            for name, spec in sorted(TIERS.items(),
                                     key=lambda kv: kv[1].price_per_page)]
