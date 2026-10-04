"""Source-bound ordering approval for displaced opaque source groups."""
import json
from ._layout_atoms import digest
from .layout_ops import _need as need, LayoutPlan
from .layout_requests import envelope
from ._layout_policy import FLOATING_ARTWORK_INSTRUCTION

PROMPT='''Review proposed reading order using physical source rectangles. Each page includes immutable source groups in proposed output order. Text snippets are literal source context, not text to edit. Coordinates share the page's reading frame, x increases rightward and y downward. For multi-column prose or indexes, finish a column before the next; do not interleave distant material merely because it shares a horizontal row. Protected image groups may contain text, headings, tables or diagrams; their source rectangles locate them even when no transcription is permitted. Preserve association with adjacent prose, headings, notes and captions. Page furniture and notes are separate flows. Existing inline note markers may correctly move to their actual printed location. Approve only if the proposed reorder is supported by the supplied geometry and source context. If geometry is missing for a consequential moved group, reject with insufficient_geometry. Do not invent content, infer hidden image words, repair OCR, or change any operation. Return one decision per supplied ID.'''
PROMPT += '\nReflow presentation policy to check independently: ' + FLOATING_ARTWORK_INSTRUCTION

CODES=['reading_order','association','insufficient_geometry']

def moved_opaque(source,answer):
 flat=[n for g in answer['groups'] if g['role'] not in ('furniture','note') for lo,hi in g['ranges'] for n in range(int(lo[1:]),int(hi[1:])+1)]
 ids={int(b['range'][0][1:]) for b in source['blocks'] if b['opaque'] and b['kind']!='aside' and not set(b.get('attributes',{}).get('class','').split()).intersection({'source-evidence-notice','source-check-notice'})}
 positions={n:i for i,n in enumerate(flat)}
 return [n for n in ids if n in positions and any((x<n)!=(positions[x]<positions[n]) for x in flat)]

def schema(rows):
 return dict(type='object',properties=dict(decisions=dict(type='array',items=dict(type='object',properties=dict(id=dict(type='string',enum=[r['id'] for r in rows]),accept=dict(type='boolean'),problems=dict(type='array',items=dict(type='string',enum=CODES))),required=['id','accept','problems'],additionalProperties=False))),required=['decisions'],additionalProperties=False)

def accept(rows,answer):
 need(isinstance(answer,dict) and set(answer)=={'decisions'},'ordering fields')
 need(isinstance(answer['decisions'],list),'ordering decisions')
 seen=set();result={}
 for r in answer['decisions']:
  need(isinstance(r,dict) and set(r)=={'id','accept','problems'},'ordering decision fields')
  need(r['id'] in {v['id'] for v in rows} and r['id'] not in seen,'unknown or repeated ordering ID');seen.add(r['id'])
  need(type(r['accept']) is bool and type(r['problems']) is list and all(p in CODES for p in r['problems']) and r['accept']==(not r['problems']),'ordering verdict')
  result[r['id']]=r['accept']
 need(seen=={r['id'] for r in rows},'missing ordering decision')
 return result

def candidates(plans):
    rows = []
    for plan in plans:
        source = json.loads(plan.prepared.contract_json)
        answer = json.loads(plan.answer_json)
        if not moved_opaque(source, answer):
            continue
        groups = []
        for index, group in enumerate(answer['groups']):
            ids = {f'a{n}' for lo, hi in group['ranges']
                   for n in range(int(lo[1:]), int(hi[1:])+1)}
            by_id = {a['id']:a for a in source['atoms']}
            owned = [by_id[f'a{n}'] for lo, hi in group['ranges']
                     for n in range(int(lo[1:]),int(hi[1:])+1)]
            rectangles = []
            for line in source.get('printed_lines', []):
                line_ids = {f'a{n}' for lo, hi in line['ranges']
                            for n in range(int(lo[1:]), int(hi[1:])+1)}
                if line_ids & ids:
                    rectangles.append(line['bbox'])
            for block in source['blocks']:
                if block['opaque'] and block['range'][0] in ids and block.get('bbox'):
                    rectangles.append(block['bbox'])
            words = ' '.join(a['text'] for a in owned).split()
            groups.append(dict(index=index, role=group['role'], ranges=group['ranges'],
                source_rectangles=rectangles, text_start=' '.join(words[:10]),
                text_end=' '.join(words[-8:]) if len(words)>10 else '',
                protected=any(a['protected'] for a in owned)))
        row = dict(page=plan.prepared.page, source_snapshot=source['snapshot'],
                   answer_sha256=digest(answer), groups=groups)
        row['id'] = 'o'+digest(row)[:20]
        rows.append(row)
    need(len({r['page'] for r in rows}) == len(rows), 'duplicate ordering page')
    return rows


def request(plans):
    rows = candidates(plans)
    need(bool(rows), 'empty ordering request')
    return envelope('ordering', digest(rows),
        [dict(id=r['id'], groups=r['groups']) for r in rows], PROMPT, schema(rows))


def approved(plans, response, source_binding):
    rows = candidates(plans)
    need(digest(rows) == source_binding, 'stale ordering batch')
    decisions = accept(rows, response)
    rejected = {r['page'] for r in rows if not decisions[r['id']]}
    return [p for p in plans if p.prepared.page not in rejected], rejected
