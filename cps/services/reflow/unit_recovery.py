"""Source-owned, transactional recovery for the explicit pixel conversion lane.

Strict reading and assembly primitives still reject invalid input. A publication
attempt runs with this controller and is disposable: no failed attempt's changed
paragraphs, notes, links or ZIP members are reused.
"""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import hashlib
import json
import os
from pathlib import Path
import threading
import re
import pickle
import shutil
import stat
import tempfile
from xml.etree import ElementTree as ET


_active = ContextVar('reflow_unit_recovery', default=None)


class SourceIntegrityError(ValueError):
    """Authoritative source/configuration cannot safely be used."""


def _pdf_references(value):
    """Read indirect references while ignoring PDF strings, names and comments."""
    tokens=[];index=0
    while index<len(value):
        char=value[index]
        if char=='(':
            depth=1;index+=1
            while index<len(value) and depth:
                if value[index]=='\\':index+=2;continue
                if value[index]=='(':depth+=1
                elif value[index]==')':depth-=1
                index+=1
            if depth:raise SourceIntegrityError('Cannot verify source object string')
            tokens=[]
        elif value[index:index+2] in ('<<','>>'):
            index+=2
        elif char=='<':
            end=value.find('>',index+1)
            if end<0:raise SourceIntegrityError('Cannot verify source object string')
            index=end+1;tokens=[]
        elif char=='%':
            end=value.find('\n',index+1);index=len(value) if end<0 else end+1
        elif char=='/':
            index+=1
            while index<len(value) and not value[index].isspace() and value[index] not in '[]<>()/%':index+=1
            tokens=[]
        elif char.isspace() or char in '[]<>':index+=1
        else:
            end=index+1
            while end<len(value) and not value[end].isspace() and value[end] not in '[]<>()/%':end+=1
            token=value[index:end];index=end
            if token=='R' and len(tokens)>=2 and all(v.isdigit() for v in tokens[-2:]):
                yield int(tokens[-2]);tokens=[]
            else:tokens=(tokens+[token])[-2:]


def page_fingerprint(doc, page):
    """Hash current page objects, encoded streams and inherited render resources.

    File identities and text alone miss in-memory edits and image-only changes.
    Reading raw streams avoids decoding them, and walking the page's resources
    avoids serializing or rendering the entire document on every retry.
    """
    try:
        source=doc[page];root=source.xref
        digest=hashlib.sha256(b'reflow-page-source-v1\x00')
        digest.update(repr((page,tuple(source.rect),source.rotation)).encode())
        pending=[root]
        # Page-tree parents carry inherited resource and geometry values. Their
        # Kids arrays name unrelated pages and are deliberately not traversed.
        ancestor=root;seen_parents=set()
        for key in ('Resources','MediaBox','CropBox','Rotate','UserUnit'):
            ancestor=root;seen_parents.clear()
            while ancestor and ancestor not in seen_parents:
                seen_parents.add(ancestor)
                kind,value=doc.xref_get_key(ancestor,key)
                if kind!='null':
                    digest.update((key+'\x00'+value+'\x00').encode())
                    pending.extend(_pdf_references(value));break
                kind,value=doc.xref_get_key(ancestor,'Parent')
                ancestor=int(value.split()[0]) if kind=='xref' else 0
        # Optional-content visibility, output profiles and form appearances can
        # affect a page without changing its own contents or resource dictionary.
        catalog=doc.pdf_catalog()
        for key in ('OCProperties','OutputIntents','AcroForm'):
            kind,value=doc.xref_get_key(catalog,key)
            digest.update((key+'\x00'+value+'\x00').encode())
            pending.extend(_pdf_references(value))
            if key=='OCProperties' and kind!='null':
                digest.update(json.dumps(doc.layer_ui_configs(),sort_keys=True).encode())
        visited=set()
        while pending:
            xref=pending.pop()
            if xref in visited:continue
            if not 0<xref<doc.xref_length():
                raise SourceIntegrityError('Cannot verify source object reference')
            visited.add(xref)
            value=doc.xref_object(xref,compressed=False)
            digest.update((str(xref)+'\x00'+value+'\x00').encode())
            references=set(_pdf_references(value))
            if doc.xref_get_key(xref,'Type')==('name','/Page'):
                kind,parent=doc.xref_get_key(xref,'Parent')
                if kind=='xref':references.discard(int(parent.split()[0]))
            pending.extend(sorted(references))
            if doc.xref_is_stream(xref):
                data=doc.xref_stream_raw(xref)
                if not isinstance(data,bytes):
                    raise SourceIntegrityError('Cannot verify source stream bytes')
                digest.update(data)
            digest.update(b'\x00')
        return digest.hexdigest()
    except SourceIntegrityError:raise
    except Exception as error:
        raise SourceIntegrityError('Cannot verify current source page') from error


def _job_component(job_id):
    value=str(job_id)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',value):
        raise SourceIntegrityError('Invalid conversion scratch job identity')
    return value


def owned_job_scratch(root, job_id):
    """A real, direct job directory under its configured scratch root, or None."""
    root=Path(root).absolute();job_id=_job_component(job_id)
    # Do not follow a substituted root or job directory during cleanup.
    if any(part.is_symlink() for part in (root,*root.parents)):return None
    path=root/job_id
    try:info=path.lstat()
    except FileNotFoundError:return None
    return path if stat.S_ISDIR(info.st_mode) else None


def remove_job_scratch(root, job_id):
    path=owned_job_scratch(root,job_id)
    if path is None:return False
    shutil.rmtree(path)
    return not path.exists()


@contextmanager
def preparation_scratch(*, scratch_dir=None, job_id=None):
    """Ephemeral preparation under a removable job owner, never beside an EPUB."""
    if scratch_dir is None:
        if job_id is not None:raise SourceIntegrityError('Job scratch root is required')
        with tempfile.TemporaryDirectory(prefix='reflow-preparation-') as staging:
            yield Path(staging)
        return
    if job_id is None:raise SourceIntegrityError('Conversion scratch job identity is required')
    root=Path(scratch_dir).absolute();root.mkdir(parents=True,exist_ok=True)
    if any(part.is_symlink() for part in (root,*root.parents)):
        raise SourceIntegrityError('Conversion scratch root must not contain symlinks')
    owner=root/_job_component(job_id)
    owner.mkdir(mode=0o700,exist_ok=True)
    if owned_job_scratch(root,job_id)!=owner:
        raise SourceIntegrityError('Conversion scratch owner is not a real directory')
    try:
        with tempfile.TemporaryDirectory(prefix='prepare-',dir=owner) as staging:
            yield Path(staging)
    finally:
        try:owner.rmdir()
        except OSError:pass             # another preparation or a retained crash file


