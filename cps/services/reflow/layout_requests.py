"""Measured source-ID layout prompts; words and emitted markup never come from replies."""
import json
from dataclasses import dataclass, replace

from . import _layout_atoms as atoms, layout_ops
from .structural_ops import ContractError
from ._layout_policy import FLOATING_ARTWORK_INSTRUCTION, FLOATING_ARTWORK_VERSION
PROTOCOL='source-bound-layout-1'
PROMPT_VERSION='source-bound-layout-prompt-12-ranges-'+FLOATING_ARTWORK_VERSION

PROMPT='''Arrange a PDF page using immutable canonical source atoms and the page image. Use the supplied constructive response contract. Every current atom must occur exactly once. Never generate source words or HTML. Atom allocations can be reordered or regrouped. Roles: paragraph, heading1, heading2, heading3, quote, lineblock, furniture, source. Respect printed paragraph boundaries and column reading order. Use lineblock for dedication/verse/display lines; each range is one printed line. Furniture is only a recurring running header/footer or folio, never a chapter number or body heading. Each opaque block MUST remain its one original atom with role source, source_furniture only for an actual recurring running header/footer or folio, or note only for the eligible whole-list placement described below; preserve it even if the raster appears to contain additional readable words. Those blocks preserve image regions, uncertain source glyphs, notes, links, list structure and evidence notices that must not be replaced by raw OCR. source_furniture retains the entire original block in the separate retained-furniture channel; never use it for figures, tables, captions, notes, chapter headings or uncertain body glyphs. Nonopaque atoms may be regrouped; protected inline atoms are indivisible. No new crops, transcription or spelling correction is permitted. joins may connect adjacent nonprotected body atoms ONLY when left is in wrap_lefts and right is in wrap_rights, both trusted printed-line endpoints: {left:atomID,right:atomID,hyphen:keep|drop}. Keep compound hyphens; drop only soft wrapping hyphens. Never join protected source atoms. Otherwise joins is empty. Groups are in final reading order. Use only supplied IDs. Return JSON only.'''

def response_schema(source):
 def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
 def enum(values):return dict(type='string',enum=values or ['NO_VALID_ID'])
 ids=[a['id'] for a in source['atoms']]
 opaque_ids=[b['range'][0] for b in source.get('protected_blocks',[])]
 body_ids=[identifier for identifier in ids if identifier not in opaque_ids]
 pair=lambda choices:dict(type='array',items=enum(choices),minItems=2,maxItems=2)
 eligible=[]
 for first,last in source.get('emphasis_ranges',[]):
  eligible.extend(ids[ids.index(first):ids.index(last)+1])
 emphasis_selector={'$ref':'#/$defs/emphasis_atom'} if eligible else enum([])
 other_body=[identifier for identifier in body_ids if identifier not in set(eligible)]
 body_selector=dict(anyOf=[emphasis_selector,enum(other_body)]) if eligible and other_body else emphasis_selector if eligible else enum(body_ids)
 body_pair=dict(type='array',items=body_selector,minItems=2,maxItems=2)
 groups=[obj(dict(role=enum(sorted(atoms.ROLES-{'source','source_furniture'})),
     ranges=dict(type='array',minItems=1,items=body_pair)))] if body_ids or not opaque_ids else []
 if opaque_ids:
  furniture_ids=[b['range'][0] for b in source['protected_blocks'] if atoms.can_be_source_furniture(b)]
  note_ids=[b['range'][0] for b in source['protected_blocks'] if atoms.can_be_source_note(b)]
  choices=[(['source','source_furniture'],opaque_ids)] if furniture_ids==opaque_ids else [(['source'],opaque_ids),(['source_furniture'],furniture_ids)]
  if note_ids: choices.append((['note'],note_ids))
  for roles,allowed in choices:
   if allowed:
    groups.append(obj(dict(role=enum(roles),ranges=dict(type='array',minItems=1,maxItems=1,
        items=dict(anyOf=[pair([identifier]) for identifier in allowed])))))
 group_schema=dict(anyOf=groups) if len(groups)>1 else groups[0]
 join=obj(dict(left=enum(source.get('wrap_lefts',[])),right=enum(source.get('wrap_rights',[])),hyphen=enum(['keep','drop'])))
 properties=dict(snapshot=enum([source['snapshot']]),groups=dict(type='array',items=group_schema),joins=dict(type='array',items=join))
 # A sentinel is a schema placeholder, never a source ID. With either side
 # absent there is no local operation to propose; page-boundary joins retain
 # their independent eligibility below and still require compiler/review gates.
 if not source.get('wrap_lefts') or not source.get('wrap_rights'):
  properties['joins']['maxItems']=0
 if not ids:properties['groups']['maxItems']=0
 properties['continuation']=dict(type='boolean') if source.get('previous') else dict(type='boolean',enum=[False])
 previous_left=source.get('previous',{}).get('wrap_lefts',[])
 boundary=obj(dict(left=enum(previous_left),right=enum(source.get('wrap_rights',[])),hyphen=enum(['keep','drop'])))
 properties['boundary_join']=dict(anyOf=[boundary,dict(type='null')]) if previous_left and source.get('wrap_rights') else dict(type='null')
 if source.get('note_candidates'):
  candidates=source['note_candidates']
  properties['note_bindings']=dict(type='array',items=obj(dict(
      reference=enum([r['id'] for r in candidates['references']]),
      note=enum([r['id'] for r in candidates['labels']]))))
 properties['emphasis']=dict(type='array',items=dict(type='array',items=emphasis_selector,minItems=2,maxItems=2))
 result=obj(properties)
 if eligible:result['$defs']={'emphasis_atom':enum(eligible)}
 return result

