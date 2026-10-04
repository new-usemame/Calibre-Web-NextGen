"""Qualified readings follow immutable occurrences, even across layout groups."""
import copy,json,zipfile
from dataclasses import asdict
from xml.dom import minidom
import pytest
from cps.services.reflow import enriched_source,build_epub,layout_ops
from cps.services.reflow.structural_ops import ContractError
from tests.unit.test_reflow_source_readings import reading_source,evidence,selectors
from tests.unit.test_reflow_source_reading_segments import retained,selected
from tests.unit.test_reflow_layout_ops import answer

pytestmark=pytest.mark.unit

def display(f,chosen=None,empty=False):
    from cps.services.reflow import source_readings as sr,source_reading_display as sd
    b,d,s,r,t=f
    p,rec,rev,e=evidence(f,chosen)
    if empty:
        for row in rec['readings']:row['alternatives']=[]
        rev['snapshot']=sr.review_request(p,rec)['snapshot']
        e=sr.admit(b,d,s,r,p,rec,rev)
    s=sr.attach(b,s,e)
    return s,sd

def tree(fragment):return minidom.parseString('<root xmlns:epub="http://www.idpf.org/2007/ops">'+fragment+'</root>')
def annotations(fragment):return [n for n in tree(fragment).getElementsByTagName('span') if n.getAttribute('class')=='source-reading-annotation']

def test_duplicate_words_follow_last_exact_occurrence_and_current_regrouping(reading_source):
    b,d,base,r,t=reading_source;s,sd=display(reading_source,[selectors(r)[-1]])
    before=(asdict(r),copy.deepcopy(b.source_inventory),base.html,base.records_json)
    out,audit=sd.render(b,d,s,r,s.html)
    assert len(annotations(out))==1 and audit['entries'][0]['inline_placed']
    blocks=list(tree(out).documentElement.childNodes)
    # The last equal paragraph owns the annotation; no first-equal-string lookup.
    owners=[n for n in blocks if n.nodeType==n.ELEMENT_NODE and n.getElementsByTagName('span')]
    assert '25' in owners[-1].toxml() and not any('source-reading-annotation' in n.toxml() for n in owners[:-1])
    prepared=layout_ops.prepare(b,d,0,s,r);a=answer(prepared)
    # Move last paragraph before the identical first paragraphs.
    a['groups']=[a['groups'][-1],*a['groups'][:-1]]
    plan=prepared.accept(b,d,a,source_page=s,raw_page=r)
    compiled=plan.compile(b,d,source_page=s,raw_page=r)
    out,audit=sd.render(b,d,s,r,compiled.page_html,plan=plan)
    first=next(n for n in tree(out).documentElement.childNodes if n.nodeType==n.ELEMENT_NODE)
    assert 'source-reading-annotation' in first.toxml() and len(annotations(out))==1
    assert before==(asdict(r),b.source_inventory,base.html,base.records_json)


def test_split_span_pixels_and_text_stay_intact_and_link_is_sibling(retained):
    b,d,base,r,t=retained;s,sd=display(retained,[selected()])
    out,audit=sd.render(b,d,s,r,s.html)
    assert audit['entries'][0]['inline_placed'] and len(annotations(out))==1
    assert 'Original: 2°1' in out and 'Proposed reading: 25' in out and 'Synthetic' in out
    assert [n.toxml() for n in tree(out).getElementsByTagName('img')]==[n.toxml() for n in tree(base.html).getElementsByTagName('img')]
    assert not any(a.getElementsByTagName('a') for a in tree(out).getElementsByTagName('a'))

@pytest.mark.parametrize('damage',['ambiguous_owner','missing_line','codepoints'])
def test_unsupported_mapping_is_explicit_detail_only(reading_source,damage):
    b,d,base,r,t=reading_source
    if damage=='ambiguous_owner':b.pages[0].append(copy.deepcopy(b.pages[0][0]))
    elif damage=='missing_line':b.pages[0][0].line_boxes=[]
    else:b.pages[0][0].runs[0][1]+=' changed'
    base=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    f=(b,d,base,r,t);s,sd=display(f,[selectors(r)[0]])
    out,audit=sd.render(b,d,s,r,s.html)
    assert out==s.html and not annotations(out)
    assert not audit['entries'][0]['inline_placed'] and audit['entries'][0]['reason']


