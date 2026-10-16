"""Private layout evidence: source-validated plans and observed emitted groups."""
import hashlib
import json
from xml.etree import ElementTree as ET
from . import layout_ops


def capture(result,book_id,document):
    if document is None:raise ValueError('layout audit requires current source document')
    rows=[];matching={};raws={r.pno:r for r in result.raw_pages}
    for plan in result.layout_plans:
        p=plan.prepared;source=result.source_pages[p.page]
        context={}
        if json.loads(p.contract_json).get('previous'):
            context=dict(previous_source=result.source_pages[p.page-1],previous_raw=raws[p.page-1])
        compiled=plan.compile(result.book,document,source_page=source,raw_page=raws[p.page],**context)
        identifier='layout-'+p.snapshot_id
        row=dict(candidate_id=identifier,kind='layout',book_id=book_id,page_index0=p.page,
            verification_scope='emitted textual group order; image groups require separate pixel evidence',
            source_pdf_sha256=p.pdf_digest,snapshot_id=p.snapshot_id,source_identity=p.source_identity,
            answer_sha256=hashlib.sha256(plan.answer_json.encode()).hexdigest(),
            selected_text_sha256=hashlib.sha256((compiled.expected_body+'\n'+compiled.expected_furniture).encode()).hexdigest(),
            raster_sha256=hashlib.sha256(p.raster).hexdigest(),
            requests=[{k:r[k] for k in ('stage','request_sha256','cached','attempt') if k in r}
                for r in result.stage_records if r['page']==p.page or r['stage'] in ('ordering','lexical')])
        # Use the existing independent wrapper observer. Image-only groups need
        # a separate pixel proof and remain explicitly unverified here.
        groups=[]
        for ordinal,node in enumerate(layout_ops._tree(compiled.page_html)):
            if node.get('class')=='reflow-retained-furniture':
                nodes=list(node)
            else:nodes=[node]
            for child in nodes:
                groups.append(dict(candidate_id=identifier+'-'+str(len(groups)),page_index0=p.page,
                    proposed_state=child.tag.split('}')[-1],text=''.join(child.itertext()),
                    has_image=any(n.tag.split('}')[-1]=='img' for n in child.iter())))
        rows.append(row);matching[identifier]=groups
    return rows,matching


def observe(path,rows,matching):
    from . import operation_audit
    group_rows=[];materials={}
    for row in rows:
        for group in matching[row['candidate_id']]:
            group_rows.append({k:group[k] for k in ('candidate_id','page_index0','proposed_state')})
            materials[group['candidate_id']]=None if group['has_image'] else (group['text'],set())
    observed=operation_audit.observe(path,group_rows,materials)
    by_id={o['candidate_id']:o for o in observed};results=[]
    for row in rows:
        groups=[by_id[g['candidate_id']] for g in matching[row['candidate_id']]]
        all_verified=bool(groups) and all(g['status']=='verified' for g in groups)
        positions=[(g['epub_entry'],g['element_ordinal']) for g in groups] if all_verified else []
        # Groups may span chapter files; preserve spine occurrence ordering.
        ordered=all(a<b for a,b in zip(positions,positions[1:]))
        item=dict(candidate_id=row['candidate_id'],page_index0=row['page_index0'],
                  status='verified' if all_verified and ordered else 'unverified',groups=groups)
        if item['status']=='verified':item['epub_entry']=groups[0]['epub_entry']
        results.append(item)
    return results
