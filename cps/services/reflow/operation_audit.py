"""Private job-journal trace of validated plans and observed EPUB wrappers.

No text/images are retained. Publication is a separate receipt: an emitted
candidate is not a published book. Existing owner/library access and financial
journal retention apply; this module does not participate in source preparation.
"""
import hashlib
import json
import zipfile
from xml.etree import ElementTree as ET

VERSION = 'applied-source-operations-2'
N = '{http://www.w3.org/1999/xhtml}'


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _normal(text):
    return ''.join(text.split())


def capture(result, book_id, document=None):
    """Return journal rows plus transient matching material from current source.

    With a native document, selected image atoms are independently rendered and
    hashed. NativeDocument dispatches this work into its supervised child."""
    if getattr(result, 'layout_plans', None):
        from . import layout_audit
        return layout_audit.capture(result, book_id, document)
    if document is not None and hasattr(document, 'capture_operation_audit'):
        return document.capture_operation_audit(result, book_id)
    if document is not None and not hasattr(document, '__getitem__'):
        raise ValueError('native document requires supervised operation-audit capture')
    rows, matching = [], {}
    displays, resources = {}, {}
    for plan in getattr(result, 'operation_plans', ()) or ():
        prepared = plan.prepared
        candidates = {c['candidate_id']: c for c in prepared.candidates()}
        compiled = None
        for identifier in plan.selected:
            candidate = candidates[identifier]
            element = result.book.pages[prepared.page][int(candidate['element_id'][1:])]
            start, end = candidate['source_range']
            text = ''.join(str(run[1]) for run in element.runs)[start:end]
            row = dict(candidate, book_id=book_id, page_index0=prepared.page,
                       source_pdf_sha256=prepared.pdf_digest, snapshot_id=prepared.snapshot_id,
                       selected_text_sha256=_hash(text), source_revision=prepared.revision,
                       raster_sha256=hashlib.sha256(prepared.raster).hexdigest(),
                       requests=[{k:r[k] for k in ('stage','request_sha256','cached','attempt') if k in r}
                                 for r in getattr(result,'stage_records',()) if r['page']==prepared.page])
            rows.append(row)
            matching[identifier] = (text, {str(r[1]) for r in element.runs
                                         if r[0]!='t' and 'uncertain' in r[2:]})
            from .structural_ops import _slice
            selected_runs = _slice(element.runs, start, end)
            if any(run[0] == 'glyph' for run in selected_runs):
                # Missing current native source is deliberately unverified. A
                # logical placeholder is never substituted for image content.
                matching[identifier] = None
                if document is None:
                    continue
                from . import build_epub, enriched_source, native_text
                from .source_display import SourceDisplay
                source = prepared.source_page
                if source is None:
                    continue
                if compiled is None:
                    compiled = plan.compile(result.book, document, source_page=source)
                source.validate(result.book)
                index = int(candidate['element_id'][1:])
                spec = next(s for s in compiled[index] if (s.start, s.end) == (start, end))
                block = build_epub.split_blocks(source.html)[json.loads(source.blocks_json)[str(index)]]
                fragment = enriched_source.wrap(block, element, [spec])
                tree = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+'</root>')
                wrappers = list(tree.iter(row['proposed_state']))
                if len(wrappers) != 1:
                    raise ValueError('selected source wrapper is not unique')
                expected = {}
                aliases = {}
                for run in selected_runs:
                    if run[0] != 'glyph':
                        continue
                    descriptor = run[2]
                    original = native_text.image_name(descriptor)
                    if original not in resources:
                        page = descriptor['page']
                        if page not in displays:
                            displays[page] = SourceDisplay(document, page)
                        display = displays[page]
                        rect = display.reading_rect(descriptor['bbox'])
                        renderer = getattr(display, 'source_image', display.jpeg)
                        blob = renderer(rect, scale=6, quality=95)
                        actual = original[:-4]+'.png' if blob.startswith(b'\x89PNG\r\n\x1a\n') else original
                        resources[original] = (actual, hashlib.sha256(blob).hexdigest())
                    actual, digest = resources[original]
                    aliases[original] = actual
                    expected[actual] = digest
                for image in wrappers[0].iter('img'):
                    image.set('src', aliases[image.get('src')])
                # The transient proof contains no pixels or inferred Unicode.
                # It is minted only after current source/plan admission above.
                matching[identifier] = {'projection': _projection(wrappers[0]),
                                        'resources': expected}
                row['source_atoms_sha256'] = _hash(json.dumps(matching[identifier], sort_keys=True))
    return rows, matching