NOTE_PROMPT = "\nThis page offers checked note_bindings: an array of {reference: current atom ID, note: current atom ID}, or [] when uncertain. Use only note_candidates. The note ID must begin a complete note-role group; its printed numeric label must exactly match the reference. The reference must remain in main paragraph/quote prose. Choose an association only when supported by the printed page, never merely because numbers match. Each reference and note target may be used once. This generates bidirectional links using the existing label and unchanged SUP; it never changes source text. Existing opaque/resolved/uncertain markers are ineligible."

SPAN_NOTE_PROMPT = '\nSome note_candidates references are plain numeric spans: their id is the only allowed reference selector; atom identifies the unchanged containing atom, and start/end are Unicode code-point offsets in XML-decoded source text. Select the supplied span ID, never invent offsets, text or markup. Only its existing digits acquire an anchor; prefix, suffix, punctuation and spacing stay exact. References must remain in main paragraph/quote prose and their containing atoms cannot participate in joins. SUP candidates retain their existing whole-atom behavior. Numeric agreement is eligibility only, never proof of a note association.'

PROMPT += "\nAlso return continuation (boolean) and boundary_join (null or {left:previous-atom-ID,right:current-atom-ID,hyphen:keep|drop}). continuation is true only if the first main paragraph or quote continues the previous PDF page's last main group of the SAME role. A continued quote must be quote on both pages; never extend quote scope into following ordinary prose. Previous atoms are context, never members of current groups. boundary_join may use only previous.wrap_lefts and current wrap_rights; it chooses a printed split word at that real page boundary. Ordinary same-page/scan-spread joins belong in joins, never boundary_join. The final compiler checks BOTH admitted pages' actual last/first main-body endpoints. Notes and evidence notices are separate from that reading flow; headings, figures and tables are barriers. Without previous source context continuation must be false and boundary_join null. Never silently drop a boundary hyphen; make the explicit source-supported choice."

PROMPT += "\nCanonical blocks are storage containers, NOT authoritative paragraph boundaries. Use the image and printed_lines geometry to infer indentation, paragraph breaks and columns even inside one canonical block. Printed_lines ranges bind literal source lines; absent mappings are unknown, not evidence of no break. A joins pair MUST be consecutive atoms inside ONE output group. If intervening furniture or a protected block interrupts a printed sentence, use multiple disjoint ranges in that one paragraph group and preserve the intervening block in its own group. Do not place the two joined halves in separate groups. Before returning, check visible paragraph starts against the image, exact-once atom coverage, and each join's group membership."

PROMPT += "\nAn indented first line following preceding prose normally starts a NEW paragraph, even without a blank line. Compare the x-position of the first word with subsequent full-width lines. Do not merge distinct indented paragraphs merely because their subject matter continues. Canonical block segmentation is deliberately omitted from the model view so it cannot substitute for printed layout. Protected_blocks alone have fixed grouping; all other paragraph boundaries must come from source evidence."