def test_empty_alternatives_do_not_create_inline_guess(reading_source):
    b,d,base,r,t=reading_source;s,sd=display(reading_source,[selectors(r)[0]],empty=True)
    out,audit=sd.render(b,d,s,r,s.html)
    assert out==s.html and audit['entries'][0]['reason']=='no_qualified_alternative'


def test_stale_plan_changed_raw_and_forged_report_cannot_place(reading_source):
    from dataclasses import replace
    b,d,base,r,t=reading_source
    prepared=layout_ops.prepare(b,d,0,base,r);plan=prepared.accept(b,d,answer(prepared),source_page=base,raw_page=r)
    s,sd=display(reading_source,[selectors(r)[0]])
    with pytest.raises(ContractError):sd.render(b,d,s,r,s.html,plan=plan)
    forged=replace(s,report_json=s.report_json.replace('25','99'))
    with pytest.raises(ContractError):sd.render(b,d,forged,r,s.html)
    changed=copy.deepcopy(r);changed.blocks[0].lines[0].spans[0].transcription_uncertain=True
    with pytest.raises(ContractError):sd.render(b,d,s,changed,s.html)


def test_missing_crop_and_negative_review_still_reject(reading_source):
    from cps.services.reflow import source_readings as sr
    from dataclasses import replace
    b,d,s,r,t=reading_source;p,rec,rev,e=evidence(reading_source,[selectors(r)[0]])
    with pytest.raises(ContractError):sr.admit(b,d,s,r,replace(p,crops=()),rec,rev)
    rev['accept']=False;rev['problems']=['pixels do not support alternative']
    with pytest.raises(ContractError):sr.admit(b,d,s,r,p,rec,rev)


