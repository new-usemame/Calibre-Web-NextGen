# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-preserving text quality checks, measured on every published page.

These counters never correct spelling or guess a missing word. A rejected layer
must be read from pixels; a rejected reading retains its original source image.
The English dictionary comes from the already installed OCR language data.
"""
from functools import lru_cache
from html.parser import HTMLParser
import hashlib
from pathlib import Path
import re
from shutil import which as _which
import subprocess
from subprocess import run as _run
import tempfile
import unicodedata

VERSION = 'page-text-quality-2'
MIN_PAGE_WORDS = 40
MISS_RATE = .40
MIN_CASE_WORDS = 3
CASE_MISS_RATE = .12
CASE_FLIP_RATE = .08
SEVERE_CASE_TRANSITIONS = 3
WINDOW_WORDS = 16
WINDOW_MISS_RATE = .65
COMBINED_MIN_ANOMALOUS_WORDS = 2
COMBINED_CASE_FLIP_RATE = .05
COMBINED_MISS_RATE = .20
COMBINED_WINDOW_MISS_RATE = .50
MAX_WORDLIST_BYTES = 8 * 1024 * 1024
_UNAVAILABLE_LEXICON = object()
_WORDS = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def _latin(word):
    return all(not c.isalpha() or unicodedata.name(c, '').startswith('LATIN ') for c in word)


def _case_anomaly(word):
    # One capital in an otherwise title/camel-case name is ordinary typography.
    # Repeated transitions or an internal uppercase run are damaged casing.
    transitions = sum(a.islower() and z.isupper() for a,z in zip(word,word[1:]))
    return transitions >= 2 or bool(re.search(r'[a-z][A-Z]{2,}',word))


def script_counts(text):
    """Count actual Unicode letter scripts, independent of the English lexicon."""
    result = {}
    for char in text or '':
        if not char.isalpha():
            continue
        name = unicodedata.name(char, '')
        script = name.split(' ', 1)[0]
        if script in {'LATIN', 'GREEK', 'CYRILLIC', 'HEBREW', 'ARABIC', 'DEVANAGARI',
                      'HIRAGANA', 'KATAKANA', 'HANGUL', 'CJK'}:
            result[script] = result.get(script, 0) + 1
    return result


@lru_cache(maxsize=2)
def _load_lexicon(data_path, version, identity):
    """Extract the trusted installed DAWG once, with time and output bounds.

    Identity is the installed language data's SHA; a language upgrade
    cannot reuse a dictionary extracted from different bytes. No PDF text is
    passed to either tool, and no downloaded dictionary or native API is used.
    """
    from . import ocr
    tools = [_which(name) for name in ('combine_tessdata', 'dawg2wordlist')]
    if not all(tools):
        return None
    try:
        data = Path(data_path)
        with tempfile.TemporaryDirectory(prefix='reflow-lexicon-') as directory:
            root = Path(directory)
            chars, dawg, words = (root / name for name in ('eng.lstm-unicharset', 'eng.lstm-word-dawg', 'words.txt'))
            for command in ([tools[0], '-e', str(data), str(chars), str(dawg)],
                            [tools[1], str(chars), str(dawg), str(words)]):
                _run(command, check=True, timeout=20, env=ocr._env(),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if words.stat().st_size > MAX_WORDLIST_BYTES:
                return None
            result = frozenset(word.casefold().replace('’', "'") for word in words.read_text(encoding='utf-8').splitlines())
            return result if len(result) >= 10_000 else None
    except (OSError, subprocess.SubprocessError, UnicodeError, IndexError):
        return None


@lru_cache(maxsize=4)
def _dictionary_identity(executable, stamp, environment):
    # Dictionary availability is independent of the page recognizer's transient
    # failures. Only trusted engine/language files enter this bounded probe.
    listing = _run([executable, '--list-langs'], capture_output=True,
        text=True, check=True, timeout=10, env=dict(environment)).stdout
    match = re.search(r'"([^"\n]+)"', listing.splitlines()[0])
    if not match:
        return None
    data = Path(match[1]) / 'eng.traineddata'
    with data.open('rb') as handle:
        identity = hashlib.file_digest(handle, 'sha256').hexdigest()
    version = _run([executable, '--version'], capture_output=True,
        text=True, check=True, timeout=10, env=dict(environment)).stdout.splitlines()[0]
    return str(data), version, identity, data.stat().st_mtime_ns, data.stat().st_size


def english_lexicon():
    from . import ocr
    executable = _which('tesseract')
    if not executable:
        return None
    try:
        path = Path(executable).resolve()
        stamp = path.stat()
        identity = _dictionary_identity(str(path), (stamp.st_size, stamp.st_mtime_ns),
            tuple(sorted(ocr._env().items())))
        if identity is None:
            return None
        data_path, version, digest, modified, size = identity
        data = Path(data_path).stat()
        if data.st_mtime_ns != modified or data.st_size != size:
            _dictionary_identity.cache_clear()
            return english_lexicon()
        return _load_lexicon(data_path, version, digest)
    except (OSError, subprocess.SubprocessError, UnicodeError, IndexError):
        return None


# Preserve the installed-dictionary loader separately from a caller's source
# vocabulary. Tests and library callers may inject a deliberately tiny source
# lexicon; converter-authored explanations still receive the real language check.
_installed_english_lexicon = english_lexicon


def publication_lexicon():
    return _installed_english_lexicon()


def inspect(text, *, lexicon=None):
    """Return counters and rejection reasons, never source words or corrections.

    English misses alone need forty words and a forty-percent miss rate. Mixed
    internal case also checks short headings. A sixteen-word sliding window
    catches dense damaged passages that a clean page average would conceal.
    Non-Latin words never count as English dictionary misses.
    """
    lexicon = english_lexicon() if lexicon is None else lexicon
    if lexicon is _UNAVAILABLE_LEXICON:
        lexicon = None
    words = [w for w in _WORDS.findall(text or '') if len(w) >= 3 and _latin(w)]
    folded = [w.casefold().replace('’', "'") for w in words]
    flips = [any(a.islower() and z.isupper() for a, z in zip(w, w[1:])) for w in words]
    anomalous_case_words = sum(_case_anomaly(w) for w in words)
    severe_case_words = sum(sum(a.islower() and z.isupper() for a,z in zip(w,w[1:])) >= SEVERE_CASE_TRANSITIONS for w in words)
    missing = [w not in lexicon for w in folded] if lexicon is not None else []
    count = len(words)
    miss_rate = sum(missing) / count if count and missing else 0.
    flip_rate = sum(flips) / count if count else 0.
    worst = max((sum(missing[n:n+WINDOW_WORDS]) / WINDOW_WORDS
        for n in range(max(0, count-WINDOW_WORDS+1))), default=0.)
    damaged_lines = 0
    if lexicon is not None:
        for line in (text or '').splitlines():
            line_words = [w for w in _WORDS.findall(line) if len(w) >= 3 and _latin(w)]
            if len(line_words) < MIN_CASE_WORDS:
                continue
            line_flips = sum(any(a.islower() and z.isupper() for a, z in zip(w,w[1:])) for w in line_words)
            line_misses = sum(w.casefold().replace('’', "'") not in lexicon for w in line_words)
            if any(_case_anomaly(w) for w in line_words) and line_flips/len(line_words) >= CASE_FLIP_RATE and line_misses/len(line_words) >= CASE_MISS_RATE:
                damaged_lines += 1
    reasons = []
    if lexicon is None and count >= MIN_CASE_WORDS:
        reasons.append('dictionary_unavailable')
    if count >= MIN_PAGE_WORDS and miss_rate >= MISS_RATE:
        reasons.append('dictionary_miss_rate')
    if count >= MIN_CASE_WORDS and anomalous_case_words >= 1 and flip_rate >= CASE_FLIP_RATE and miss_rate >= CASE_MISS_RATE:
        reasons.append('internal_case_and_dictionary_misses')
    if damaged_lines and 'internal_case_and_dictionary_misses' not in reasons:
        reasons.append('internal_case_and_dictionary_misses')
    if severe_case_words:
        reasons.append('repeated_internal_case_transitions')
    if worst >= WINDOW_MISS_RATE:
        reasons.append('dense_dictionary_miss_window')
    # Independent weak signals can corroborate each other even when no one
    # isolated threshold is crossed. Require repeated damaged casing, sustained
    # lexical loss and a dense local pocket together; ordinary unfamiliar names
    # or a single mixed-case product name cannot satisfy this rule.
    if (count >= MIN_PAGE_WORDS
            and anomalous_case_words >= COMBINED_MIN_ANOMALOUS_WORDS
            and flip_rate >= COMBINED_CASE_FLIP_RATE
            and miss_rate >= COMBINED_MISS_RATE
            and worst >= COMBINED_WINDOW_MISS_RATE):
        reasons.append('combined_case_dictionary_window')
    return dict(version=VERSION, latin_words=count, dictionary_available=lexicon is not None,
        dictionary_misses=sum(missing), dictionary_miss_rate=round(miss_rate, 6),
        case_flip_words=sum(flips), case_flip_rate=round(flip_rate, 6), anomalous_case_words=anomalous_case_words, severe_case_words=severe_case_words,
        worst_window_miss_rate=round(worst, 6), damaged_case_lines=damaged_lines, scripts=script_counts(text),
        reasons=reasons, needs_read=bool(reasons))


def inspect_disclosure(text, *, lexicon=None):
    """Check short purported readings excluded from the page's word inventory.

    An uncertainty note or image description cannot use the forty-word page
    minimum to publish a dense fragment of unrecognized text. Three eligible
    Latin words suffice here; non-Latin scripts remain outside English misses.
    Rejection retains original pixels rather than guessing replacement words.
    """
    # Codes and labelled coordinates such as RackLeft00 are identifiers, not
    # misspelled English. Exclude the complete alphanumeric identifier rather
    # than feeding only its letter prefix to the English word classifier.
    identifiers=[]
    def prose_token(match):
        token=match[0]
        if any(char.isalpha() for char in token) and any(char.isdigit() for char in token):
            identifiers.append(token)
            return ''
        return token
    prose=re.sub(r'\w+',prose_token,text or '')
    metric = inspect(prose, lexicon=lexicon)
    metric['alphanumeric_identifiers'] = len(identifiers)
    if (metric['latin_words'] >= MIN_CASE_WORDS
            and metric['dictionary_miss_rate'] >= MISS_RATE):
        if 'short_disclosure_dictionary_misses' not in metric['reasons']:
            metric['reasons'].append('short_disclosure_dictionary_misses')
        metric['needs_read'] = True
    return metric


def script_mismatches(source, reading):
    """Reject a reading that loses a source-proven non-Latin passage's script.

    Four source letters avoid treating isolated mathematical variables as prose.
    This is positive Unicode evidence, not a claim that an English recognizer can
    identify every script on a page whose text layer is absent or damaged.
    """
    before, after = script_counts(source), script_counts(reading)
    return [script for script, count in before.items() if script != 'LATIN' and count >= 4
            and after.get(script, 0) < max(2, count * .25)]


def reading_script_mismatches(reading, compiled):
    """An optical fallback cannot replace part of a non-Latin transcription.

    Unlike potentially damaged source-layer evidence, the accepted reader text
    is already the compiler's input. Preserve its complete non-Latin letter
    sequence, allowing Unicode compatibility and case equivalence only.
    """
    def letters(text,script):
        return ''.join(char for char in unicodedata.normalize('NFKC',text).casefold()
            if char.isalpha() and unicodedata.name(char,'').split(' ',1)[0]==script)
    return [script for script,count in script_counts(reading).items()
        if script!='LATIN' and count>=4 and letters(reading,script)!=letters(compiled,script)]


class _ReadingText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get('class', '').split()
        excluded = any(c in {'vision-disclosure', 'source-original', 'source-disclosure', 'source-reading-annotation',
                            'source-evidence-notice', 'source-disclosure-ref', 'note-return'} for c in classes)
        if tag not in {'br', 'img', 'hr', 'meta', 'link', 'input'}:
            self.stack.append((tag, excluded or any(v for _, v in self.stack)))
        if tag in {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'aside', 'br', 'li', 'tr', 'td', 'th'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        for n in range(len(self.stack)-1, -1, -1):
            if self.stack[n][0] == tag:
                del self.stack[n:]
                break
        if tag in {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'aside', 'li', 'tr', 'td', 'th'}:
            self.parts.append('\n')

    def handle_data(self, value):
        if not any(v for _, v in self.stack):
            self.parts.append(value)


def reading_text(page):
    parser = _ReadingText()
    parser.feed('\n'.join(page.get('body', []) + page.get('asides', [])))
    return ''.join(parser.parts)


def sweep_pages(pages, records, expected_pages, *, lexicon=None):
    """Check every physical page's actual reading text before page-turn joins."""
    expected = sorted(expected_pages)
    actual = [p['pno'] for p in pages]
    if sorted(actual) != expected or len(set(actual)) != len(actual):
        raise ValueError('Text quality sweep does not cover every selected source page exactly once')
    result = {}
    for page in pages:
        pno = page['pno']
        metric = inspect(reading_text(page), lexicon=lexicon)
        resolution = records[pno]['source']
        metric['resolution'] = resolution
        # A complete source-page disclosure has no purported reading text.
        metric['passed'] = not metric['needs_read']
        result[pno] = metric
    return dict(version=VERSION, expected_pages=expected, checked_pages=sorted(result),
        pages=result, passed=all(m['passed'] for m in result.values()),
        thresholds=dict(min_page_words=MIN_PAGE_WORDS, dictionary_miss_rate=MISS_RATE,
            min_case_words=MIN_CASE_WORDS, case_flip_rate=CASE_FLIP_RATE,
            case_dictionary_miss_rate=CASE_MISS_RATE, severe_case_transitions=SEVERE_CASE_TRANSITIONS, window_words=WINDOW_WORDS,
            window_dictionary_miss_rate=WINDOW_MISS_RATE,
            combined_min_anomalous_words=COMBINED_MIN_ANOMALOUS_WORDS,
            combined_case_flip_rate=COMBINED_CASE_FLIP_RATE,
            combined_dictionary_miss_rate=COMBINED_MISS_RATE,
            combined_window_dictionary_miss_rate=COMBINED_WINDOW_MISS_RATE))