PROMPT += "\nThe role note is available for an actual printed footnote/citation that is ordinary selectable text. Group its complete existing label and text separately from main prose. This does not create or guess any reference link; existing opaque linked notes remain source blocks. An intact protected ol/ul with only class source-list may instead use role note when the entire printed list is footnotes/citations. Keep its single original atom and all labels/items intact; this wraps the list in an unbound note channel without creating links. Never use this for a body list, figure, table, heading or mixed note/body material. Notes never become running furniture and never interrupt a continued main paragraph. Do not label body text as a note merely because it mentions a source."

# Qualified against real inline-glyph failures in the reader oracle. These are
# source slots, not empty paragraphs; opaque block ownership remains unchanged.
PROMPT += "\nProtected atoms with empty text can represent inline images or glyphs at their original position within a paragraph. An empty text value does not mean the atom is blank or disposable. Preserve each such atom exactly once in its original inline position within the same natural paragraph as the surrounding words. Do not pull an inline slot out into a separate empty paragraph or move it after the paragraph. A continuous paragraph range can include protected inline atoms; only joins across protected atoms are forbidden. This does not authorize any word, markup, source-role or geometry edits. Use the source image and printed paragraph boundaries, and preserve all ordinary opaque source blocks under their required source role."
PROMPT += '\n' + FLOATING_ARTWORK_INSTRUCTION
PROMPT += '\nReturn emphasis as [] or inclusive [firstAtomID,lastAtomID] ranges over atoms within supplied emphasis_ranges only. Those eligibility ranges compactly list all eligible source IDs; select any supported subrange, not necessarily a whole eligibility range. Each range generates strong emphasis over unchanged source text, wholly within one paragraph/quote group, in source order. Use the printed image to identify genuinely emphasized inline labels or words; do not promote them into headings. No overlapping/nested ranges, existing markup, notes, protected material, generated note-reference carriers or joined atoms. Trusted wrap_lefts cannot be emphasized. Never supply HTML, text, fonts or attributes. Role selection is independent of inline emphasis.'

def envelope(stage,source_identity,view,prompt,schema):
    return dict(protocol=PROTOCOL,source_identity=source_identity,
        prompt_version=PROMPT_VERSION+'-'+stage,
        messages=[dict(role='system',content=prompt),dict(role='user',content=json.dumps(view,ensure_ascii=False,separators=(',',':')))],
        response_schema=schema,context=dict(stage=stage))

PREVIOUS_CANDIDATE_PROMPT = '\nprevious_candidate describes the preceding source-checked layout proposal, not final approval. Its last_main_role and last_main_paragraph (also used for quote text) describe the proposed reading-flow endpoint after any source-supported rearrangement. Use it with the previous source context when deciding continuation; a null endpoint is a main-flow barrier. Never copy its atoms into current groups. Independent review and final admission of both pages are still required before any boundary is applied.'

def _legacy_proposal(prepared, previous_plan=None):
    from . import layout_construction as construction
    view=prepared.model_view()
    identity=dict(snapshot=prepared.snapshot_id)
    prompt=PROMPT+(NOTE_PROMPT if view.get('note_candidates') else '')
    if any('atom' in r for r in view.get('note_candidates', {}).get('references', [])):
        prompt += SPAN_NOTE_PROMPT
    prompt += '\n'+construction.PROMPT
    if previous_plan is not None:
        source=json.loads(prepared.contract_json)
        _need_review(source['snapshot']==prepared.snapshot_id, 'prepared snapshot differs')
        _, prior, _ = _previous_material(prepared, source, previous_plan)
        view['previous_candidate']=prior
        identity['previous_candidate']=prior['snapshot']
        prompt += PREVIOUS_CANDIDATE_PROMPT
    source = json.loads(prepared.contract_json)
    view['atoms'] = [dict(atom, **cue) for atom,cue in zip(view['atoms'],construction.cues(source))]
    view['construction'] = dict(contract=construction.VERSION, scope='current',
        ownership_order='atoms in exact canonical order; previous context is never allocated')
    if view.get('previous'):
        view['previous']['scope'] = 'previous_context_only'
        for atom in view['previous']['atoms']:
            atom['membership'] = dict(state='unknown',lines=[])
    identity['construction_version'] = construction.VERSION
    return envelope('proposer',identity,view,prompt,
                    construction.schema(view,response_schema(view)))


# A structural review can approve local grouping while declining a page boundary.
# Its output never supplies source text or edits the candidate's local operations.
REVIEW_CODES = ('paragraph_split', 'paragraph_merge', 'heading_scope', 'quote_scope',
                'furniture', 'reading_order', 'note_or_caption_association', 'unsupported_change')
