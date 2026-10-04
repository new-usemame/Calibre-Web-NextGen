"""Fixed native operations. No provider dispatch or publication capability."""
from dataclasses import replace
import faulthandler
import json
import os
from pathlib import Path
import sys

from . import native_codec as codec, native_ipc as ipc


def sources(book, supplied):
    from .enriched_source import prepare_source_page
    result = {}
    for pno, old in (supplied or {}).items():
        current = prepare_source_page(book, pno, json.loads(old.provenance_json),
                                      json.loads(old.records_json))
        if current.identity != old.identity or current.html != old.html:
            raise ValueError('source page changed across native transport')
        result[pno] = current
    return result


def _shutdown_document(doc, control, sequence, reason):
    """Bounded, text-free close phases; slow exits get one stderr stack dump.

    These frames follow the final command reply. They are diagnostic only and
    cannot make a failed shutdown successful. The watchdog ends with this fresh
    interpreter, including if Python finalization stalls after this returns.
    """
    import time
    def phase(name):
        try:
            control.write(json.dumps({'seq': sequence, 'shutdown_phase': name,
                'monotonic_ns': time.monotonic_ns(), 'cpu_ns': time.process_time_ns()}) + '\n')
            control.flush()
        except (OSError, ValueError):
            pass  # Lost diagnostic channel must not prevent native cleanup.
    phase(reason)
    try:
        faulthandler.dump_traceback_later(1.0, repeat=False, file=sys.stderr)
    except (OSError, ValueError, RuntimeError):
        pass
    phase('document_close_started')
    if doc is not None:
        doc.close()
    phase('document_closed')
    phase('runtime_returning')