def validate_sweep(sweep, expected_pages):
    """The packaging gate cannot accept an incomplete or rejected quality sweep."""
    expected = sorted(expected_pages)
    pages = sweep.get('pages', {})
    observed = sorted(int(p) for p in pages)
    if (sweep.get('version') != VERSION or sweep.get('expected_pages') != expected
            or sweep.get('checked_pages') != expected or observed != expected
            or not sweep.get('passed') or any(not m.get('passed') or m.get('needs_read') for m in pages.values())):
        raise ValueError('Every source page requires a passing text quality sweep or retained source disclosure')


def inspect_partitioned(units, *, disclosure=False, lexicon=None):
    """Attribute a rejected reading to the source units supplying its evidence.

    Units are ``(owner, text)`` pairs, or ``(owner, text, kind)`` triples. Alt,
    accessibility and disclosure units use the short-reading safeguard; ordinary
    reading units retain the page rule and supplied source vocabulary. Generated
    channels use the independently loaded installed English dictionary, including
    any quoted source text; none of their words bypasses lexical checks. Repeated
    owners are joined before their
    independent check. Aggregate checks also catch damage across unit boundaries.
    A mixed-owner failing window implicates owners of its missed words, never a
    clean owner merely adjacent to that window. Page-wide case and miss evidence
    implicates its contributing owners; ownership cannot prove which contributing
    damaged word to repair, so all such contributors are retained as disclosures.
    The result contains counters and owner IDs, never the supplied source text.
    """
    lexicon = english_lexicon() if lexicon is None else lexicon
    grouped = {}
    ordered = []
    kinds = {}
    for unit in units:
        owner, text = unit[:2]
        ordered.append((owner, text or '', unit[2] if len(unit) > 2 else 'reading'))
        grouped.setdefault(owner, []).append(text or '')
        kinds.setdefault(owner, set()).add(unit[2] if len(unit) > 2 else 'reading')
    texts = {owner: '\n'.join(parts) for owner, parts in grouped.items()}
    generated = {owner for owner in texts if kinds[owner] - {'reading'}}
    generated_lexicon = publication_lexicon() if generated else lexicon
    metrics = {owner: (inspect_disclosure if disclosure or owner in generated else inspect)(
        text, lexicon=(generated_lexicon if generated_lexicon is not None else _UNAVAILABLE_LEXICON) if owner in generated else lexicon)
        for owner, text in texts.items()}
    # In production both vocabularies are the same installed language data. A
    # caller's tiny source-only vocabulary must not invalidate generated controls;
    # its source units are independently checked above without any augmentation.
    aggregate_lexicon = generated_lexicon if generated else lexicon
    aggregate = inspect('\n'.join(text for _, text, _ in ordered),
                        lexicon=_UNAVAILABLE_LEXICON if generated and aggregate_lexicon is None else aggregate_lexicon)
    rejected = {owner for owner, metric in metrics.items() if metric['needs_read']}
    def attribute(metric, segments, vocabulary):
        words = [(owner, word) for owner, text, _ in segments for word in _WORDS.findall(text)
                 if len(word) >= 3 and _latin(word)]
        if not metric['needs_read']:
            return
        reasons = set(metric['reasons'])
        misses = [vocabulary is not None and word.casefold().replace('’', "'") not in vocabulary for _, word in words]
        if 'dictionary_unavailable' in reasons:
            rejected.update(owner for owner, _ in words)
        if 'dictionary_miss_rate' in reasons:
            rejected.update(owner for (owner, _), missing in zip(words, misses) if missing)
        if reasons & {'internal_case_and_dictionary_misses', 'repeated_internal_case_transitions', 'combined_case_dictionary_window'}:
            rejected.update(owner for (owner, word), missing in zip(words, misses) if missing or _case_anomaly(word))
        if 'dense_dictionary_miss_window' in reasons:
            for start in range(max(0, len(words) - WINDOW_WORDS + 1)):
                if sum(misses[start:start + WINDOW_WORDS]) / WINDOW_WORDS >= WINDOW_MISS_RATE:
                    rejected.update(words[n][0] for n in range(start, start + WINDOW_WORDS) if misses[n])
    attribute(aggregate, ordered, aggregate_lexicon)
    # Generated explanations may not dilute the former source-only page check.
    # Keep that projection independently authoritative as publication channels grow.
    reading = [segment for segment in ordered if segment[2] == 'reading']
    reading_metric = inspect('\n'.join(text for _, text, _ in reading), lexicon=lexicon)
    attribute(reading_metric, reading, lexicon)
    aggregate['source_reading'] = reading_metric
    if reading_metric['needs_read'] and not aggregate['needs_read']:
        aggregate['reasons'].append('source_reading_text_quality')
    # An individually bad short reading remains bad beside a long clean body.
    aggregate['units'] = metrics
    aggregate['rejected_units'] = [owner for owner in texts if owner in rejected]
    if rejected and not aggregate['needs_read']:
        aggregate['reasons'].append('unit_text_quality')
    aggregate['needs_read'] = bool(rejected)
    return aggregate