REVIEW_PROMPT = '''Review proposed PDF-to-EPUB structure changes against the printed page image and literal source-line geometry. Source words and protected resources have already passed mechanical preservation checks. Reject NEW semantic/layout errors introduced by the proposed changes, not unchanged baseline limitations. Canonical reference blocks are deterministic output, not ground truth. Approve source-supported corrections such as removing a running header from prose or splitting at a printed indentation. Inspect every changed group. Reject arbitrary sentence-by-sentence splits, merging distinct printed paragraphs, false furniture, wrong reading order, detached captions/notes, and inline bold labels promoted without printed heading support. Preserve quoted passage scope and table/figure associations. Hyphen spelling is handled separately and must not determine this structural verdict.
Return exactly snapshot, accept, continuation_accept, problems. Use the supplied review snapshot. accept and problems judge ONLY this page's local grouping, roles, order and associations. accept is true exactly when problems is empty. problems contains only supplied categorical codes, never source words, corrected prose, HTML or operations.
Judge continuation independently: continuation_accept may be true only when a continuation is proposed and the supplied previous candidate's last main paragraph supports joining this candidate's actual first main paragraph or quote with the SAME role. Use last_main_role to preserve quote scope; reject quote-to-paragraph or paragraph-to-quote boundaries. An unavailable previous candidate or an unsupported boundary must NOT reject otherwise correct local layout. Previous candidate context is not proof of its final approval. The compiler separately requires both adjacent pages to be accepted and their exact endpoints to agree before applying any boundary. Without an available previous main paragraph or quote, continuation_accept must be false.'''
REVIEW_PROMPT += '\nA protected source-list selected as note must consist entirely of printed footnotes/citations. Reject body lists or mixed note/body lists moved out of main flow with note_or_caption_association. The original list remains intact and no new note binding is inferred.'
REVIEW_PROMPT += '\nInspect every proposed emphasis range against the original page raster and literal source atoms. Preserved words alone do not justify emphasis. Reject unsupported inline strong styling with unsupported_change; keep paragraph/heading roles independent.'
REVIEW_PROMPT += '\nReflow presentation policy to check independently: ' + FLOATING_ARTWORK_INSTRUCTION


@dataclass(frozen=True)
class LayoutReview:
    """A bound categorical decision; compilation still revalidates live source."""
    snapshot: str
    accept: bool
    continuation_accept: bool
    problems: tuple
    accepted_plan: object


def _need_review(condition, reason):
    if not condition:
        raise ContractError(reason)


def _candidate(prepared, plan):
    _need_review(type(prepared) is layout_ops.PreparedLayout and type(plan) is layout_ops.LayoutPlan,
                 'prepared layout and candidate plan required')
    _need_review(plan.prepared == prepared, 'candidate belongs to different prepared source')
    try:
        source = json.loads(prepared.contract_json)
        _need_review(source['snapshot'] == prepared.snapshot_id, 'prepared snapshot differs')
        answer = layout_ops._answer(source, json.loads(plan.answer_json))
        groups = atoms.validate(source, answer)
        if getattr(plan, 'construction_json', ''):
            from . import layout_construction
            layout_construction.validate_plan(source, answer, plan.construction_json)
        return source, answer, groups
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise ContractError('invalid review candidate') from exc


def _main_edge(groups, last):
    from . import _layout_flow
    value = _layout_flow.edge(groups, last)
    return value[1] if value else None


