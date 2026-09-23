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


def serve(parent, cache_root, control):
    import pymupdf
    from . import pipeline, extract, structural_ops, build_epub, report, source_assessment
    faulthandler.enable(all_threads=True)
    root = Path.cwd(); doc = None; sequence = 0; fingerprint = None; operation_book = None
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
    try:
        for line in sys.stdin:
            if stop(): break
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
                        allowed = {'page_numbers', 'require_figure_caption', 'recovery_opts'}
                        if set(args) - allowed: raise ValueError('prepare fields')
                        opts = args.setdefault('recovery_opts', {})
                        if set(opts) - {'mode', 'language', 'dpi'}: raise ValueError('recovery fields')
                        opts.update(cache_dir=str(Path(cache_root) / 'ocr-cache') if cache_root else str(root / 'ocr-cache'),
                                    scratch_dir=str(root / 'ocr-scratch'))
                        value = pipeline.run(doc, client=None, progress=progress, should_stop=stop, **args)
                    elif op == 'operations':
                        allowed = {'book', 'pno', 'revision', 'source_layer', 'seed', 'max_candidates', 'max_context_chars', 'raw_page', 'source_bound'}
                        if set(args) - allowed: raise ValueError('operation fields')
                        book = args.pop('book'); pno = args.pop('pno'); layer = args.pop('source_layer')
                        if book is not None: operation_book = book
                        book = operation_book
                        if book is None: raise ValueError('missing native book context')
                        from .enriched_source import prepare_source_page
                        bound = args.pop('source_bound')
                        if type(bound) is not bool: raise ValueError('source authority flag')
                        canonical = prepare_source_page(book, pno, layer, layer.get('uncertain', ())) if bound else None
                        value = structural_ops.prepare(book, doc, pno, source_layer=layer,
                                                       source_page=canonical, **args)
                        value = replace(value, source_page=None)
                        # Factories use weak-key authority. Do not retain an equal
                        # prior canonical object across the next command's factory.
                        canonical = None
                    elif op == 'survey':
                        if args: raise ValueError('survey fields')
                        value = source_assessment.survey(doc)
                    elif op == 'build':
                        allowed = {'book', 'page_html', 'metadata', 'report_html', 'sidecar', 'identifier',
                                   'source_pages', 'operation_plans', 'recovery'}
                        if set(args) - allowed: raise ValueError('build fields')
                        book = args.pop('book'); recovery = args.pop('recovery')
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
            # Do not retain a whole preparation graph while the parent decodes
            # its reply. Only the explicitly bound operation Book stays live.
            value = reply = None
            emit({'ready': True})
    finally:
        if doc is not None: doc.close()
    return 0
