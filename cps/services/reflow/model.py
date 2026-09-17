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
import math
import random
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from urllib3.exceptions import NameResolutionError, NewConnectionError

from .annotate import uncertain_record
from .gate import number_the_notes
from .prompts import PROMPT_VERSION, structure_prompt, user_prompt

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Date the per-page and per-token prices below were measured (RESEARCH §3.4-3.6,
#: re-measured 2026-09-13 against real billing). Prices move; a table with no date is
#: a table nobody will ever revisit.
PRICE_TABLE_MEASURED = "2026-09-13"

#: Reflow identifies itself to OpenRouter so the spend is attributable.
REFERER = "https://github.com/new-usemame/Calibre-Web-NextGen"
TITLE = "Calibre-Web-NextGen Reflow"

#: Retry is reserved for failures that provably never left the machine
#: (pre-dispatch transport errors). Every HTTP response is answered by something
#: that saw the request: a documented request-error rejection is released but
#: pointless to retry unchanged, and anything else is unknown billing -- held,
#: never silently retried as free. So there is no retryable status list any more.

#: The answer is the page's own text again with markup around it, so the room it
#: needs is a function of the page. MEASURED on the acceptance book: a dense page of
#: 2,604 characters came back in 947 completion tokens once the model was told not to
#: think out loud. Half a token per character plus a floor for the markup leaves room
#: for the worst page in the book without ever being the reason a page is refused.
COMPLETION_TOKENS_FLOOR = 2048
COMPLETION_TOKENS_CEILING = 16384
COMPLETION_TOKENS_PER_CHAR = 0.5


def completion_budget(page_text):
    """How much room to leave for one page's answer."""
    needed = COMPLETION_TOKENS_FLOOR + int(len(page_text or "") * COMPLETION_TOKENS_PER_CHAR)
    return max(COMPLETION_TOKENS_FLOOR, min(COMPLETION_TOKENS_CEILING, needed))


def _utf8_bytes(text):
    return len((text or "").encode("utf-8"))


def _jpeg_dimensions(data):
    """(width, height) of a JPEG, read off its SOF marker. None when unreadable.

    The image's token price is a function of its pixels on every upstream that
    bills by token, so the bound reads the real dimensions of the bytes that will
    actually be sent rather than trusting what the caller meant to render.
    """
    try:
        if not data or len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
            return None
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker == 0xD8 or marker == 0xD9 or 0xD0 <= marker <= 0xD7:
                index += 2                       # payload-free markers
                continue
            length = (data[index + 2] << 8) + data[index + 3]
            if length < 2:
                return None
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height = (data[index + 5] << 8) + data[index + 6]
                width = (data[index + 7] << 8) + data[index + 8]
                return (width, height) if width and height else None
            index += 2 + length
    except (IndexError, TypeError):
        return None
    return None


def image_token_bound(image_jpeg):
    """The most tokens the image half of a request can be billed for.

    Pixels over 750, doubled -- see the accounting note above -- with a floor for
    providers that rescale small images up, and the pipeline's largest legal raster
    for an image whose own dimensions could not be read.
    """
    dimensions = _jpeg_dimensions(image_jpeg)
    if dimensions is None:
        return UNKNOWN_IMAGE_TOKENS
    width, height = dimensions
    tokens = IMAGE_TOKEN_HEADROOM * (-(-width * height // IMAGE_PIXELS_PER_TOKEN))
    return max(IMAGE_TOKEN_MINIMUM, tokens)


#: What one page of a real book costs in tokens. MEASURED 2026-09-13 on the
#: acceptance book: prompt 1,878-2,029 (the page's text plus its raster) and
#: completion 892-1,293. These carry about 10% over the worst page measured, and they
#: are what every dollar figure shown to a user is built from. They are ESTIMATES:
#: the cap does not reserve against them, it reserves against the strict bound of
#: each request (see ``request_bound``) -- a measured average is not a ceiling.
PAGE_PROMPT_TOKENS = 2100
PAGE_COMPLETION_TOKENS = 1400

#: How the strict request bound counts its tokens. Every rate a request can be
#: billed at is capped by ``provider.max_price`` -- but max_price limits RATES, not
#: the total, so the bound has to count the most the request can ever contain:
#:
#: * prompt text: one token per UTF-8 byte. The tier models all run byte-level
#:   tokenizers (byte-pair encodings with byte fallback), where merges only ever
#:   reduce the count, so the byte count is a hard ceiling rather than a guess.
#: * prompt scaffolding: a flat allowance per message for role/wrapper tokens.
#: * the image: pixels / 750, doubled. The densest published per-pixel accounting
#:   among vision upstreams is Anthropic's (width x height) / 750; OpenAI's tiling
#:   (85 + 170 per 512px tile after scaling under 2048px) and the Qwen/BytePlus
#:   patch accounting (pixels / 784, with a merge that quarters it) both come out
#:   lower for the rasters this pipeline sends. Doubling the densest one is the
#:   headroom for an upstream that tokenizes more finely or rescales the image
#:   upward to a minimum size. It is a documented bound with a stated provenance,
#:   not an unlimited assumption -- and it is why ``max_price`` also carries the
#:   ``image`` ceiling, so an upstream that prices an image flat instead of by
#:   token cannot bill that mode outside the cap either.
#: * completion: exactly the ``max_tokens`` sent. Reasoning is disabled in the
#:   payload, so the whole output budget is the visible answer; OpenRouter routes
#:   only to providers that can honour the requested length.
#:
#: What remains outside the bound, honestly: a provider that bills MORE tokens than
#: a byte-per-token of the prompt it was sent, or beyond the max_tokens it was
#: given, is outside its own contract -- and a non-200 response is treated as
#: unbilled, which is OpenRouter's documented shape for errors (no completion is
#: produced). The ledger still records the provider's own reported figure when one
#: arrives, so an over-bound bill would be visible in the job's record rather than
#: hidden.
MESSAGE_OVERHEAD_TOKENS = 16
IMAGE_PIXELS_PER_TOKEN = 750
IMAGE_TOKEN_HEADROOM = 2
#: Providers that rescale an image upward to their minimum size make a tiny image
#: cost more than its pixels say. The floor prices that rescale.
IMAGE_TOKEN_MINIMUM = 1024
#: An image whose dimensions cannot be read from its bytes is priced at the largest
#: raster the pipeline is allowed to allocate (extract.MAX_RASTER_PIXELS), at the
#: bound density above: 2 * ceil(4096*4096 / 750) = 44,740.
UNKNOWN_IMAGE_TOKENS = 48000


@dataclass
class ModelSpec(object):
    """A tier: which model, and the most its upstreams are allowed to charge.

    The two rates are not a guess at what routing will pick. They are sent with every
    request as a hard ceiling, so the price the user was quoted is the price they can
    be billed -- see ``_payload``.
    """

    model_id: str
    prompt_usd_per_mtok: float
    completion_usd_per_mtok: float
    label: str = ""
    supports_images: bool = True

    @property
    def price_per_page(self):
        """The expected cost of one page at this tier's ceiling rate.

        This is the ESTIMATE every dollar figure shown to a user is built from. It
        is not the most a page can cost -- a dense page's request permits more
        completion tokens than this counts -- so the cap reserves the strict bound
        of each request instead (``OpenRouterClient.request_bound``).
        """
        return round((PAGE_PROMPT_TOKENS * self.prompt_usd_per_mtok
                      + PAGE_COMPLETION_TOKENS * self.completion_usd_per_mtok) / 1e6, 6)

    @property
    def max_price(self):
        """The ceiling, in the shape OpenRouter's provider router wants."""
        return {"prompt": self.prompt_usd_per_mtok,
                "completion": self.completion_usd_per_mtok}


#: The rates below are ceilings chosen from OpenRouter's per-endpoint price list,
#: MEASURED 2026-09-13 (``/api/v1/models/<slug>/endpoints``), not from the catalogue
#: headline. A model on OpenRouter is not one price: deepseek-v4.1-flash is served by
#: fourteen upstreams between $0.15/$0.60 and $0.375/$1.50 per million tokens, and
#: routing picks one per request. Each ceiling here admits most of that model's
#: upstreams and excludes the dearest -- $0.30/$1.20 leaves thirteen of the fourteen
#: and rules out only Venice, the one that charged 2.9x what the cheapest did for the
#: same page on the same day.
#:
#: The two tiers costing the same is not a slip: Luna's own endpoints sit at
#: $0.22/$1.32, slightly under deepseek's dearest. Which of the two is better at this
#: particular job is a question for a measured comparison, not for a price list.
TIERS = {
    "cheap": ModelSpec("qwen/qwen3-vl-32b-instruct", 0.11, 0.45, label="Cheapest"),
    "standard": ModelSpec("deepseek/deepseek-v4.1-flash", 0.30, 1.20, label="Standard"),
    "quality": ModelSpec("openai/gpt-5.6-luna", 0.22, 1.32, label="Best quality"),
}
DEFAULT_TIER = "standard"

_JSON_LINE = re.compile(r'^\s*\{\s*"uncertain"\s*:.*\}\s*$', re.M)
_HTML_TAG = re.compile(r"<\s*(h[1-6]|p|blockquote|aside|figure|table|ul|ol)\b", re.I)
_FENCE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.S)


class ModelError(Exception):
    """The provider refused, or answered something we cannot use."""


class UnusableAnswer(ModelError):
    """The provider answered, and the answer is not one this page can use.

    Prose where an HTML fragment was asked for, a reply with no message content, an
    answer the provider cut off at the token ceiling. The page keeps its
    deterministic text either way -- that is what the base class means -- but the
    difference matters one level up: a provider that is answering is not a provider
    that has gone away, and ``pipeline`` walks a book away from the model after three
    refusals *in a row* on the theory that something systemic has happened to the
    service. MEASURED on the acceptance book, whose blank leaves carry a raster with
    no ink: the model reads them, says in prose that there is nothing to transcribe,
    and three of those in a row ended a 698-page conversion twenty pages early, with
    the job still reporting itself done.

    It carries what the call cost. The provider answered, so the provider billed --
    an answer cut off at the token ceiling burned the whole completion allowance
    before it was thrown away -- and ``Ledger.spent()`` is the only number the cap
    reserves against. A charge that never reaches the ledger is a charge the reader's
    ceiling cannot stop. MEASURED on the acceptance book: 42 pages over four
    conversions answered in prose or ran into the ceiling and every one was written
    down as free.
    """

    def __init__(self, message, cost_usd=0.0, cost_source="", prompt_tokens=0,
                 completion_tokens=0, attempt=None):
        ModelError.__init__(self, message)
        self.cost_usd = float(cost_usd or 0.0)
        self.cost_source = str(cost_source or "")
        self.prompt_tokens = int(prompt_tokens or 0)
        self.completion_tokens = int(completion_tokens or 0)
        self.attempt = attempt


class CapExceeded(Exception):
    """Re-exported so callers need not import the ledger to catch the cap."""


class UncertainBilling(ModelError):
    """The request was dispatched and its billing state is unknown.

    A timeout, a reset mid-transfer, a lost response, a malformed success or a
    gateway 5xx all share one shape: the provider may have accepted and billed the
    request while we hold no answer. OpenRouter's own error contract says a
    provider that produces no content may still charge prompt processing, so none
    of these is proof of no charge. The strict bound stays held in the ledger as
    unresolved liability, and ``cost_usd`` is None rather than 0: a possible
    charge is never written down as a free call.
    """

    def __init__(self, message, held_usd=0.0, attempt=None):
        ModelError.__init__(self, message)
        self.cost_usd = None
        self.cost_source = "unresolved"
        self.held_usd = float(held_usd or 0.0)
        self.attempt = attempt


class AttemptCancelled(ModelError):
    """The caller cancelled between attempts: nothing new was dispatched.

    Cancelling our wait is not cancelling the provider's already-accepted
    request, so this is only ever raised before an attempt is sent; anything
    already dispatched stays held in the ledger.
    """


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
    #: Which upstream served the call. OpenRouter picks one per request and they do
    #: not all charge the same or answer the same, so a page's cost and its verdict
    #: are only explainable next to this.
    provider: str = ""
    attempts: int = 1
    #: The ledger reservation this answer reconciled, so the page record can
    #: reference the same attempt and the charge is counted exactly once.
    attempt: str = ""
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

    # ------------------------------------------------------- the strict bound

    def request_bound(self, page_text, image_jpeg=None, max_tokens=None,
                      ladder=(1, 2, 3, 4), hints=None, page_label=None, headings=()):
        """The most the request for this page can be billed, in USD.

        The number the cap reserves. max_price caps the RATES, not the total, so
        the bound counts the most the request can contain -- every prompt byte, the
        image's pixels, the full answer allowance -- at those ceiling rates. See the
        accounting note at ``MESSAGE_OVERHEAD_TOKENS`` for what is counted and what
        is honestly outside it.
        """
        max_tokens = int(max_tokens or completion_budget(page_text))
        payload = self._payload(page_text, image_jpeg, ladder, hints, page_label,
                                max_tokens, headings=headings)
        return self._request_bound_for(payload, image_jpeg, max_tokens)

    def _request_bound_for(self, payload, image_jpeg, max_tokens):
        messages = payload.get("messages") or []
        text_bytes = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                text_bytes += _utf8_bytes(content)
            elif isinstance(content, list):
                text_bytes += sum(_utf8_bytes(part.get("text")) for part in content
                                  if part.get("type") == "text")
        prompt_tokens = text_bytes + MESSAGE_OVERHEAD_TOKENS * len(messages)
        if image_jpeg:
            prompt_tokens += image_token_bound(image_jpeg)
        # No rounding: rounding the reservation DOWN would admit a bill past the
        # cap by the rounding, which is the bug this number exists to close.
        return (prompt_tokens * self.spec.prompt_usd_per_mtok
                + int(max_tokens) * self.spec.completion_usd_per_mtok) / 1e6

    # ------------------------------------------------------------------- calling

    def edit_page(self, page_text, image_jpeg=None, ladder=(1, 2, 3, 4), hints=None,
                  page_label=None, ledger=None, max_tokens=None, headings=(),
                  should_stop=None):
        """Ask the model to mark up one page. Words in, the same words out.

        ``headings`` is what the deterministic reader read as a heading on this page
        (``assemble.page_headings``); the model is told rather than asked, and
        ``gate.check_structure`` refuses an answer that marks anything else.

        The cap is reserved against the strict bound of THIS request before any
        attempt leaves the machine -- never against the measured average page,
        which is an estimate, not a ceiling. The reservation is durable and
        per-attempt: an answer lost after dispatch stays held as unresolved
        liability instead of being retried as free.
        """
        if self.dry_run:
            return self._dry_run_result(page_text)

        max_tokens = int(max_tokens or completion_budget(page_text))
        payload = self._payload(page_text, image_jpeg, ladder, hints, page_label,
                                max_tokens, headings=headings)
        bound = self._request_bound_for(payload, image_jpeg, max_tokens)
        data, attempts, attempt_id = self._post(payload, ledger=ledger, bound=bound,
                                                page_label=page_label,
                                                should_stop=should_stop)
        return self._parse(data, attempts, ledger=ledger, attempt_id=attempt_id,
                           bound=bound)

    def _payload(self, page_text, image_jpeg, ladder, hints, page_label, max_tokens,
                 headings=()):
        content = [{"type": "text",
                    "text": user_prompt(page_text, ladder=ladder, hints=hints,
                                        page_label=page_label, headings=headings)}]
        max_price = dict(self.spec.max_price)
        if image_jpeg:
            content.insert(0, {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + _b64(image_jpeg)}})
            # Some upstreams price an image flat instead of by token. Uncapped,
            # that whole billing mode would sit outside the user's cap; the ceiling
            # is the image's token bound at this tier's prompt rate, so a provider
            # that would charge more is excluded by routing rather than discovered
            # on the bill.
            max_price["image"] = round(image_token_bound(image_jpeg)
                                       * self.spec.prompt_usd_per_mtok / 1e6, 6)
        return {
            "model": self.model_id,
            "messages": [{"role": "system", "content": structure_prompt()},
                         {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": int(max_tokens),
            "usage": {"include": True},
            # This is a re-marking job: the words are already in front of the model
            # and there is nothing to work out. Reasoning tokens are billed as
            # completion tokens and consume the same ceiling, so leaving them on
            # pays for thinking nobody reads and crowds out the answer.
            "reasoning": {"enabled": False},
            # The user consented to a dollar figure computed from these rates. Sent
            # as a ceiling, they stop the request reaching an upstream that would
            # bill more than the figure they agreed to.
            "provider": {"max_price": max_price},
        }

    def _post(self, payload, ledger=None, bound=None, page_label=None,
              should_stop=None, prompt_version=PROMPT_VERSION, attempt_context=None, safe_errors=False, payload_bytes=None):
        session = self._session or requests
        headers = {"Authorization": "Bearer %s" % self._api_key,
                   "Content-Type": "application/json",
                   "HTTP-Referer": REFERER, "X-Title": TITLE}
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            if should_stop is not None and should_stop():
                raise AttemptCancelled(
                    "cancelled before this attempt; nothing new was dispatched")
            attempt_id = None
            if ledger is not None and bound is not None:
                # The strict bound is durably held BEFORE the socket opens, so a
                # lost response after dispatch -- or a crash -- cannot turn a
                # possibly-billed request into a free one.
                attempt_id = ledger.reserve_attempt(page_label, bound,
                                                    model_id=self.model_id,
                                                    prompt_version=prompt_version, context=attempt_context)
            try:
                body = {"data": payload_bytes} if payload_bytes is not None else {"json": payload}
                response = session.post(OPENROUTER_URL, headers=headers,
                                        timeout=self.timeout, **body)
            except requests.RequestException as exc:
                if _transport_was_pre_dispatch(exc):
                    if ledger is not None and attempt_id is not None:
                        ledger.release_attempt(attempt_id, "pre_dispatch")
                    last_error = "network error: %s" % (type(exc).__name__ if safe_errors else exc)
                    if attempt == self.max_retries:
                        raise ModelError(last_error)
                    self._sleep(attempt, response=None)
                    continue
                raise UncertainBilling(
                    "the request was dispatched and its answer was lost: %s. Up to "
                    "$%.4f may still be billed; the bound stays held as unresolved"
                    % (type(exc).__name__ if safe_errors else exc, bound or 0.0), held_usd=bound or 0.0, attempt=attempt_id)

            if response.status_code == 200:
                try:
                    return response.json(), attempt, attempt_id
                except ValueError:
                    raise UncertainBilling(
                        "the provider returned a 200 whose body we cannot parse; the "
                        "request may have been billed. The bound stays held as "
                        "unresolved", held_usd=bound or 0.0, attempt=attempt_id)

            detail = 'request refused' if safe_errors else _error_detail(response)
            if safe_errors and ledger is not None and attempt_id is not None:
                ledger.record({'kind': 'attempt_diagnostic', 'attempt': attempt_id,
                               'http_status': response.status_code,
                               'generation_present': bool(_generation_id(response))})
            if not _rejection_was_pre_generation(response):
                raise UncertainBilling(
                    "OpenRouter %s after the request may have been accepted: %s. Up "
                    "to $%.4f may still be billed; the bound stays held as "
                    "unresolved" % (response.status_code, detail, bound or 0.0),
                    held_usd=bound or 0.0, attempt=attempt_id)
            # A documented request-error rejection: pre-generation, known unbilled,
            # never worth retrying as-is.
            if ledger is not None and attempt_id is not None:
                ledger.release_attempt(attempt_id, "rejected_before_generation")
            raise ModelError("OpenRouter %s: %s" % (response.status_code, detail))
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

    def _settle(self, data, ledger=None, attempt_id=None, bound=0.0, require_reported=False):
        # The bill first, because every way out of this method is a call that has
        # already been answered and therefore already been charged. Working the cost
        # out only on the path that produces a page is how an answer nobody can use
        # becomes an answer nobody paid for.
        raw_usage = data.get("usage") if isinstance(data, dict) else None
        trustworthy = isinstance(raw_usage, dict) and any(
            key in raw_usage for key in ("prompt_tokens", "completion_tokens", "cost"))
        if not trustworthy:
            # We ask for usage on every request; its absence on a 200 is a bill we
            # cannot reconcile. The answer is refused and the bound stays held.
            raise UncertainBilling(
                "the provider answered without a usage record; the call cannot be "
                "reconciled, so its bound stays held as unresolved",
                held_usd=bound, attempt=attempt_id)
        usage = raw_usage
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
            if prompt_tokens < 0 or completion_tokens < 0:
                raise ValueError('negative token usage')
            if require_reported and (isinstance(usage.get('cost'), bool) or usage.get('cost') is None):
                raise ValueError('missing provider cost')
            cost, source = self._cost(usage, prompt_tokens, completion_tokens)
            if not math.isfinite(cost) or cost < 0 or (require_reported and source != 'provider'):
                raise ValueError('invalid provider cost')
        except (TypeError, ValueError, OverflowError):
            raise UncertainBilling('provider usage cannot be reconciled; bound remains held',
                                   held_usd=bound, attempt=attempt_id)
        if ledger is not None and attempt_id is not None:
            # The held bound becomes the metered debit, durably, HERE: a crash
            # before the pipeline's page record still counts the charge (the page
            # record references the same attempt, so a completed run counts once).
            ledger.reconcile_attempt(attempt_id, cost)

        return prompt_tokens, completion_tokens, cost, source

    def _parse(self, data, attempts, ledger=None, attempt_id=None, bound=0.0):
        prompt_tokens, completion_tokens, cost, source = self._settle(
            data, ledger=ledger, attempt_id=attempt_id, bound=bound)

        def unusable(message):
            return UnusableAnswer(message, cost_usd=cost, cost_source=source,
                                  prompt_tokens=prompt_tokens,
                                  completion_tokens=completion_tokens,
                                  attempt=attempt_id)

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise unusable("the provider's reply had no message content")

        if (choice.get("finish_reason") or choice.get("native_finish_reason")) == "length":
            # Adopting the fragment is not an option and neither is pretending it is
            # a model that deleted the end of the page: say what happened, so the
            # page keeps its deterministic text for a reason somebody can act on.
            raise unusable(
                "the provider cut the answer short at the token ceiling "
                "(%s completion tokens); the page was not converted"
                % (completion_tokens or "?"))

        content = _FENCE.sub("", content).strip()
        uncertain, notes, html = _split_contract(content)
        if not _HTML_TAG.search(html):
            raise unusable(
                "the model answered prose instead of an HTML fragment: %s"
                % html[:160].replace("\n", " "))

        # One shape from here on: the gate, the page cache and the EPUB all read the
        # same markup, so which of the two ways a provider chose to write a note down
        # stops being something any of them has to know about.
        return ModelResult(html=number_the_notes(html), uncertain=uncertain, notes=notes,
                           model=data.get("model") or self.model_id,
                           prompt_tokens=prompt_tokens,
                           completion_tokens=completion_tokens,
                           cost_usd=cost, cost_source=source,
                           provider=str(data.get("provider") or ""), attempts=attempts,
                           attempt=attempt_id or "")

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
    if isinstance(uncertain, (dict, str)):
        uncertain = [uncertain]
    records = [record for record in (uncertain_record(item) for item in uncertain)
               if record is not None]
    return records, str(parsed.get("notes") or ""), html


def _error_detail(response):
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:200]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:200]
    return str(error or body)[:200]


def _walk_causes(exc):
    """The exception and everything it wraps, as exception objects (requests and
    urllib3 both chain through args and __cause__/__context__)."""
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend(arg for arg in getattr(current, "args", ())
                     if isinstance(arg, BaseException))
        stack.append(getattr(current, "__cause__", None))
        stack.append(getattr(current, "__context__", None))


def _transport_was_pre_dispatch(exc):
    """True only when the request provably never left this machine.

    A connect timeout, a refused connection or an unresolvable name mean no bytes
    were sent, so no provider saw the request: safely unbilled. A read timeout, a
    reset mid-transfer or anything murkier means the request may already be with
    the provider -- unknown, and unknown is not free.
    """
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    return any(isinstance(cause, (NewConnectionError, NameResolutionError,
                                  socket.gaierror, ConnectionRefusedError))
               for cause in _walk_causes(exc))


def _rejection_was_pre_generation(response):
    """True only when the response proves the request never reached a provider.

    OpenRouter's error contract: request-validation, auth, credit and guardrail
    rejections happen before any work begins. Everything else -- 408, 409, 425,
    429, every 5xx -- lacks a documented pre-generation proof for Chat
    Completions: OpenRouter says even a no-content answer may bill prompt
    processing, and the ABSENCE of a generation id is not evidence of absence.
    Any response carrying a generation identity (the body id, or the documented
    X-Generation-Id header) was accepted by a provider by definition.
    """
    if _generation_id(response):
        return False
    return response.status_code in (400, 401, 402, 403, 404, 413, 422)


def _generation_id(response):
    """The generation's identity, in either representation it can arrive in.

    The body field is ``id``; the documented tracking header is
    ``X-Generation-Id``. Header names are case-insensitive on the wire, so they
    are compared case-insensitively here, whatever mapping type the response
    object carries.
    """
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("id"):
        return str(body["id"])
    headers = getattr(response, "headers", None) or {}
    items = headers.items() if hasattr(headers, "items") else []
    for name, value in items:
        if str(name).lower() == "x-generation-id" and value:
            return str(value)
    return None


def _b64(data):
    import base64

    return base64.b64encode(data).decode("ascii")


def min_request_bound_usd(tier=DEFAULT_TIER):
    """The strict bound of the cheapest paid request this tier can make.

    No image, an empty page, the completion floor -- the smallest request the
    pipeline ever sends. A cap below it cannot fund even one page, whatever the
    book, so the start endpoint refuses such a cap with this figure rather than
    letting the job die at its first reservation.
    """
    client = OpenRouterClient(None, tier=tier)     # unconfigured: nothing leaves
    return client.request_bound("", image_jpeg=None,
                                max_tokens=COMPLETION_TOKENS_FLOOR)


def tier_choices():
    """What the Reflow page shows in its cost card."""
    return [{"tier": name, "model": spec.model_id, "label": spec.label,
             "price_per_page": spec.price_per_page,
             "min_request_usd": min_request_bound_usd(name)}
            for name, spec in sorted(TIERS.items(),
                                     key=lambda kv: kv[1].price_per_page)]