def _previous_material(prepared, source, previous_plan):
    """One evidence-bound, provisional neighbor view for proposal and review."""
    previous = source.get('previous')
    prior_identity = None
    prior_view = None
    prior_edge = None
    if previous_plan is not None:
        _need_review(previous is not None and type(previous_plan) is layout_ops.LayoutPlan,
                     'previous candidate lacks source context')
        left = previous_plan.prepared
        left_source, left_answer, left_groups = _candidate(left, previous_plan)
        _need_review(left.page + 1 == prepared.page and left.pdf_digest == prepared.pdf_digest,
                     'previous candidate must be adjacent in the same PDF')
        _need_review(left.source_identity == previous['identity'] and left.page == previous['page'],
                     'previous candidate source identity differs')
        binding = dict(left_source['binding'])
        binding.pop('raster_panels', None)
        binding.pop('raster_sha256', None)  # Previous source context has no raster.
        _need_review(binding == previous['binding'], 'previous candidate evidence differs')
        left_atoms = [{k: v for k, v in atom.items() if k in ('id', 'text', 'protected', 'raster_region')}
                      for atom in left_source['atoms']]
        _need_review(left_atoms == previous['atoms'] and left_source['wrap_lefts'] == previous['wrap_lefts'],
                     'previous candidate source atoms differ')
        prior_identity = dict(snapshot=left.snapshot_id, candidate=left_answer)
        if getattr(previous_plan,'construction_json',''):
            prior_identity['construction'] = json.loads(previous_plan.construction_json)
        prior_edge = _main_edge(left_groups, True)
        from . import _layout_flow
        prior_role = (_layout_flow.edge(left_groups, True) or (None,))[0]
        prior_view = dict(status='candidate available; final structural approval still required',
                          snapshot=atoms.digest(prior_identity),
                          last_main_role=prior_role,
                          last_main_paragraph=[{k:v for k,v in a.items() if k in ('id','text','raster_region')} for a in prior_edge[-120:]] if prior_edge else None)
    elif previous:
        prior_view = dict(status='previous candidate unavailable; no boundary approval permitted')
    return prior_identity, prior_view, prior_edge


def _review_material(prepared, plan, previous_plan):
    source, answer, groups = _candidate(prepared, plan)
    prior_identity, prior_view, prior_edge = _previous_material(prepared, source, previous_plan)
    identity = dict(snapshot=prepared.snapshot_id, candidate=answer, previous=prior_identity)
    construction_json = getattr(plan, 'construction_json', '')
    if construction_json: identity['construction'] = json.loads(construction_json)
    binding = atoms.digest(identity)
    proposed = [dict(index=i, role=role, ranges=answer['groups'][i]['ranges'],
                     text=' '.join(a['text'] for a in group)) for i, (role, group) in enumerate(groups)]
    owned = {a['id']: a for a in source['atoms']}
    reference = []
    for block in source['blocks']:
        first, last = block['range']
        ids = list(owned)
        text = ' '.join(owned[k]['text'] for k in ids[ids.index(first):ids.index(last)+1])
        reference.append(dict(kind=block['kind'], range=block['range'], protected=block['opaque'],
                              bbox=block.get('bbox'), text=text))
    view = dict(snapshot=binding, continuation=answer.get('continuation', False),
                previous=prior_view, reference_layout=reference, proposed_layout=proposed,
                printed_lines=source.get('printed_lines', []), problem_codes=list(REVIEW_CODES))
    view['protected_constraints'] = atoms.protected_constraints(source)
    if source['binding'].get('raster_panels'):
        view['raster_panels'] = source['binding']['raster_panels']
    pixels = [{k:v for k,v in a.items() if k in ('id','raster_region')} for a in source['atoms'] if a.get('raster_region')]
    if pixels: view['raster_atoms'] = pixels
    if answer.get('emphasis'):
        view['emphasis'] = answer['emphasis']
        from . import _layout_emphasis
        selected={identifier for item in _layout_emphasis.checked(source,answer,groups) for identifier in item['ids']}
        view['emphasis_source_atoms'] = [dict(id=a['id'], text=a['text']) for a in source['atoms'] if a['id'] in selected]
    if answer.get('note_bindings'):
        view['note_bindings'] = answer['note_bindings']
        view['note_candidates'] = atoms.model_view(source)['note_candidates']
    if construction_json:
        from . import layout_construction as construction
        raw = json.loads(construction_json)
        if raw.get('contract') == 'layout-domain-2':
            from . import layout_domain
            raw.pop('emphasis',None)
            raw = layout_domain.expand(source,raw)
        view['atoms'] = construction.cues(source)
        # Literal source appears exactly once in current atoms. Both baseline
        # and proposed groups address its complete unchanged canonical ranges.
        for row in view['reference_layout']+view['proposed_layout']: row.pop('text',None)
        if raw.get('contract') in ('layout-semantic-choices-1','layout-range-choices-1'):
            from . import layout_ranges as layout_choices
            if raw['contract'] == 'layout-semantic-choices-1':
                from . import layout_choices
            raw.pop('emphasis',None)
            view['decisions'] = layout_choices.decisions(source,answer,raw,prior_view)
            ce = layout_choices.expand(source,raw)[0]['continuation_evidence']
            if ce and prior_edge:
                _need_review(ce['previous'][0] == prior_edge[-1]['id'],
                             'incoming evidence differs from previous candidate endpoint')
        else:
            view['decisions'] = construction.decisions(source, answer, raw)
        view['construction_version'] = construction.VERSION
        ce = raw.get('continuation_evidence')
        if ce and prior_edge:
            _need_review(ce['previous'][0] == prior_edge[-1]['id'],
                         'continuation evidence differs from previous candidate endpoint')
    from . import _layout_flow
    current_edge = _layout_flow.edge(groups, False)
    return binding, view, answer, bool(prior_edge and current_edge and
        prior_view['last_main_role'] == current_edge[0])