class UnitFailure(Exception):
    def __init__(self, units, stage, cause):
        self.units = units
        self.pages = sorted({u['page'] for u in units})
        self.stage, self.cause = stage, cause
        super().__init__(str(cause))


def fatal(error):
    from .build_epub import BuildCancelled
    from .model import AttemptCancelled
    from .ocr import OCRCancelled
    return (isinstance(error, (SourceIntegrityError, MemoryError, BuildCancelled, AttemptCancelled, OCRCancelled)) or
            isinstance(error, OSError) and error.errno in
            (errno.ENOSPC, errno.EACCES, errno.EROFS, errno.EMFILE, errno.ENFILE))


def current():
    return _active.get()


@contextmanager
def activated(controller):
    token = _active.set(controller)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def guard(pages, stage, *, block=None, units=None):
    controller = current()
    if controller is None:
        yield
        return
    pages = [pages] if isinstance(pages, int) else list(pages)
    owned = units or [dict(page=n, block=block) for n in pages]
    controller.check_units(owned)
    try:
        yield
    except UnitFailure:
        raise
    except Exception as error:
        if fatal(error):
            raise
        owned = controller.refine_units(owned, error)
        raise UnitFailure(owned, stage, error) from error


class Controller:
    """One recovery allowance shared by all work on each original source page."""
    def __init__(self, reader, pages, staging):
        self.reader, self.pages = reader, set(pages)
        self.staging = Path(staging)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.readings, self.images, self.answers, self.regions = {}, {}, {}, {}
        self.source_repairs = {}
        self.admitted_answers = {}
        self.omission_coverage = {}
        self.source_image_provider = None
        self.retried, self.degraded, self.events = set(), {}, []
        self.source_render_repaired = set()
        self._lock = threading.RLock()
        self._page_by_image = {}
        self._active_page = None
        self.recovery_closed = set()
        self.records = {}
        self.prepared_pages = {}
        self.source_fingerprints = {}
        self.native_classification = {}
        self.fragment_units, self.id_units = {}, {}
        self.ledger = getattr(reader, 'ledger', None)
        self.start_spend = self.ledger.spent() if self.ledger else None

    def check_units(self, units):
        if not units or any(type(u.get('page')) is not int or u['page'] not in self.pages for u in units):
            raise SourceIntegrityError('Recovery unit has no authoritative selected source page')

    def claim_source_render_repair(self, page):
        """One local original-image retry per physical page; no reading dispatch."""
        self.check_units([dict(page=page)])
        with self._lock:
            if page in self.source_render_repaired:
                return False
            self.source_render_repaired.add(page)
            return True

    def refine_units(self, units, error):
        """A block object in the guarded source page can narrow that page unit.

        This uses registered response identities, not a guessed integer local or
        an identifier suffix. Explicit component guards always take precedence.
        """
        if any(u.get('block') for u in units) or len(units) != 1:
            return units
        n = units[0]['page']
        answer = self.admitted_answers.get(n, self.answers.get(n))
        blocks = answer.get('blocks', []) if isinstance(answer, dict) else []
        if not isinstance(blocks, list):
            blocks = []
        ids = {b['id'] for b in blocks if isinstance(b, dict)
               and isinstance(b.get('id'), str)}
        frames = []
        tb = error.__traceback__
        while tb:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        for frame in reversed(frames):
            candidates = {v['id'] for key, v in frame.f_locals.items()
                          if key in ('block', 'b', 'note', 'art') and isinstance(v, dict)
                          and isinstance(v.get('id'), str) and v['id'] in ids and v.get('role')}
            if len(candidates) == 1:
                return [dict(page=n, block=candidates.pop())]
        return units

    def claim_reread(self, page, identity=None):
        self.check_units([dict(page=page)])
        identity = identity or self.images.get(page, {}).get('sha256')
        with self._lock:
            if page in self.retried:
                return False
            # A persisted dispatch decision precedes a request, so a process
            # restart cannot turn the same page into a third paid attempt.
            path = None
            if identity and hasattr(self.reader, 'cache'):
                path = Path(self.reader.cache) / ('unit-reread-' + identity + '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    self.retried.add(page)
                    return False
            self.retried.add(page)
            if path:
                with path.open('x') as handle:
                    json.dump({'image_sha256': identity, 'reread_claimed': True}, handle)
            return True

    def register_image(self, page, image):
        identity = hashlib.sha256(image).hexdigest()
        path = self.staging / (identity + '.jpg')
        if not path.exists():
            path.write_bytes(image)
        self.images[page] = dict(path=str(path), sha256=identity)
        self._page_by_image[identity] = page
        return identity

    def verify_source(self, doc, page):
        """Fail before any cached reading or preparation can cross a source edit."""
        value=page_fingerprint(doc,page)
        previous=self.source_fingerprints.setdefault(page,value)
        if previous!=value:
            raise SourceIntegrityError('Current source page changed during conversion')
        return value

    def _preparation_key(self, page):
        record = self.readings.get(page)
        if record is None:
            return None
        # Scoped to this controller's admitted source and reading image. Block
        # removal changes geometry, so it invalidates preparation as a reread does.
        value = [record, sorted(self.blocked(page)), self.images.get(page, {}).get('sha256'),
                 self.source_fingerprints.get(page)]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def load_prepared(self, page):
        saved = self.prepared_pages.get(page)
        if saved is None or saved[0] != self._preparation_key(page):
            return None
        # Only our own ephemeral checkpoint is read. No external pickle/cache
        # input is accepted. Every attempt receives independent mutable objects.
        with saved[1].open('rb') as handle:
            return pickle.load(handle)

    def store_prepared(self, page, prepared):
        key = self._preparation_key(page)
        if key is None:
            return
        path = self.staging / ('prepared-' + str(page) + '.pickle')
        with path.open('wb') as handle:
            pickle.dump(prepared, handle, protocol=pickle.HIGHEST_PROTOCOL)
        self.prepared_pages[page] = (key, path)

    def _replace_reading(self, page, record):
        self.readings[page] = copy.deepcopy(record)
        self.answers[page] = copy.deepcopy(record['answer'])
        self.admitted_answers.pop(page, None)
        self.regions.pop(page, None)
        self.omission_coverage.pop(page, None)
        self.prepared_pages.pop(page, None)

    def read(self, page, image, source_repair=None):
        identity = self.register_image(page, image)
        if source_repair is not None:self.source_repairs[page]=source_repair
        if page in self.readings:
            return copy.deepcopy(self.readings[page])
        if hasattr(self.reader, 'read_unit'):
            record = self.reader.read_unit(image, source_repair=source_repair,
                                           recovery=self, page=page)
        else:
            record = self.reader.read(image)
        if record.get('degraded_source'):
            self.events.extend(record.get('recovery_events', []))
            self.recovery_closed.add(page)
            raise UnitFailure([dict(page=page)], 'reading', ValueError(record['failure']))
        self._replace_reading(page, record)
        self.events.extend(record.get('recovery_events', []))
        return copy.deepcopy(record)

    def recover(self, failure):
        """Discard the attempt; replace only its explicitly owned failing units."""
        self.check_units(failure.units)
        cause_message = str(failure.cause)
        # Some strict predicates use a bare assertion. Its empty message must
        # not invalidate the source fallback; retain the raw message separately.
        reason = cause_message if cause_message.strip() else (
            f'{type(failure.cause).__name__} in {failure.stage}')
        event = dict(stage=failure.stage, units=copy.deepcopy(failure.units),
                     error_type=type(failure.cause).__name__, reason=reason,
                     cause_message=cause_message)
        reread = False
        from .ledger import CapExceeded
        from . import model
        for page in failure.pages:
            image_record = self.images.get(page)
            if not image_record and self.source_image_provider is not None:
                try:
                    self.register_image(page,self.source_image_provider(page))
                    image_record=self.images[page]
                except Exception as error:
                    if fatal(error):raise
                    # A missing retry image cannot authorize a paid dispatch or
                    # recursively abort this recovery handler. The same proved
                    # unit can still be retained by a clean fallback render.
                    event.setdefault('source_image_provider_failures',[]).append(
                        dict(page=page,error_type=type(error).__name__,reason=str(error)))
            if image_record and hasattr(self.reader,'cached_reread'):
                try:
                    record=self.reader.cached_reread(Path(image_record['path']).read_bytes(),self.source_repairs.get(page))
                    if record and record.get('config_sha256')!=self.readings.get(page,{}).get('config_sha256'):
                        self._replace_reading(page, record)
                        self.retried.add(page);reread=True
                        event.setdefault('cached_reread_receipts',[]).append(dict(page=page,**{k:v for k,v in record.items() if k!='answer'}))
                        continue
                except Exception as error:
                    if fatal(error):raise
                    event.setdefault('cached_reread_failures',[]).append(dict(page=page,reason=str(error)))
            eligible=(page not in self.recovery_closed and
                      (not hasattr(self.reader,'key') or bool(self.reader.key)) and
                      not isinstance(failure.cause,(CapExceeded,model.CapExceeded)) and
                      (not hasattr(self.reader,'financial_dispatch_allowed') or
                       self.reader.financial_dispatch_allowed()) and
                      (not self.ledger or (self.ledger.remaining()>0 and
                       (hasattr(self.reader,'financial_dispatch_allowed') or not self.ledger.pending_usd()))))
            if eligible and image_record and hasattr(self.reader,'reread_dispatch_allowed'):
                eligible=self.reader.reread_dispatch_allowed(Path(image_record['path']).read_bytes())
            if (eligible and image_record and hasattr(self.reader, 'reread') and
                    self.claim_reread(page, image_record['sha256'])):
                try:
                    record = self.reader.reread(Path(image_record['path']).read_bytes())
                    self._replace_reading(page, record)
                    event.setdefault('reread_receipts', []).append(
                        dict(page=page, **{k: v for k, v in record.items() if k != 'answer'}))
                    reread = True
                except Exception as error:
                    if fatal(error):
                        raise
                    event.setdefault('reread_failures', []).append(
                        dict(page=page, error_type=type(error).__name__, reason=str(error)))
            else:
                event.setdefault('reread_not_dispatched',[]).append(dict(page=page,
                    reason='page_allowance_used_or_no_safe_paid_route_or_financial_hold'))
        if reread:
            event['transition'] = 'TARGETED_REREAD_REASSEMBLE'
            self.events.append(event)
            return
        # A publication batch can implicate an existing source presentation
        # alongside newly rejected readings. Remove the new readings first and
        # check a fresh assembly before concluding the original presentation is
        # unusable. This makes bounded same-page progress; a repeated failure
        # without a new unit still escalates through the original fallback path.
        progress_pages = {unit['page'] for unit in failure.units
                          if unit.get('block') is not None
                          and (unit['page'], unit['block']) not in self.degraded
                          and (unit['page'], None) not in self.degraded}
        for unit in failure.units:
            key = (unit['page'], unit.get('block'))
            if (key in self.degraded and unit.get('block') is not None
                    and unit['page'] in progress_pages
                    and failure.stage in {'publication_text_quality', 'compiled_publication_text_quality'}):
                event.setdefault('deferred_source_unit_rechecks', []).append(copy.deepcopy(unit))
                continue
            if key in self.degraded:
                if unit.get('block') is None:
                    raise SourceIntegrityError('Original source fallback itself is unusable') from failure.cause
                previous=[copy.deepcopy(v) for k,v in self.degraded.items() if k[0]==unit['page']]
                self.degraded={k:v for k,v in self.degraded.items() if k[0]!=unit['page']}
                unit=dict(page=unit['page'],block=None)
                event.setdefault('source_page_escalations',[]).append(dict(page=unit['page'],previous_units=previous))
                key=(unit['page'],None)
            if unit.get('block') is None:
                previous=[copy.deepcopy(v) for k,v in self.degraded.items()
                          if k[0]==unit['page'] and k[1] is not None]
                self.degraded={k:v for k,v in self.degraded.items()
                               if k[0]!=unit['page'] or k[1] is None}
                if previous:
                    event.setdefault('source_page_escalations',[]).append(
                        dict(page=unit['page'],previous_units=previous))
            elif (unit['page'],None) in self.degraded:
                continue
            unit_reason=unit.get('source_failure_reason')
            if not isinstance(unit_reason,str) or not unit_reason.strip():
                unit_reason=reason
            self.degraded[key] = dict(unit, stage=failure.stage, reason=unit_reason,
                                      error_type=type(failure.cause).__name__,
                                      cause_message=cause_message,
                                      status='DEGRADED_SOURCE_CONTEXT')
        event['transition'] = 'VISIBLE_SOURCE_DEGRADATION'
        self.events.append(event)

    def blocked(self, page):
        return {bid: case for (n, bid), case in self.degraded.items() if n == page and bid is not None}

    def admit_answer(self, page, answer):
        """Retain identities only after source geometry admitted their units.

        Omitted-line recovery creates units absent from the model response. Their
        IDs must survive transactional retries just like the original response's
        IDs. Previously blocked units remain in this registry for source targets,
        without reintroducing their rejected text into compilation.
        """
        previous = self.admitted_answers.get(page, self.answers.get(page, {'blocks': []}))
        blocks = copy.deepcopy(answer['blocks'])
        ids = {b['id'] for b in blocks}
        old = previous['blocks']
        for index, block in enumerate(old):
            if block['id'] in ids:
                continue
            following = {b['id'] for b in old[index+1:]}
            at = next((i for i, b in enumerate(blocks) if b['id'] in following), len(blocks))
            blocks.insert(at, copy.deepcopy(block)); ids.add(block['id'])
        self.admitted_answers[page] = dict(answer, blocks=blocks)

    def source_answer(self, page):
        return copy.deepcopy(self.admitted_answers.get(page, self.answers.get(page, {'blocks': []})))

    def register_regions(self, page, regions, omission_coverage=None):
        previous = self.regions.setdefault(page, {})
        blocked = self.blocked(page)
        # Disclosing a unit does not release its already admitted physical ink.
        protected = {instance: owner for instance, owner in previous.get('_instances', {}).items()
                     if owner in blocked}
        previous.update(copy.deepcopy(regions))
        previous.setdefault('_instances', {}).update(protected)
        # The boxes previously used to suppress omitted-line recovery need not
        # be independently proved clipping geometry. Keep that narrower purpose
        # separate, and never let a retry release a disclosed row's footprint.
        coverage = self.omission_coverage.setdefault(page, {})
        for bid, boxes in (omission_coverage or {}).items():
            if bid not in blocked or bid not in coverage:
                coverage[bid] = copy.deepcopy(boxes)

    def coverage_regions(self, page, regions):
        """Source territory retained by a visible fallback cannot be recovered twice.

        Regions were admitted before the failed operation. Their presence here
        reserves ink only; it does not validate or publish the rejected wording.
        """
        regions = copy.deepcopy(regions)
        blocked = self.blocked(page)
        protected = {instance: owner for instance, owner in self.regions.get(page, {}).get('_instances', {}).items()
                     if owner in blocked}
        regions.setdefault('_instances', {}).update(copy.deepcopy(protected))
        for bid in blocked:
            boxes = self.omission_coverage.get(page, {}).get(bid,
                self.regions.get(page, {}).get(bid, []))
            regions.setdefault('_degraded_territories', []).extend(copy.deepcopy(boxes))
        return regions

    def source_pages(self):
        return {n for n, bid in self.degraded if bid is None}

    def seam_units(self,previous,current):
        units=[]
        for page,tail in ((previous,True),(current,False)):
            n=page['pno'];blocks=self.records.get(n,{}).get('blocks',[])
            body=[b for b in blocks if b.get('role')=='body' and not b.get('degraded_source_context')]
            record=(body[-1] if tail else body[0]) if body else {}
            units.append(dict(page=n,block=record.get('id'),**({'fragment_sha256':record['fragment_sha256']} if record.get('fragment_sha256') else {})))
        return units

    def filter_answer(self, page, answer):
        answer = copy.deepcopy(answer)
        blocked = self.blocked(page)
        if not blocked:
            return answer
        removed_notes = {b['id'] for b in answer['blocks']
                         if b['id'] in blocked and b['role'] == 'footnote'}
        # A response reference to an unproved note owner is a dependent unit,
        # never permission to attach it to another note or erase its glyph.
        import re
        for b in answer['blocks']:
            if any(ref in removed_notes for ref in re.findall(r'<n:([^>]+)>', b['text'])):
                key = (page, b['id'])
                self.degraded.setdefault(key, dict(page=page, block=b['id'],
                    stage='note_dependency', reason='Note owner requires original source inspection',
                    status='DEGRADED_SOURCE_CONTEXT'))
        blocked = self.blocked(page)
        answer['blocks'] = [b for b in answer['blocks'] if b['id'] not in blocked or b['role'] in ('figure','table')]
        admitted = {b['id']: b for b in self.admitted_answers.get(page, {}).get('blocks', [])}
        for block in answer['blocks']:
            if block['id'] in blocked and block['role'] in ('figure','table'):
                # Geometry was already bound before this unit was rejected.
                # Its neutral label cannot establish a new pixel match.
                owned = admitted.get(block['id'])
                if owned is not None and owned['role'] == block['role']:
                    block['bbox'] = copy.deepcopy(owned['bbox'])
                # The retained source image owns this rejected graphic. Its
                # discarded wording cannot demand note owners during geometry
                # or leak into live table cells or accessibility descriptions.
                block.update(text='Original source image',marker='',low_confidence=[],
                             continues_previous=False,continues_next=False)
        return answer

    def register_page(self,page,record):
        self.fragment_units[hashlib.sha256(page['anchor'].encode()).hexdigest()]=[dict(page=page['pno'],block=None)]
        for ident in re.findall(r'\bid="([^"]+)"',page['anchor']):
            self.id_units.setdefault(ident,[]).append(dict(page=page['pno'],block=None))
        emitted=[b for b in record.get('blocks',[]) if b['role'] not in ('footnote','running_head','folio')]
        for index,fragment in enumerate(page['body']):
            unit=dict(page=page['pno'],block=emitted[index]['id'] if index<len(emitted) else None)
            self.fragment_units[hashlib.sha256(fragment.encode()).hexdigest()]=[unit]
            for ident in re.findall(r'\bid="([^"]+)"',fragment):
                self.id_units.setdefault(ident,[]).append(unit)
        for fragment in page['asides']:
            ident=re.search(r'<aside\b[^>]*\bid="v4-note-\d+-([^" ]+)"',fragment)
            units=[dict(page=page['pno'],block=ident[1])] if ident else []
            if not units:
                target=re.search(r'<aside\b[^>]*\bid="([^"]+)"',fragment)
                if target:
                    for origin in page['body']:
                        if re.search(r'href="#'+re.escape(target[1])+r'"',origin):
                            units.extend(self.fragment_units.get(hashlib.sha256(origin.encode()).hexdigest(),[]))
            if not units:units=[dict(page=page['pno'],block=None)]
            units=list({(unit['page'],unit.get('block')):unit for unit in units}.values())
            self.fragment_units[hashlib.sha256(fragment.encode()).hexdigest()]=units
            for name in re.findall(r'\bid="([^"]+)"',fragment):
                self.id_units.setdefault(name,[]).extend(units)

    def fragment_owners(self,fragment,anchor=None):
        found=(self.id_units.get(anchor,[]) if anchor else
            self.fragment_units.get(hashlib.sha256(fragment.encode()).hexdigest(),[]))
        if not found and not anchor:
            found=[unit for ident in re.findall(r'\bid="([^"]+)"',fragment) for unit in self.id_units.get(ident,[])]
        if not found:
            found=[dict(page=int(n),block=bid) for n,bid in re.findall(
                r'<span\b[^>]*data-source-page="(\d+)"[^>]*data-source-block="([^"]+)"',fragment)
                if int(n) in self.pages and bid in {b['id'] for b in self.records.get(int(n),{}).get('blocks',[])}]
        if not found:
            found=[dict(page=int(n),block=None) for n in re.findall(r'data-source-page="(\d+)"',fragment) if int(n) in self.pages]
        unique={(u['page'],u.get('block')):u for u in found}
        self.check_units(list(unique.values()))
        return list(unique.values())

    def decorate_report(self, report):
        report['unit_recovery_events'] = copy.deepcopy(self.events)
        rendered=report.get('degraded_units',[])+report.get('degraded_figures',[])
        report['degraded_units'] = [dict(case,**next((r for r in rendered if
            r['page']==case['page'] and r.get('block')==case.get('block')),{}))
            for case in copy.deepcopy(list(self.degraded.values()))]
        for case in report['degraded_units']:
            if not case.get('source_image') or not case.get('source_reference'):
                raise UnitFailure([dict(page=case['page'],block=case.get('block'))],
                    'degradation_reporting',ValueError('Degraded unit has no rendered source image and inspection target'))
        report['degraded_unit_count'] = len(self.degraded)
        report['reread_pages'] = sorted(self.retried)
        if self.ledger:
            report['recovery_financial_state'] = dict(
                start_spend_usd=self.start_spend, cumulative_spend_usd=self.ledger.spent(),
                held_usd=self.ledger.pending_usd())
            report['new_cost_usd'] = self.ledger.spent() - self.start_spend
        for case in report.get('degraded_figures', []):
            if not any(u.get('page') == case['page'] and u.get('block') == case['block']
                       for u in report['degraded_units']):
                report['degraded_units'].append(dict(case, unit_kind='figure'))
        report['degraded_units'].extend(copy.deepcopy(report.get('note_marker_disclosures',[])))
        report['degraded_unit_count'] = len(report['degraded_units'])


def _repair_event(stage,error,evidence):
    controller=current()
    if controller is None:raise error
    controller.events.append(dict(stage=stage,transition='DETERMINISTIC_SOURCE_REPAIR',
        reason=str(error),evidence=evidence))


def chapters(pages):
    """Chapter grouping never earns permission to discard an admitted fragment."""
    from . import build_epub
    from collections import Counter
    admitted=[fragment for page in pages for fragment in
              [page['anchor']]+page['body']+page['asides'] if fragment]
    try:
        result=build_epub._chapters(copy.deepcopy(pages))
        if len({c.href for c in result})!=len(result):
            raise ValueError('Chapter grouping reused a document identity')
        if Counter(block for c in result for block in c.blocks if block)!=Counter(admitted):
            raise ValueError('Chapter grouping changed admitted fragments')
        anchors={page['anchor'] for page in pages if page['anchor']}
        def reading_order(items):
            return [b for b in items if b and b not in anchors and not build_epub._LEADING_NOTICE.match(b)]
        if reading_order([b for c in result for b in c.blocks])!=reading_order(admitted):
            raise ValueError('Chapter grouping changed admitted reading order')
        return result
    except Exception as error:
        if fatal(error):raise
        _repair_event('chapter_grouping',error,'Every admitted fragment retained byte-for-byte, in physical reading order')
        result=[]
        for page in pages:
            blocks=[fragment for fragment in [page['anchor']]+page['body']+page['asides'] if fragment]
            chapter=build_epub.Chapter(index=len(result)+1,title='Page '+str(page['pno']+1),blocks=blocks,pages=[page['pno']])
            result.append(chapter)
        return result


def bind_links(chapters):
    """Relocate only references to a uniquely admitted destination ID."""
    from . import build_epub
    controller=current();home={};orig=copy.deepcopy(chapters)
    for chapter in orig:
        for fragment in chapter.blocks:
            for ident in build_epub._ID.findall(fragment):
                if ident in home:
                    units=(controller.fragment_owners(fragment,ident) if controller else [])
                    if controller:raise UnitFailure(units,'link_target_ownership',ValueError('Duplicate publication target '+ident))
                    raise ValueError('Duplicate publication target '+ident)
                home[ident]=chapter.href
    expected=[]
    for chapter in orig:
        blocks=[]
        for fragment in chapter.blocks:
            def rewrite(match):
                target=match[1]
                if target not in home:
                    start=fragment.rfind('<a',0,match.start());end=fragment.find('>',match.end())
                    anchor=re.search(r'\bid="([^"]+)"',fragment[start:end]) if start>=0 else None
                    if controller:raise UnitFailure(controller.fragment_owners(fragment,anchor[1] if anchor else None),
                        'link_reference',ValueError('No admitted destination for '+target))
                    raise ValueError('No admitted destination for '+target)
                return match[0] if home[target]==chapter.href else 'href="'+home[target]+'#'+target+'"'
            blocks.append(build_epub._HREF.sub(rewrite,fragment))
        expected.append(blocks)
    try:
        trial=copy.deepcopy(orig);unresolved=build_epub._bind_links(trial)
        if unresolved or [c.blocks for c in trial]!=expected:
            raise ValueError('Link binder changed content or failed exact unique-target relocation')
        for chapter,trial_chapter in zip(chapters,trial):chapter.blocks=trial_chapter.blocks
    except UnitFailure:raise
    except Exception as error:
        if fatal(error):raise
        _repair_event('link_binding',error,'Exact admitted IDs uniquely bind every reference; only href relocation changes')
        for chapter,blocks in zip(chapters,expected):chapter.blocks=blocks


def page_homes(chapters):
    from . import build_epub
    expected={}
    for chapter in chapters:
        for block in chapter.blocks:
            for ident in build_epub._ID.findall(block):
                if not re.fullmatch(r'pg_\d+',ident):continue
                page=int(ident[3:])
                if page in expected:
                    raise UnitFailure([dict(page=page,block=None)],'page_anchor',ValueError('Duplicate physical source-page anchor'))
                expected[page]=chapter.href
    controller=current()
    if controller and set(expected)!=controller.pages:
        missing=controller.pages-set(expected)
        if missing:raise UnitFailure([dict(page=n,block=None) for n in sorted(missing)],'page_anchor',ValueError('Physical source-page anchor is absent'))
        raise SourceIntegrityError('Publication page anchor lies outside selected source')
    try:
        actual=build_epub._page_homes(chapters)
        if actual!=expected:raise ValueError('Page home lookup differs from exact admitted anchors')
        return actual
    except Exception as error:
        if fatal(error):raise
        _repair_event('page_homes',error,'Every unique admitted physical page anchor names its actual document')
        return expected


def document(title,body,language='en'):
    """A failed XML envelope can be rebuilt from the exact admitted fragments."""
    from . import build_epub
    from html import escape
    from xml.sax.saxutils import quoteattr
    canonical=('<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        'xml:lang='+quoteattr(language)+' lang='+quoteattr(language)+'>\n'
        '<head><title>'+escape(title or '')+'</title><meta charset="utf-8"/>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        '<body>\n'+body+'\n</body>\n</html>\n')
    expected=ET.fromstring(canonical).find('{http://www.w3.org/1999/xhtml}body')
    try:
        output=build_epub._document(title,body,language)
        actual=ET.fromstring(output).find('{http://www.w3.org/1999/xhtml}body')
        if ET.tostring(actual)!=ET.tostring(expected):
            raise ValueError('XML envelope changed admitted publication fragments')
        return output
    except Exception as error:
        if fatal(error):raise
        controller=current()
        if controller is None:raise
        controller.events.append(dict(stage='packaging_xml',transition='DETERMINISTIC_SOURCE_REPAIR',
            reason=str(error),evidence='Exact admitted XHTML fragments preserved in canonical XML envelope'))
        return canonical


def generated_xml(kind, producer, *args, **kwargs):
    """Rebuild generated EPUB metadata solely from admitted package inputs."""
    from . import build_epub
    from html import escape
    from xml.sax.saxutils import quoteattr
    try:
        result=producer(*args,**kwargs);tree=ET.fromstring(result)
        local=lambda tag:tag.rsplit('}',1)[-1]
        if kind=='opf':
            actual=[(e.get('id'),e.get('href'),e.get('media-type'),e.get('properties')) for e in tree.iter() if local(e.tag)=='item']
            expected=[(i['id'],i['href'],i['type'],i.get('properties')) for i in args[1]]
            refs=[e.get('idref') for e in tree.iter() if local(e.tag)=='itemref']
            if actual!=expected or refs!=args[2]:raise ValueError('OPF changed admitted resource or spine inventory')
        elif kind=='ncx':
            actual=[e.get('src') for e in tree.iter() if local(e.tag)=='content']
            if actual!=[href for href,_ in args[0]]:raise ValueError('NCX changed admitted navigation destinations')
        elif kind=='nav':
            toc=next((e for e in tree.iter() if local(e.tag)=='nav' and e.get('id')=='toc'),None)
            actual=[(e.get('href'),''.join(e.itertext())) for e in toc.iter() if local(e.tag)=='a'] if toc is not None else []
            if actual!=list(args[0]):raise ValueError('Navigation changed admitted document destinations or labels')
            homes=args[2] if len(args)>2 else kwargs.get('page_homes')
            page_list=next((e for e in tree.iter() if local(e.tag)=='nav' and e.get('id')=='page-list'),None)
            actual_pages=[(e.get('href'),''.join(e.itertext())) for e in page_list.iter() if local(e.tag)=='a'] if page_list is not None else []
            expected_pages=[(home+'#pg_%04d'%int(n),'PDF page %d'%(int(n)+1)) for n,home in sorted((homes or {}).items())]
            if actual_pages!=expected_pages:raise ValueError('Navigation changed admitted source page destinations')
        return result
    except Exception as error:
        if fatal(error):raise
        _repair_event('packaging_'+kind,error,'Generated metadata rebuilt from exact admitted resource, document and page inventories')
    if kind=='nav':
        entries,language,homes=args
        items=''.join('<li><a href='+quoteattr(href)+'>'+escape(title)+'</a></li>' for href,title in entries)
        pages=''.join('<li><a href='+quoteattr(href+'#pg_%04d'%n)+'>PDF page '+str(n+1)+'</a></li>' for n,href in sorted(homes.items()))
        return document('Contents','<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'+items+'</ol></nav>'+
            ('<nav epub:type="page-list" id="page-list" hidden="hidden"><h2>Source PDF pages</h2><ol>'+pages+'</ol></nav>' if homes else ''),language)
    if kind=='ncx':
        entries,identifier,title=args;ns='http://www.daisy.org/z3986/2005/ncx/'
        root=ET.Element('{'+ns+'}ncx',version='2005-1');head=ET.SubElement(root,'{'+ns+'}head')
        for name,value in [('uid',identifier),('depth','1'),('totalPageCount','0'),('maxPageNumber','0')]:
            ET.SubElement(head,'{'+ns+'}meta',name='dtb:'+name,content=value)
        ET.SubElement(ET.SubElement(root,'{'+ns+'}docTitle'),'{'+ns+'}text').text=title
        nav=ET.SubElement(root,'{'+ns+'}navMap');orders={}
        for index,(href,title) in enumerate(entries,1):
            order=orders.setdefault(href,len(orders)+1)
            point=ET.SubElement(nav,'{'+ns+'}navPoint',id='nav'+str(index),playOrder=str(order))
            ET.SubElement(ET.SubElement(point,'{'+ns+'}navLabel'),'{'+ns+'}text').text=title
            ET.SubElement(point,'{'+ns+'}content',src=href)
        return ET.tostring(root,encoding='unicode')
    if kind=='opf':
        meta,manifest,spine,identifier,modified=args;ns='http://www.idpf.org/2007/opf';dc='http://purl.org/dc/elements/1.1/'
        root=ET.Element('{'+ns+'}package',{'version':'3.0','unique-identifier':'bookid','prefix':'cwng: '+build_epub.REFLOW_NS})
        metadata=ET.SubElement(root,'{'+ns+'}metadata')
        ET.SubElement(metadata,'{'+dc+'}identifier',id='bookid').text=identifier
        ET.SubElement(metadata,'{'+dc+'}title').text=meta.get('title') or 'Untitled'
        ET.SubElement(metadata,'{'+dc+'}language').text=meta.get('language') or 'en'
        for prop,value in [('dcterms:modified',modified),('cwng:reflow',build_epub.SIDECAR_PATH),('cwng:converter',build_epub.CONVERTER+' '+build_epub.CONVERTER_VERSION)]:
            ET.SubElement(metadata,'{'+ns+'}meta',property=prop).text=value
        for key,tag in [('authors','creator'),('tags','subject')]:
            for value in meta.get(key,[]):ET.SubElement(metadata,'{'+dc+'}'+tag).text=value
        for key in ('publisher','description'):
            if meta.get(key):ET.SubElement(metadata,'{'+dc+'}'+key).text=meta[key]
        inventory=ET.SubElement(root,'{'+ns+'}manifest')
        for item in manifest:
            ET.SubElement(inventory,'{'+ns+'}item',dict(id=item['id'],href=item['href'],**{'media-type':item['type']},**({'properties':item['properties']} if item.get('properties') else {})))
        order=ET.SubElement(root,'{'+ns+'}spine',toc='ncx')
        for ident in spine:ET.SubElement(order,'{'+ns+'}itemref',dict(idref=ident,**({'linear':'no'} if ident in kwargs.get('nonlinear',()) else {})))
        return ET.tostring(root,encoding='unicode')
    raise SourceIntegrityError('Unknown generated packaging configuration')


def _proved_styles(sidecar):
    """Serialize admitted metric scalars independently of fallible CSS helpers."""
    from . import build_epub
    glyphs={};paragraphs={}
    for page in sidecar.get('source_evidence',[]):
        for entry in page.get('glyph_presentation',{}).get('entries',[]):
            metric=entry.get('presentation',{})
            if metric.get('applied'):
                key=metric['id'];glyphs[key]=('.source-glyph-display[data-source-presentation="%s"] img '
                    '{ height:%.8fem !important; width:auto; max-width:none; vertical-align:%.8fem !important; }\n' %
                    (key,metric['height_em'],metric['baseline_offset_em']))
    for page in sidecar.get('paragraph_presentation',[]):
        for entry in page.get('paragraph_presentation',{}).get('entries',[]):
            if entry['displayed']:
                key=entry['id'];paragraphs[key]=('p[data-source-inset="%s"] { margin-left:%.8fem; text-indent:%.8fem; }\n' %
                    (key,entry['left_em'],entry['first_line_em']))
    return build_epub.STYLESHEET+''.join(glyphs[k] for k in sorted(glyphs))+''.join(paragraphs[k] for k in sorted(paragraphs))


def source_support(evidence,homes,chapters):
    """Generated indexes use admitted destinations; original pages keep owners."""
    from . import build_epub
    from html import escape
    def static(href,title,producer,body,expected_links=None):
        try:
            output=producer();tree=ET.fromstring(output)
            if expected_links is not None:
                actual=[(e.get('href'),''.join(e.itertext())) for e in tree.iter()
                        if e.tag.rsplit('}',1)[-1]=='a']
                if actual!=expected_links:
                    raise ValueError('Source index changed admitted physical-page label or destination correspondence')
            return output
        except Exception as error:
            if fatal(error):raise
            _repair_event('source_index_packaging',error,'Index reconstructed from exact admitted source page homes')
            return document(title,body)
    items=''.join('<li><a href="'+escape(home,quote=True)+'#pg_%04d">PDF page %d</a></li>'%(n,n+1) for n,home in sorted(homes.items()))
    originals=''.join('<li><a href="'+escape(record['href'],quote=True)+'">Original PDF page '+str(n+1)+'</a></li>' for n,record in sorted(evidence.items()))
    result={
        build_epub.SOURCE_CHECKS_HREF:static(build_epub.SOURCE_CHECKS_HREF,'Source checks',
            lambda:build_epub._source_checks('en',evidence),
            '<h1>Source checks in the reading text</h1><p>Uncertain lettering, punctuation, notes and PDF links can be checked against their linked original printed pixels. Images retain their printed size; use the original page and Return controls to inspect them.</p>'),
        build_epub.SOURCE_INDEX_HREF:static(build_epub.SOURCE_INDEX_HREF,'Source PDF pages',
            lambda:build_epub._source_index(homes,'en',evidence),
            '<h1>Source PDF pages</h1><ol>'+items+'</ol><h2>Original printed evidence</h2><ol>'+originals+'</ol>',
            [(home+'#pg_%04d'%n,'PDF page %d'%(n+1)) for n,home in sorted(homes.items())]+
            [(record['href'],'Original PDF page '+str(n+1)) for n,record in sorted(evidence.items())])}
    returns={int(ident.removeprefix('source_return_')):c.href+'#'+ident
             for c in chapters for b in c.blocks for ident in build_epub._ID.findall(b)
             if re.fullmatch(r'source_return_\d+',ident)}
    for n,record in evidence.items():
        with guard(n,'original_source_document'):
            result[record['href']]=build_epub._original_document(record,homes[n],'en',returns.get(n))
            ET.fromstring(result[record['href']])
    return result


def verify_degradation(parts,assets):
    """Every counted unit has a rendered source image and working inspection."""
    controller=current()
    if controller is None:return
    ids={}
    for href,text in parts['documents'].items():
        for element in ET.fromstring(text).iter():
            ident=element.get('id')
            if ident:ids.setdefault(ident,[]).append((href,element))
    local=lambda tag:tag.rsplit('}',1)[-1]
    for unit in parts['sidecar'].get('degraded_units',[]):
        with guard(unit['page'],'degradation_visibility',block=unit.get('block')):
            src=unit['source_image'];target=unit['source_reference'];found=ids.get(target,[])
            if src not in assets or len(found)!=1:raise ValueError('Degraded SOURCE target or asset absent')
            href,element=found[0]
            if not any(local(e.tag)=='img' and e.get('src')==src for e in element.iter()):
                raise ValueError('Degraded SOURCE target does not render its source image')
            if not any(local(e.tag) in ('p','figcaption') and ''.join(e.itertext()).strip() for e in element.iter()):
                raise ValueError('Degraded SOURCE image lacks visible disclosure')
            origin=unit.get('source_reference_origin')
            if origin:
                entry=ids.get(origin,[])
                if len(entry)!=1 or entry[0][1].get('href') not in ('#'+target,href+'#'+target):
                    raise ValueError('Degraded SOURCE inspection control is not linked')
                back=entry[0][0]+'#'+origin
                if not any(local(e.tag)=='a' and e.get('href') in ('#'+origin,back) for e in element.iter()):
                    raise ValueError('Degraded SOURCE inspection has no working Return')
            for entry_id,entries in ids.items():
                for entry_href,entry_element in entries:
                    if local(entry_element.tag)!='a' or entry_element.get('href') not in ('#'+target,href+'#'+target):continue
                    if entry_href!=href and entry_element.get('href')=='#'+target:continue
                    if not any(local(e.tag)=='a' and e.get('href') in
                        ((('#'+entry_id,) if entry_href==href else ())+ (entry_href+'#'+entry_id,))
                        for e in element.iter()):
                        raise ValueError('Referenced degraded SOURCE note has no working Return')


def package(out_path, *, source_bytes=0):
    """Keep strict asset admission, plus an independent original-source writer."""
    from . import build_epub
    import posixpath
    import zipfile
    class RecoverablePackage(build_epub._Package):
        def __init__(self,path):
            try:super().__init__(path, source_bytes=source_bytes)
            except Exception as error:
                if fatal(error) or current() is None:raise
                if getattr(self,'_zf',None) is not None:self._zf.close()
                self.path=str(path);self.tmp=self.path+'.tmp'
                Path(self.tmp).parent.mkdir(parents=True,exist_ok=True)
                self.images={};self.aliases={};self.media_types={}
                self.source_bytes=source_bytes;self._image_digests={};self._resource_hashes={}
                self._zf=zipfile.ZipFile(self.tmp,'w',zipfile.ZIP_DEFLATED)
                info=zipfile.ZipInfo('mimetype');info.compress_type=zipfile.ZIP_STORED
                self._zf.writestr(info,b'application/epub+zip')
                _repair_event('package_initialization',error,'Fresh archive initialized with the exact EPUB mimetype; no source unit consumed')
            self._hashes={}

        def image(self,href,data,*,store=False):
            digest=hashlib.sha256(data).hexdigest()
            actual=self.aliases.get(href,href)
            if actual in self.images and self._hashes.get(actual)==digest:
                controller=current()
                if controller:controller.events.append(dict(stage='asset_packaging',
                    transition='DETERMINISTIC_SOURCE_REPAIR',image=actual,
                    evidence='Repeated asset bytes have identical SHA256',sha256=digest))
                return
            # Distinct ordinary disclosure regions retain their own ownership
            # records, even when their pixels happen to be identical. Repeated
            # original-page resources share bytes through source_image().
            super().image(href,data,store=store)
            self._hashes[self.aliases.get(href,href)]=digest

        def source_image(self,href,data):
            # Semantic crop/image helpers cannot prevent the trusted original
            # unit from being retained. Actual archive I/O failures still fail.
            requested=href
            href=build_epub._encoded_image_href(href,data)
            digest=hashlib.sha256(data).hexdigest()
            actual=self.aliases.get(requested,href)
            if actual in self.images:
                if self._hashes.get(actual)==digest:return
                raise SourceIntegrityError('Original source asset changed during an assembly attempt')
            if not (data.startswith(b'\xff\xd8') or data.startswith(b'\x89PNG\r\n\x1a\n')):
                raise SourceIntegrityError('Original source renderer did not produce an image')
            existing=self._image_digests.get(digest)
            if existing is not None:
                self.aliases[requested]=existing
                return
            self._zf.writestr(posixpath.join(build_epub.OEBPS,href),data)
            self._image_digests[digest]=href
            self.images[href]=len(data)
            self.media_types[href]='image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
            self._hashes[href]=digest
            if requested!=href:self.aliases[requested]=href

        def finish(self,parts):
            # Prove the exact inputs before trying a fallible packaging helper.
            for text in list(parts['documents'].values())+[parts['opf'],parts['nav'],parts['ncx']]:
                ET.fromstring(text)
            json.dumps(parts['sidecar'],ensure_ascii=False)
            verify_degradation(parts,self.images)
            backup=self.tmp+'.admitted'
            with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as saved:
                for href in self.images:
                    saved.writestr(posixpath.join(build_epub.OEBPS,href),self._zf.read(posixpath.join(build_epub.OEBPS,href)))
            try:
                result=super().finish(copy.deepcopy(parts))
                with zipfile.ZipFile(self.path) as published:
                    info=published.infolist()
                    if not info or info[0].filename!='mimetype' or info[0].compress_type!=zipfile.ZIP_STORED or published.read('mimetype')!=b'application/epub+zip':
                        raise ValueError('Package changed admitted EPUB mimetype')
                    for href,text in parts['documents'].items():
                        if published.read('OEBPS/'+href)!=text.encode('utf-8'):
                            raise ValueError('Package changed admitted document '+href)
                    for name in ('opf','nav','ncx'):
                        href={'opf':'content.opf','nav':'nav.xhtml','ncx':'toc.ncx'}[name]
                        if published.read('OEBPS/'+href)!=parts[name].encode('utf-8'):
                            raise ValueError('Package changed admitted metadata '+name)
                    for href in self.images:
                        if hashlib.sha256(published.read('OEBPS/'+href)).hexdigest()!=self._hashes[href]:
                            raise ValueError('Package changed admitted image '+href)
                    if json.loads(published.read(build_epub.SIDECAR_PATH))!=json.loads(json.dumps(parts['sidecar'],ensure_ascii=False)):
                        raise ValueError('Package changed admitted disclosure report')
                os.unlink(backup)
                return result
            except Exception as error:
                if fatal(error):
                    self.abandon()
                    for candidate in (backup,self.tmp+'.repaired'):
                        try:os.unlink(candidate)
                        except FileNotFoundError:pass
                    raise
                controller=current()
                if controller is None:raise
                controller.events.append(dict(stage='packaging',transition='DETERMINISTIC_SOURCE_REPAIR',
                    reason=str(error),evidence='All admitted XML documents and assets retained byte-for-byte in fresh archive'))
                controller.decorate_report(parts['sidecar'])
                # Preserve admitted image bytes, including members written by a
                # finish helper before it failed. They are read one at a time.
                self._zf.close()
                replacement=self.tmp+'.repaired'
                admitted_archive=backup
                with zipfile.ZipFile(admitted_archive) as prior,zipfile.ZipFile(replacement,'w',zipfile.ZIP_DEFLATED) as fresh:
                    info=zipfile.ZipInfo('mimetype');info.compress_type=zipfile.ZIP_STORED
                    fresh.writestr(info,b'application/epub+zip')
                    for href in self.images:
                        fresh.writestr(posixpath.join(build_epub.OEBPS,href),prior.read(posixpath.join(build_epub.OEBPS,href)))
                    fresh.writestr('META-INF/container.xml','<?xml version="1.0" encoding="utf-8"?>'
                        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
                    fresh.writestr(build_epub.SIDECAR_PATH,json.dumps(parts['sidecar'],ensure_ascii=False,indent=1))
                    for name,key in [('content.opf','opf'),('nav.xhtml','nav'),('toc.ncx','ncx')]:
                        fresh.writestr('OEBPS/'+name,parts[key])
                    fresh.writestr('OEBPS/style.css',_proved_styles(parts['sidecar']))
                    for href,text in parts['documents'].items():fresh.writestr('OEBPS/'+href,text)
                from .image_budget import check
                try:check(self.source_bytes,os.path.getsize(replacement))
                except BaseException:
                    self.abandon()
                    for candidate in (backup,replacement):
                        try:os.unlink(candidate)
                        except FileNotFoundError:pass
                    raise
                os.replace(replacement,self.path)
                if os.path.exists(self.tmp):os.unlink(self.tmp)
                os.unlink(backup)
                return self.path
    return RecoverablePackage(out_path)