def serve(parent, cache_root, control):
    import pymupdf
    from . import pipeline, extract, structural_ops, build_epub, report, source_assessment
    faulthandler.enable(all_threads=True)
    root = Path.cwd(); doc = None; sequence = 0; fingerprint = None; operation_book = None
    audit_pages = {}
    layout_authority = None
    def stop(): return os.getppid() != parent
    def emit(row):
        control.write(json.dumps(dict(seq=sequence, **row), separators=(',', ':')) + '\n')
        control.flush()
        if 'progress' in row:
            if sys.stdin.readline(32) != 'continue\n' or stop():
                raise build_epub.BuildCancelled('parent stopped native work')
    def progress(event):
        emit({'progress': {'stage': str(event.stage)[:48], 'page': int(event.page),
                           'pages': int(event.pages)}})
    end_reason = 'command_loop_error'
    try:
        for line in sys.stdin:
            if stop():
                end_reason = 'parent_gone'
                break
            if len(line) > 32 or int(line) != sequence + 1: raise ValueError('native sequence')
            sequence += 1
            request = codec.loads(ipc.read_owned(root, 'request.json'))
            if not isinstance(request, dict) or set(request) != {'seq', 'operation', 'arguments'} or request['seq'] != sequence:
                raise ValueError('native request schema')
            op, args = request['operation'], request['arguments']
            if not isinstance(args, dict): raise ValueError('native arguments')
            try:
                if op == 'open':
                    if doc is not None or set(args) != {'fingerprint'}: raise ValueError('native open')
                    fingerprint = extract.document_fingerprint(root / 'source.pdf')
                    if fingerprint != args['fingerprint']: raise ValueError('source fingerprint')
                    doc = pymupdf.open(root / 'source.pdf'); value = len(doc)
                else:
                    if doc is None or extract.document_fingerprint(root / 'source.pdf') != fingerprint:
                        raise ValueError('source identity changed')
                    if op == 'prepare':
                        audit_pages.clear(); operation_book = None; layout_authority = None
                        allowed = {'page_numbers', 'require_figure_caption', 'recovery_opts', 'visual_results'}
                        if set(args) - allowed: raise ValueError('prepare fields')
                        opts = args.setdefault('recovery_opts', {})
                        if set(opts) - {'mode', 'language', 'dpi'}: raise ValueError('recovery fields')
                        (root / 'ocr-scratch').mkdir(mode=0o700, exist_ok=True)
                        opts.update(cache_dir=str(Path(cache_root) / 'ocr-cache') if cache_root else str(root / 'ocr-cache'),
                                    scratch_dir=str(root / 'ocr-scratch'))
                        value = pipeline.run(doc, client=None, progress=progress, should_stop=stop, **args)
                        from .layout_native import SourceAuthority
                        layout_authority = SourceAuthority(value)
                    elif op == 'readings_prepare':
                        from . import source_readings
                        if layout_authority is None or set(args)!={'book_digest','page','source_identity','raw_digest','selectors','synthetic'}:
                            raise ValueError('readings require current native preparation')
                        if args['book_digest']!=layout_authority.digest:raise ValueError('reading book differs')
                        pno=args['page'];canonical=layout_authority.source(pno);raw=layout_authority.raws[pno]
                        from .structural_ops import _digest
                        from dataclasses import asdict
                        if canonical.identity!=args['source_identity'] or _digest(asdict(raw))!=args['raw_digest']:
                            raise ValueError('reading source differs')
                        value=source_readings._inputs(layout_authority.result.book,doc,canonical,raw,args['selectors'],args['synthetic'])
                    elif op == 'layout_prepare':
                        if layout_authority is None:raise ValueError('layout requires native source preparation')
                        value = layout_authority.prepare(doc,args)
                    elif op == 'layout_figures':
                        if layout_authority is None:raise ValueError('layout requires native source preparation')
                        value = layout_authority.figure_failures(doc,args,should_stop=stop)
                    elif op == 'operations':
                        allowed = {'book', 'pno', 'revision', 'source_layer', 'seed', 'max_candidates', 'max_context_chars', 'raw_page', 'source_bound'}
                        if set(args) - allowed: raise ValueError('operation fields')
                        book = args.pop('book'); pno = args.pop('pno'); layer = args.pop('source_layer')
                        if book is not None:
                            operation_book = book; audit_pages.clear()
                        book = operation_book
                        if book is None: raise ValueError('missing native book context')
                        from .enriched_source import prepare_source_page
                        bound = args.pop('source_bound')
                        if type(bound) is not bool: raise ValueError('source authority flag')
                        canonical = prepare_source_page(book, pno, layer, layer.get('uncertain', ())) if bound else None
                        value = structural_ops.prepare(book, doc, pno, source_layer=layer,
                                                       source_page=canonical, **args)
                        value = replace(value, source_page=None)
                        if value.specs:
                            # Store only child-minted prepared candidates, not
                            # all-page rasters in RAM. Paths are child-generated.
                            cache = root / ('audit-%d' % sequence)
                            cache.mkdir(mode=0o700)
                            ipc.write_owned(cache, 'response.json', dict(prepared=value, source=canonical))
                            audit_pages[pno] = cache
                        else: audit_pages.pop(pno, None)
                        # Factories use weak-key authority. Do not retain an equal
                        # prior canonical object across the next command's factory.
                        canonical = None
                    elif op == 'operation_audit':
                        from types import SimpleNamespace
                        from . import native_audit, operation_audit
                        native_audit.request(args, fingerprint, len(doc))
                        if operation_book is None: raise ValueError('missing native audit book')
                        plans = []
                        for selected in args['operations']:
                            pno = selected['page']
                            if pno not in audit_pages: raise ValueError('unprepared native audit page')
                            saved = codec.loads(ipc.read_owned(audit_pages[pno], 'response.json'))
                            old = saved['source']
                            canonical = sources(operation_book, {pno: old})[pno] if old is not None else None
                            prepared = replace(saved['prepared'], source_page=canonical)
                            if (prepared.snapshot_id != selected['snapshot_id'] or
                                    (canonical.identity if canonical is not None else None) != selected['source_identity']):
                                raise ValueError('stale native audit source snapshot')
                            plans.append(prepared.accept(operation_book, doc, dict(protocol=structural_ops.PROTOCOL,
                                snapshot_id=selected['snapshot_id'], select=selected['selected']), source_page=canonical))
                        value = operation_audit.capture(SimpleNamespace(book=operation_book,
                            operation_plans=plans, stage_records=args['stage_records']), args['book_id'], document=doc)
                        native_audit.bounded(value)
                        plans = []; canonical = prepared = saved = old = None
                    elif op == 'survey':
                        if args: raise ValueError('survey fields')
                        value = source_assessment.survey(doc)
                    elif op == 'build':
                        allowed = {'book', 'page_html', 'metadata', 'report_html', 'sidecar', 'identifier',
                                   'source_pages', 'operation_plans', 'recovery', 'layout_plans', 'raw_pages'}
                        if set(args) - allowed: raise ValueError('build fields')
                        book = args.pop('book'); recovery = args.pop('recovery')
                        region_bound = bool(getattr(book, 'source_region_protection', {})) or bool(
                            layout_authority is not None and getattr(
                                layout_authority.result.book, 'source_region_protection', {}))
                        reading_bound=any(s.report().get('source_readings') for s in (args.get('source_pages') or {}).values())
                        if args.get('layout_plans') or region_bound or reading_bound:
                            if layout_authority is None:raise ValueError('layout publication requires native source preparation')
                            book,canonical,raws = layout_authority.publication(book,args.get('source_pages'),args.get('layout_plans') or (),args.get('page_html'),doc=doc)
                            args['raw_pages'] = raws
                        else:
                            canonical = sources(book, args.get('source_pages'))
                        args['source_pages'] = canonical
                        args['operation_plans'] = [replace(plan, prepared=replace(plan.prepared,
                            source_page=canonical.get(plan.prepared.page))) for plan in args.get('operation_plans') or ()]
                        recipe = args.get('report_html')
                        if isinstance(recipe, dict):
                            if set(recipe) != {'payload', 'show_cost'}: raise ValueError('report recipe')
                            args['report_html'] = lambda links, losses: report.about_page(
                                recipe['payload'], show_cost=recipe['show_cost'], links=links, losses=losses)
                        value = build_epub.build(book, str(root / 'candidate.epub'), doc=doc,
                            figure_transform=recovery.figure_rect if recovery else None,
                            evidence_progress=lambda done, total: emit({'progress': {
                                'kind': 'evidence_progress', 'done': done, 'total': total}}),
                            should_stop=stop, runtime_progress=lambda event: emit({'progress': event}), **args)
                        value.path = 'candidate.epub'
                    else: raise ValueError('unknown native operation')
                reply = {'seq': sequence, 'ok': True, 'value': value}
            except Exception as exc:
                import traceback
                traceback.print_exc(file=sys.stderr)
                # No extracted text or arbitrary paths cross the failure channel.
                reply = {'seq': sequence, 'ok': False, 'value': type(exc).__name__}
            ipc.write_owned(root, 'response.json', reply)
            # A factory seal belongs to the current operation, not its equal
            # JSON value. Drop every command-local source/plan reference before
            # the next command reissues weak-key authority, including after a
            # failed operation. The explicit operation Book/cache remains live.
            request = args = canonical = prepared = saved = old = plans = None
            # Do not retain a whole preparation graph while the parent decodes
            # its reply. Only the explicitly bound operation Book stays live.
            value = reply = None
            emit({'ready': True})
        else:
            end_reason = 'stdin_eof'
    finally:
        _shutdown_document(doc, control, sequence, end_reason)
    return 0