def _legacy_review(prepared, plan, previous_plan=None):
    """Construct a strict review envelope bound to exact current/prior candidates."""
    binding, view, candidate, _ = _review_material(prepared, plan, previous_plan)
    properties = dict(snapshot=dict(type='string', enum=[binding]), accept=dict(type='boolean'),
                      continuation_accept=dict(type='boolean'),
                      problems=dict(type='array',
                                    items=dict(type='string', enum=list(REVIEW_CODES))))
    if view.get('construction_version'):
        properties['decisions'] = dict(type='object',
            properties={d['id']: dict(type='boolean') for d in view['decisions']},
            required=[d['id'] for d in view['decisions']], additionalProperties=False)
    schema = dict(type='object', properties=properties, required=list(properties), additionalProperties=False)
    from . import layout_construction as construction
    prompt = REVIEW_PROMPT
    if view.get('construction_version'):
        prompt = prompt.replace('Return exactly snapshot, accept, continuation_accept, problems.',
                                'Return exactly snapshot, accept, continuation_accept, problems and decisions.')
    return envelope('reviewer', dict(snapshot=prepared.snapshot_id, review_snapshot=binding),
                    view, prompt + (('\n'+construction.REVIEW_PROMPT) if view.get('construction_version') else '') + ('\nInspect every proposed note_bindings association against the printed reference and complete note. Reject unsupported or wrong attachments using note_or_caption_association or unsupported_change.' if candidate.get('note_bindings') else ''), schema)


def _legacy_validate_review(prepared, plan, response, previous_plan=None):
    """Refuse missing/stale/forged verdicts; decline boundaries independently.

    A negative local verdict returns no selected plan. A positive local verdict
    with a negative boundary verdict retains every local source operation and
    clears only continuation/boundary_join. It does not admit the previous page.
    """
    binding, view, candidate, can_continue = _review_material(prepared, plan, previous_plan)
    fields = {'snapshot', 'accept', 'continuation_accept', 'problems'}
    if view.get('construction_version'): fields.add('decisions')
    _need_review(type(response) is dict and set(response) == fields, 'missing or malformed structural review')
    _need_review(response['snapshot'] == binding, 'stale structural review')
    accepted, continued, problems = response['accept'], response['continuation_accept'], response['problems']
    _need_review(type(accepted) is bool and type(continued) is bool and type(problems) is list,
                 'malformed structural review verdict')
    _need_review(all(type(code) is str and code in REVIEW_CODES for code in problems),
                 'unknown structural review problem')
    _need_review(len(set(problems)) == len(problems) and accepted == (not problems),
                 'inconsistent structural review verdict')
    _need_review(not continued or (candidate.get('continuation', False) and can_continue),
                 'continuation approval lacks candidate context')
    if view.get('construction_version'):
        decisions = response['decisions']
        _need_review(type(decisions) is dict and set(decisions) == {d['id'] for d in view['decisions']}
            and all(type(v) is bool for v in decisions.values()), 'missing or foreign individual review')
        _need_review(not accepted or all(decisions[d['id']] for d in view['decisions']
            if d['kind'] != 'continuation'), 'false individual local approval')
        _need_review(not continued or all(decisions[d['id']] for d in view['decisions']
            if d['kind'] == 'continuation'), 'false individual continuation approval')
    selected = None
    if accepted:
        if not continued:
            candidate.update(continuation=False, boundary_join=None)
        selected = replace(plan, answer_json=layout_ops._json(candidate))
    return LayoutReview(binding, accepted, continued, tuple(problems), selected)