def test_published_inline_controls_and_detail_return_resolve(reading_source):
    from cps.services.reflow import source_readings as sr
    b,d,base,r,t=reading_source
    _,_,_,admitted=evidence(reading_source,[selectors(r)[-1]])
    s=sr.attach(b,base,admitted)
    built=build_epub.build(b,str(t/'reading-display.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        docs={n:tree(z.read(n).decode().split('<body>',1)[1].split('</body>',1)[0]) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        marked=[(n,node) for n,doc in docs.items() for node in doc.getElementsByTagName('span') if node.getAttribute('class')=='source-reading-annotation']
        assert len(marked)==1
        n,node=marked[0];a=node.getElementsByTagName('a')[0];file,ident=a.getAttribute('href').split('#')
        detail=minidom.parseString(z.read('OEBPS/'+file));heading=[x for x in detail.getElementsByTagName('h2') if x.getAttribute('id')==ident];assert len(heading)==1
        assert any(x.getAttribute('href')==n.removeprefix('OEBPS/')+'#'+node.getAttribute('id') for x in detail.getElementsByTagName('a'))
        payload=json.loads(z.read('META-INF/reflow.json'));entry=payload['source_evidence'][0]['source_readings']['entries'][0]
        assert entry['inline_placed'] and payload['conservation']==b.conservation.to_dict()
        assert payload['source_enrichment']['0']['identity']==s.identity
        assert 'source-reading-annotation' in payload['generated_evidence_semantics']


def test_inline_glyph_size_scales_with_font_without_changing_factory_pixels():
    from cps.services.reflow import source_reading_display as sd
    assert sd.GLYPH_HEIGHT_EM>=1.35
    assert sd.glyph_stylesheet() in build_epub.STYLESHEET
    # Publication CSS must override the retained factory's intrinsic 1em style.
    assert 'height:%.2fem !important'%sd.GLYPH_HEIGHT_EM in sd.glyph_stylesheet()
    assert 'width:auto' in sd.glyph_stylesheet()


def test_alternative_number_has_no_note_binding_authority(reading_source):
    from cps.services.reflow import assemble
    b,d,base,r,t=reading_source
    b.notes.append(assemble.Note(25,'Independent source note.',0,bbox=(50,600,200,620)))
    base=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    s,sd=display((b,d,base,r,t),[selectors(r)[0]])
    before=copy.deepcopy(b.source_navigation)
    out,audit=sd.render(b,d,s,r,s.html)
    assert audit['entries'][0]['inline_placed']
    assert not any(a.getAttribute('href')=='#fn_25' for a in tree(out).getElementsByTagName('a'))
    assert b.source_navigation==before
    assert 'id="fn_25"' in out and 'Equal 15' in out


def test_native_child_publishes_current_occurrence_and_valid_returns(tmp_path):
    import pymupdf
    from cps.services.reflow import source_readings as sr,enriched_source
    from cps.services.reflow.native_ipc import NativeDocument
    path=tmp_path/'native-display.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=400)
        p.insert_text((40,70),'Equal 15 original.',fontsize=12)
        p.insert_text((40,110),'Equal 15 original.',fontsize=12)
        pdf.save(path)
    with NativeDocument(path,scratch_root=tmp_path/'native') as doc:
        result=doc.prepare_result(recovery_opts={'mode':'off'},require_figure_caption=False)
        b=result.book;r=result.raw_pages[0]
        base=enriched_source.prepare_source_page(b,0,{'layer':'native'})
        chosen=selectors(r)[-1:]
        p,rec,rev,e=evidence((b,doc,base,r,tmp_path),chosen)
        s=sr.attach(b,base,e)
        prep=layout_ops.prepare(b,doc,0,s,r);a=answer(prep);a['groups']=[a['groups'][-1],*a['groups'][:-1]]
        plan=prep.accept(b,doc,a,source_page=s,raw_page=r)
        built=build_epub.build(b,str(tmp_path/'native-display.epub'),doc=doc,source_pages={0:s},raw_pages={0:r},layout_plans=[plan])
        assert build_epub.validate(built.path)==[]
        with zipfile.ZipFile(built.path) as z:
            payload=json.loads(z.read('META-INF/reflow.json'))
            assert payload['source_evidence'][0]['source_readings']['entries'][0]['inline_placed']
            chapters=[z.read(n).decode() for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')]
            assert sum(x.count('class="source-reading-annotation"') for x in chapters)==1
            assert payload['source_enrichment']['0']['identity']==s.identity


def test_existing_owned_soft_hyphen_seam_replays_without_normalizing_raw(tmp_path):
    import pymupdf
    from cps.services.reflow import assemble,extract,skeleton
    path=tmp_path/'seam.pdf'
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=400,height=400)
        p.insert_text((40,70),'Oikodespotes already. Oiko-',fontsize=12)
        p.insert_text((40,85),'despotes ends 15',fontsize=12)
        pdf.save(path)
    with pymupdf.open(path) as doc:
        raw=extract.read_page(doc,0,keep_char_boxes=True)
        # Retained source has a soft hyphen, while the current canonical assembly
        # already consumed that earned physical-line seam. This is no new repair.
        from dataclasses import replace
        ln=raw.blocks[0].lines[0];ln.spans[0]=replace(ln.spans[0],text=ln.spans[0].text.replace('Oiko-','Oiko\xad'))
        lines=[l for b in raw.blocks for l in b.lines]
        skel=skeleton.PageSkeleton(0,raw.width,raw.height,[skeleton.Region('body',lines,bbox=raw.blocks[0].bbox)])
        book=assemble.assemble([skel],skeleton.BookStyle(12),[raw]);base=enriched_source.prepare_source_page(book,0,{'layer':'native'})
        original=copy.deepcopy(asdict(raw));s,sd=display((book,doc,base,raw,tmp_path),selectors(raw))
        out,audit=sd.render(book,doc,s,raw,s.html)
        assert audit['entries'][0]['inline_placed'] and original==asdict(raw)
        assert base.html==s.html and '\xad' not in base.html


def test_existing_builder_puts_split_span_alternative_after_original(retained):
    from cps.services.reflow import source_readings as sr
    b,d,base,r,t=retained
    _,_,_,admitted=evidence(retained,[selected()]);s=sr.attach(b,base,admitted)
    built=build_epub.build(b,str(t/'split-main.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    with zipfile.ZipFile(built.path) as z:
        html=''.join(z.read(n).decode() for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml'))
        assert len(annotations(html.split('<body>',1)[1].split('</body>',1)[0]))==1
        assert 'Original: 2°1; Proposed reading: 25' in html
        assert [n.getAttribute('src') for n in tree(base.html).getElementsByTagName('img')]==[n.getAttribute('src') for n in tree(html.split('<body>',1)[1].split('</body>',1)[0]).getElementsByTagName('img')]


def test_generated_reading_copy_does_not_decide_or_break_source_page_join():
    # The disclosure ends in ']' and would falsely suppress a prose continuation
    # if publication treated generated words as source sentence structure.
    mark='<span class="source-reading-annotation" id="reading-return-reading-'+('a'*64)+'"> [Original: damaged; Proposed reading: proposed. <a href="original-p0000.xhtml#reading-'+('a'*64)+'">Inspect source</a>]</span>'
    left='<p>the damaged'+mark+' text continues</p>';right='<p>with the source sentence.</p>'
    pages=[dict(pno=0,anchor=build_epub.page_anchor(0),body=[left],asides=[]),dict(pno=1,anchor=build_epub.page_anchor(1),body=[right],asides=[])]
    assert build_epub._join_page_turns(pages)==1
    assert build_epub.block_text(pages[0]['body'][0])=='the damaged text continues with the source sentence.'
    assert mark in pages[0]['body'][0]
    # The marker is restored at its exact pre-join location after stripping copy
    # for the unchanged source-only join mechanics.
    head='<p>with'+mark+' the source sentence.</p>'
    left='<p>the source con-'+mark+'</p>'
    merged=build_epub._merge_layout_paragraphs(left,head,'','drop')
    assert build_epub.block_text(merged)=='the source conwith the source sentence.'
    assert merged.count(mark)==2


def test_equal_codepoints_cannot_hide_changed_source_links_or_glyph_attributes(retained):
    b,d,base,r,t=retained;s,sd=display(retained,[selected()])
    for changed in (s.html.replace('height:1em','height:0.1em'),s.html.replace('original-p0000.xhtml#page','original-p0000.xhtml#wrong')):
        assert changed!=s.html
        out,audit=sd.render(b,d,s,r,changed)
        assert out==changed and not audit['entries'][0]['inline_placed']
        assert audit['entries'][0]['reason']=='current_output_source_tree_differs'


def test_generated_note_inspection_copy_does_not_block_proved_body_occurrence(reading_source):
    from cps.services.reflow import assemble,source_readings as sr
    b,d,base,r,t=reading_source
    b.notes.append(assemble.Note(15,'Independent source note.',0,uncertain=True,bbox=(50,600,200,620)))
    base=enriched_source.prepare_source_page(b,0,{'layer':'native'})
    _,_,_,admitted=evidence((b,d,base,r,t),[selectors(r)[-1]])
    s=sr.attach(b,base,admitted)
    built=build_epub.build(b,str(t/'ambiguous-note-inspection.epub'),doc=d,source_pages={0:s},raw_pages={0:r})
    assert build_epub.validate(built.path)==[]
    with zipfile.ZipFile(built.path) as z:
        payload=json.loads(z.read('META-INF/reflow.json'))
        assert payload['source_evidence'][0]['source_readings']['entries'][0]['inline_placed']
        assert payload['source_evidence'][0]['ambiguous_notes']==['15']
        assert payload['conservation']==b.conservation.to_dict()
