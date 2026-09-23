"""Private job-journal trace of validated plans and observed EPUB wrappers.

No text/images are retained. Publication is a separate receipt: an emitted
candidate is not a published book. Existing owner/library access and financial
journal retention apply; this module does not participate in source preparation.
"""
import hashlib
import zipfile
from xml.etree import ElementTree as ET

VERSION = 'applied-source-operations-1'
N = '{http://www.w3.org/1999/xhtml}'


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _normal(text):
    return ''.join(text.split())


def capture(result, book_id):
    """Return journal-safe rows plus transient matching material; no rereading PDF."""
    rows, matching = [], {}
    for plan in getattr(result, 'operation_plans', ()) or ():
        prepared = plan.prepared
        candidates = {c['candidate_id']: c for c in prepared.candidates()}
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
    return rows, matching


def observe(path, rows, matching):
    """Inspect final chapter XHTML, scoped by its PDF-page markers.

    Ambiguous or missing wrappers are reported as unverified, never promoted to
    emitted merely because the model approved them. Text comparisons preserve all
    non-whitespace characters; qualified uncertain markers remain in the EPUB.
    """
    wanted = {(r['page_index0'],r['proposed_state']) for r in rows}
    blocks = {}
    with zipfile.ZipFile(path) as archive:
        opf=ET.fromstring(archive.read('OEBPS/content.opf'))
        ns='{http://www.idpf.org/2007/opf}'
        manifest={e.get('id'):e.get('href') for e in opf.findall(ns+'manifest/'+ns+'item')}
        page=None
        for item in opf.findall(ns+'spine/'+ns+'itemref'):
            href=manifest[item.get('idref')]
            if not href.startswith('ch') or not href.endswith('.xhtml'):continue
            name='OEBPS/'+href
            root=ET.fromstring(archive.read(name))
            for ordinal,node in enumerate(root.iter()):
                ident=node.get('id','')
                if node.get('class')=='reflow-page' and ident.startswith('pg_'):
                    page=int(ident[3:])
                tag=node.tag.removeprefix(N)
                if (page,tag) in wanted:blocks.setdefault((page,tag),[]).append((name,ordinal,node))
    observed=[]
    for row in rows:
        text,uncertain=matching[row['candidate_id']]
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
            if _normal(visible(node))==_normal(text):found.append((name,ordinal))
        entry={'candidate_id':row['candidate_id'],'page_index0':row['page_index0'],
               'status':'verified' if len(found)==1 else 'ambiguous' if found else 'not_found',
               'matches':len(found)}
        if len(found)==1:entry.update(epub_entry=found[0][0],element_ordinal=found[0][1],role=row['proposed_state'])
        observed.append(entry)
    return observed


def record(ledger, event, **fields):
    ledger.record(dict(kind='operation_audit',schema=VERSION,event=event,**fields),durable=True)