RASTER_PROMPT = "\nAn atom with raster_region is an indivisible existing uncertain-text crop, not OCR words. Its bbox locates it on the source page. Select paragraph/quote only if the COMPLETE crop belongs to that printed paragraph; it may share disjoint ranges with ordinary text across retained notices/furniture. Keep distinct paragraphs and quote scope separate. Never join/drop a hyphen at a pixel endpoint, transcribe, split or crop it. A mixed/multiparagraph region must stay a separate group; do not claim its internal continuity is repaired. A crop containing only a recurring running head/folio may use furniture, retaining its exact pixels. Retain every evidence notice as source outside the reading paragraph, after its completed group where appropriate. Known tables, figures, captions and whole-page fallbacks remain opaque barriers. If binding.raster_panels (or raster_panels in review) is present, the supplied image contains the previous page on the LEFT and current page on the RIGHT; panel rectangles are image pixels, raster_region bboxes are page reading coordinates. Judge both ends against those actual pages. Empty text is no evidence of paragraph continuity."
PROMPT += RASTER_PROMPT
REVIEW_PROMPT += RASTER_PROMPT

CONSTRAINT_PROMPT = "\nprotected_constraints are source-derived MECHANICAL eligibility, not semantic approval. Keep each atom intact and use only its allowed_roles. flow_by_role says whether that selected role remains a main-flow barrier or a separate retained channel. Evidence notices and existing asides may move intact outside a completed reading paragraph; they are disclosures/retained source, not a reason to split its printed prose into one group per crop. A source-list may leave main flow only after you establish that the WHOLE list is printed notes; binding_authority none forbids deriving reference identity from its labels. Protected inline links and uncertain markers retain their exact source associations; only supplied note_candidates authorize new bindings. No label agreement creates an unsupported or nonexistent note. Image/raster atoms are whole crops; mixed or whole-page internal continuity remains unsupported. Structural roles/ranges and optional emphasis are admitted and independently reviewed separately; an emphasis decision cannot justify a paragraph break or a heading."
PROMPT += CONSTRAINT_PROMPT
REVIEW_PROMPT += CONSTRAINT_PROMPT


def proposal(prepared,previous_plan=None, *, hints=None, profile_selection=None):
    from . import layout_hints
    request = layout_hints.proposal(prepared,previous_plan,hints)
    if profile_selection:
        from . import layout_model, layout_eligibility, layout_glm, layout_luna_high
        if layout_luna_high.selected(profile_selection):
            request = layout_luna_high.proposal(prepared, request)
        elif layout_glm.selected(profile_selection):
            request = layout_glm.proposal(prepared, request)
        elif dict(profile_selection) == dict(layout_model.LUNA_REVIEW_SELECTION):
            request = layout_eligibility.proposal(prepared, request)
    return request


def review(prepared,plan,previous_plan=None, *, profile_selection=None):
    original = _review_request(prepared,plan,previous_plan)
    from . import layout_glm, layout_luna_high
    if layout_luna_high.selected(profile_selection):
        return layout_luna_high.review(prepared,plan,previous_plan,original)
    return layout_glm.review(prepared,plan,previous_plan,original) if layout_glm.selected(profile_selection) else original


def _review_request(prepared,plan,previous_plan=None):
    if getattr(plan,'construction_json','') and json.loads(plan.construction_json).get('contract')=='layout-range-choices-1':
        from . import layout_ranges
        return layout_ranges.review(prepared,plan,previous_plan)
    if getattr(plan,'construction_json','') and json.loads(plan.construction_json).get('contract')=='layout-semantic-choices-1':
        from . import layout_choices
        return layout_choices.review(prepared,plan,previous_plan)
    if getattr(plan,'construction_json','') and json.loads(plan.construction_json).get('contract')=='layout-domain-2':
        from . import layout_domain
        return layout_domain.review(prepared,plan,previous_plan)
    return _legacy_review(prepared,plan,previous_plan)


def validate_review(prepared,plan,response,previous_plan=None):
    if getattr(plan,'construction_json','') and json.loads(plan.construction_json).get('contract')=='layout-range-choices-1':
        from . import layout_ranges
        return layout_ranges.validate_review(prepared,plan,response,previous_plan)
    if getattr(plan,'construction_json','') and json.loads(plan.construction_json).get('contract')=='layout-semantic-choices-1':
        from . import layout_choices
        return layout_choices.validate_review(prepared,plan,response,previous_plan)
    if getattr(plan,'construction_json','') and json.loads(plan.construction_json).get('contract')=='layout-domain-2':
        from . import layout_domain
        return layout_domain.validate_review(prepared,plan,response,previous_plan)
    return _legacy_validate_review(prepared,plan,response,previous_plan)