def _projection(node):
    """Ordered text and exact immutable glyph subtrees, never alt-as-text."""
    parts = []
    def append_text(value):
        text = _normal(value or '')
        if text:
            if parts and parts[-1][0] == 'text':
                parts[-1][1] += text
            else:
                parts.append(['text', text])
    def shape(element):
        return [element.tag.removeprefix(N), sorted(map(list, element.attrib.items())),
                element.text or '', [[shape(child), child.tail or ''] for child in element]]
    def visit(element, ancestors=()):
        if element.tag.removeprefix(N) == 'a' and element.get('class') == 'source-glyph':
            parts.append(['glyph', list(ancestors), shape(element)])
            return
        if element.tag.removeprefix(N) == 'img':
            # An unrelated image cannot disappear into the text projection.
            parts.append(['unbound-image', shape(element)])
            return
        append_text(element.text)
        for child in element:
            visit(child, (*ancestors, [element.tag.removeprefix(N), sorted(map(list, element.attrib.items()))]))
            append_text(child.tail)
    visit(node)
    return parts


def observe(path, rows, matching):
    """Inspect final chapter XHTML, scoped by its PDF-page markers.

    Ambiguous or missing wrappers are reported as unverified, never promoted to
    emitted merely because the model approved them. Text comparisons preserve all
    non-whitespace characters; qualified uncertain markers remain in the EPUB.
    """
    if rows and all(r.get('kind')=='layout' for r in rows):
        from . import layout_audit
        return layout_audit.observe(path, rows, matching)
    wanted = {(r['page_index0'],r['proposed_state']) for r in rows}
    blocks = {}
    with zipfile.ZipFile(path) as archive:
        opf=ET.fromstring(archive.read('OEBPS/content.opf'))
        ns='{http://www.idpf.org/2007/opf}'
        manifest={e.get('id'):e.get('href') for e in opf.findall(ns+'manifest/'+ns+'item')}
        for item in opf.findall(ns+'spine/'+ns+'itemref'):
            href=manifest[item.get('idref')]
            if not href.startswith('ch') or not href.endswith('.xhtml'):continue
            # A chapter can begin with a continuation and no local marker.
            # Do not invent source-page ownership from the preceding file.
            page=None
            name='OEBPS/'+href
            root=ET.fromstring(archive.read(name))
            for ordinal,node in enumerate(root.iter()):
                ident=node.get('id','')
                if node.get('class')=='reflow-page' and ident.startswith('pg_'):
                    page=int(ident[3:])
                tag=node.tag.removeprefix(N)
                if (page,tag) in wanted:blocks.setdefault((page,tag),[]).append((name,ordinal,node))
        resource_hashes = {}
        for material in matching.values():
            if not isinstance(material, dict):
                continue
            for href in material['resources']:
                name = 'OEBPS/'+href
                if archive.namelist().count(name) != 1:
                    resource_hashes[href] = None
                else:
                    resource_hashes[href] = hashlib.sha256(archive.read(name)).hexdigest()
    observed=[]
    for row in rows:
        material=matching[row['candidate_id']]
        text,uncertain=material if isinstance(material, tuple) else ('', set())
        found=[]
        for name,ordinal,node in blocks.get((row['page_index0'],row['proposed_state']),[]):
            # Rendering adds a visible qualification only to known uncertain
            # atomic source markers. Preserve it; remove it only for comparison.
            def visible(element):
                value=element.text or ''
                if (element.tag==N+'span' and element.get('class')=='reflow-uncertain'
                        and element.get('title')=='number or association read from a damaged text layer'
                        and value in {n+' (?)' for n in uncertain}):value=value[:-4]
                for child in element:value+=visible(child)+(child.tail or '')
                return value
            if isinstance(material, dict):
                expected = material['resources']
                if (row.get('source_atoms_sha256') == _hash(json.dumps(material, sort_keys=True))
                        and expected and all(resource_hashes.get(href) == digest for href,digest in expected.items())
                        and _projection(node) == material['projection']):
                    found.append((name,ordinal))
            elif material is not None and not any(n.tag == N+'img' for n in node.iter()):
                if _normal(visible(node))==_normal(text):found.append((name,ordinal))
        entry={'candidate_id':row['candidate_id'],'page_index0':row['page_index0'],
               'status':'verified' if len(found)==1 else 'ambiguous' if found else 'not_found',
               'matches':len(found)}
        if len(found)==1:entry.update(epub_entry=found[0][0],element_ordinal=found[0][1],role=row['proposed_state'])
        observed.append(entry)
    return observed


def record(ledger, event, **fields):
    ledger.record(dict(kind='operation_audit',schema=VERSION,event=event,**fields),durable=True)