class _PublicationText(HTMLParser):
    """Keep reader-facing text channels distinct from source word accounting."""
    _BLOCKS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'aside', 'br', 'li', 'tr', 'td', 'th'}
    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    _DISCLOSURES = {'vision-disclosure', 'source-original', 'source-disclosure', 'source-reading-annotation',
                    'source-evidence-notice', 'source-disclosure-ref', 'note-return'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack, self.pending = [], [], []
        self.kind = 'reading'
        self.owner = None
        self.pending_provenance = []
        self.pending_size = 0

    def _flush(self):
        raw = ''.join(self.pending)
        text = raw.strip()
        if text:
            left = len(raw) - len(raw.lstrip())
            right = left + len(text)
            spans = []
            owners = []
            for start, end, owner in self.pending_provenance:
                if owner is not None and start < right and end > left:
                    if owner not in owners:
                        owners.append(owner)
                    spans.append(dict(start=max(start, left)-left, end=min(end, right)-left, owners=[owner]))
            self.parts.append(dict(id=len(self.parts), text=text, kind=self.kind, owners=owners, spans=spans))
        self.pending = []
        self.pending_provenance = []
        self.pending_size = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        excluded = tag in {'script', 'style', 'head'} or any(frame[1] for frame in self.stack)
        kind = 'disclosure' if set(values.get('class', '').split()) & self._DISCLOSURES else self.kind
        if tag in self._BLOCKS or kind != self.kind:
            self._flush()
        owner = self.owner
        if values.get('data-source-page', '').isdigit():
            owner = dict(page=int(values['data-source-page']), block=values.get('data-source-block') or None)
        elif values.get('data-source-block') and owner is not None:
            owner = dict(page=owner['page'], block=values['data-source-block'])
        if not excluded:
            for attribute in ('alt', 'aria-label', 'title'):
                if values.get(attribute):
                    self._flush()
                    self.parts.append(dict(id=len(self.parts), text=values[attribute],
                                           kind='alt' if attribute == 'alt' else 'accessibility',
                                           owners=[owner] if owner is not None else [],
                                           spans=[dict(start=0, end=len(values[attribute]), owners=[owner])] if owner is not None else []))
        if tag not in self._VOID:
            self.stack.append((tag, excluded, self.kind, self.owner))
            self.kind = kind
            self.owner = owner

    def handle_endtag(self, tag):
        if tag in self._BLOCKS:
            self._flush()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                restored = self.stack[index][2]
                if restored != self.kind:
                    self._flush()
                self.kind = restored
                self.owner = self.stack[index][3]
                del self.stack[index:]
                break

    def handle_data(self, value):
        if not any(frame[1] for frame in self.stack):
            self.pending.append(value)
            self.pending_provenance.append((self.pending_size, self.pending_size + len(value), self.owner))
            self.pending_size += len(value)


def publication_parts(page):
    """Return ordered reader-visible blocks and accessible image descriptions.

    This projection deliberately includes generated notes and image attributes.
    ``reading_text`` remains the source-only projection used for word inventory.
    Parts carry local IDs, inherited source owners and character-offset spans.
    Inline provenance changes preserve a word's exact character sequence; the
    owner union is conservative when one word crosses multiple source spans.
    """
    parser = _PublicationText()
    parser.feed('\n'.join(page.get('body', []) + page.get('asides', [])))
    parser.close()
    parser._flush()
    return parser.parts


def publication_text(page):
    return '\n'.join(part['text'] for part in publication_parts(page))


def sweep_publication_pages(pages, records, expected_pages, *, lexicon=None):
    """Sweep source readings, visible disclosures and accessibility text alike."""
    sweep = sweep_pages(pages, records, expected_pages, lexicon=lexicon)
    for page in pages:
        parts = publication_parts(page)
        metric = inspect_partitioned([(part['id'], part['text'], part['kind']) for part in parts], lexicon=lexicon)
        metric['resolution'] = records[page['pno']]['source']
        metric['passed'] = not metric['needs_read']
        sweep['pages'][page['pno']] = metric
    sweep['passed'] = all(metric['passed'] for metric in sweep['pages'].values())
    sweep['projection'] = 'reader-publication-text-1'
    return sweep
