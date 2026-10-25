"""Synthetic reader disagreements retain assistance and adjacent original ink."""
import pytest
from xml.etree import ElementTree as ET
from cps.services.reflow import reading_tiers as tiers

pytestmark = pytest.mark.unit


def test_unproved_complete_reading_is_flagged_without_fabricating_a_resolution():
    original = dict(source=['modem'], model=['modern'], chosen=['modern'],
                    status='UNRESOLVED', method='unproved_shared_stroke_identity', source_image_only=True)
    result = dict(tokens=['modern'], marked={0}, word_notes={0: original})
    tiers.prepare(result)
    assert result['flagged_readings'][0]['reading'] == 'modern'
    assert not result['word_notes'][0]['source_image_only']
    assert original['source_image_only'] and original['status'] == 'UNRESOLVED'
    root = ET.fromstring(tiers.flagged_html('modern', 'images/word.png', 'own-note'))
    assert root.find('.//a').get('href') == '#own-note'
    assert root.find('.//img').get('src') == 'images/word.png'
    assert root.find('.//img').get('alt') == ''
    assert ''.join(root.itertext()).strip() == 'modern'


@pytest.mark.parametrize('method', ['unproved_native_character_map', 'incomplete_vertical_word_ink',
                                   'unproved_doubtful_leading_word_ink'])
def test_opaque_codes_or_missing_ink_do_not_become_guessed_assistance(method):
    choice = tiers.choose('garden', dict(status='UNRESOLVED', method=method, source=['garden'], model=['garden']))
    assert choice['tier'] == 'print' and choice['reading'] is None


def test_numeric_identifier_uses_complete_source_candidate_but_remains_flagged():
    choice = tiers.choose('7E42', dict(status='UNRESOLVED', source=['71E42'], model=['7E42']))
    assert choice == dict(tier='flagged', reading='71E42', method='source_complete_numeric_identifier')


def test_resolved_correction_keeps_its_existing_proof_and_notice():
    assert tiers.choose('modern', dict(status='RESOLVED'))['tier'] == 'proved'


@pytest.mark.parametrize('a,b,same', [('reader’s', "reader's", True), ('ﬁeld', 'field', True),
                                      ('co\u00adoperate', 'cooperate', True), ('cāre', 'care', False),
                                      ('modem', 'modern', False), ('O', '0', False)])
def test_only_representation_differences_are_typographically_equivalent(a, b, same):
    assert tiers.equivalent(a, b) is same


def test_real_page_compiler_keeps_plausible_reading_with_its_own_print(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa, vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer, block
    printed = 'The modern garden stays green.'
    with pymupdf.open() as drawn, pymupdf.open() as scan:
        pg = drawn.new_page(width=350, height=140)
        pg.insert_text((25, 60), printed, fontsize=12)
        words = [dict(text=w[4], bbox=list(w[:4]), confidence=95) for w in pg.get_text('words')]
        pg = scan.new_page(width=350, height=140)
        pg.insert_image(pg.rect, pixmap=drawn[0].get_pixmap())
        monkeypatch.setattr(pa, 'read_crop', lambda *a, **kw: pa.CropWitness(words))
        # This is a real unresolved occurrence, not a fabricated proof. The
        # rendering contract keeps its plausible reader and actual own image.
        original = pa.adjudicate
        def unsettled(*a, **kw):
            result = original(*a, **kw)
            result['marked'].add(1)
            result.setdefault('word_notes', {})[1] = dict(source=['modem'], model=['modern'],
                chosen=['modern'], status='UNRESOLVED', method='unproved_shared_stroke_identity',
                source_image_only=True, source_bbox=words[1]['bbox'])
            return result
        monkeypatch.setattr(pa, 'adjudicate', unsettled)
        class Package:
            def __init__(self): self.data = {}
            def image(self, name, data): self.data[name] = data
        package = Package(); images = {}
        page, record = vp.compile_page(answer(block(printed)), words, SourceDisplay(scan, 0), 0,
            package, images, pixel_words=words, regions={'b1': [[20, 40, 300, 70]]}, body_owners={},
            note_context={'cache': tmp_path/'cache', 'labels': {}, 'catalogue': {}, 'refs': {}})
    html = ''.join(page['body'])
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+html+'</root>')
    flag = root.find('.//span[@class="reflow-flagged-reading"]')
    assert flag is not None and flag.find('span').text == 'modern'
    image = flag.find('.//img')
    assert image.get('src') in package.data
    assert image.get('alt') == ''
    assert 'vertical-align:-' in image.get('style')
    assert record['blocks'][0]['flagged_readings']['1']['tier'] == 'flagged'
    assert record['blocks'][0]['source_image_words'] == []


def test_numeric_identifier_cannot_be_a_silent_deleted_digit(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa, vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer, block
    printed='The printed position is 71E42 beside this marker.'
    with pymupdf.open() as drawn, pymupdf.open() as scan:
        p=drawn.new_page(width=400,height=150);p.insert_text((20,60),printed,fontsize=12)
        source=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in p.get_text('words')]
        pg=scan.new_page(width=400,height=150);pg.insert_image(pg.rect,pixmap=p.get_pixmap())
        wrong=[dict(w,text='7E42'if w['text']=='71E42'else w['text'])for w in source]
        witness=pa.CropWitness(wrong);witness.model_reading=True
        monkeypatch.setattr(pa,'read_crop',lambda *a,**kw:witness)
        class Package:
            def image(self,*a):pass
        page,record=vp.compile_page(answer(block(printed.replace('71E42','7E42'))),source,
            SourceDisplay(scan,0),0,Package(),{},pixel_words=source,regions={'b1':[[15,40,390,75]]},
            body_owners={},note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    b=record['blocks'][0]; index=4
    assert str(index) in b['flagged_readings']
    assert b['published_tokens'][index]=='71E42'
    assert b['flagged_readings'][str(index)]['method']=='source_complete_numeric_identifier'
    assert 'reflow-flagged-reading' in ''.join(page['body'])


def test_a_retained_first_read_without_counterevidence_is_assistance_not_a_proof():
    first=dict(status='RESOLVED',method='complete_vision_reading_without_optical_counterevidence',
               source=['modem'],model=['modern'],chosen=['modern'])
    second=dict(first,status='UNRESOLVED',method='unproved_shared_stroke_identity')
    assert tiers.choose('modern',first)['tier']=='flagged'
    assert tiers.choose('modern',second)['tier']=='flagged'


@pytest.mark.parametrize('source,model,settled',[("reader’s","reader's",True),('ﬁeld','field',True),
    ('cāre','care',False),('modem','modern',False),('O','0',False)])
def test_representation_equivalence_removes_doubt_without_erasing_correction_notice(source,model,settled):
    result=dict(tokens=[model],marked={0},word_notes={0:dict(source=[source],model=[model],chosen=[model],
        status='UNRESOLVED',method='unproved_quote_identity',source_image_only=True)},guard=[])
    tiers.settle_typographic_equivalents(result)
    tiers.prepare(result)
    assert (result['word_notes'][0]['status']=='RESOLVED') is settled
    assert result['marked']=={0}
    if settled:assert not result['flagged_readings'] and not result['word_notes'][0]['source_image_only']


@pytest.mark.parametrize('source,model,expected',[('garden','gardem','garden'),
    ('modem','modern','modern'),('reader','Ravena','Ravena'),('cāre','care','care')])
def test_assistance_ranks_observed_lexical_candidates_without_certifying_them(source,model,expected):
    decision=dict(source=[source],model=[model],chosen=[model],status='UNRESOLVED',method='unsettled_alternate_pixel_readings')
    choice=tiers.choose(model,decision)
    assert choice['tier']=='flagged' and choice['reading']==expected
    assert decision['status']=='UNRESOLVED' and decision['chosen']==[model]


def assert_flagged_print(markup, readings=None):
    """Every plausible run keeps live words and linked inline or shared print."""
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+markup+'</root>')
    flags = [n for n in root.iter() if n.get('class') == 'reflow-flagged-reading']
    assert flags
    actual = []
    for flag in flags:
        live = next(n for n in flag if n.get('class') == 'reflow-plausible')
        reading = ''.join(live.itertext())
        assert reading.strip()
        links = [n for n in flag.iter() if n.get('class') == 'source-word-image']
        if links:
            assert len(links) == 1
            link = links[0]
        else:
            # Dense or refused inline evidence has one explicit unit print.
            units = [n for n in root.iter() if n.get('class') == 'reflow-unit-print']
            assert len(units) == 1
            link = units[0][0]
            assert link.get('class') == 'source-disclosure-ref'
            assert len(link) == 2 and link[1].tag == 'sup' and link[1].text == '†'
        assert link.get('href', '').startswith('#v4-')
        assert link[0].tag == 'img'
        assert link[0].get('src', '').startswith('images/') and link[0].get('alt') == ''
        actual.append(reading)
    if readings is not None:
        assert ' '.join(actual).split() == readings
    return root, flags


def test_native_assistance_keeps_its_first_reading_without_lexical_overrule():
    decision=dict(source=['remalns'],model=['remalns'],alternative=['remains'],
                  status='UNRESOLVED',method='pixel_read_inconclusive')
    choice=tiers.choose('remalns',decision,rank_lexical=False)
    assert choice['tier']=='flagged' and choice['reading']=='remalns'


@pytest.mark.parametrize('prove_one',[False,True])
def test_compiler_shares_one_legible_print_for_a_dense_multiline_reading(tmp_path,monkeypatch,prove_one):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    lines=['The garden grows green.','The clear sample remains readable.']
    printed=' '.join(lines)
    with pymupdf.open()as drawn,pymupdf.open()as scan:
        pg=drawn.new_page(width=350,height=180)
        for y,line in zip((60,90),lines):pg.insert_text((30,y),line,fontsize=16)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=95)for w in pg.get_text('words')]
        pg=scan.new_page(width=350,height=180);pg.insert_image(pg.rect,pixmap=drawn[0].get_pixmap())
        monkeypatch.setattr(pa,'read_crop',lambda *a,**kw:pa.CropWitness(words))
        original=pa.adjudicate
        def unsettled(*a,**kw):
            result=original(*a,**kw);result['adjudication']=[]
            result['marked']=set(range(len(result['tokens'])))
            result['word_notes']={i:dict(source=[word],model=[word],chosen=[word],
                status='UNRESOLVED',method='pixel_read_inconclusive',source_bbox=words[i]['bbox'])
                for i,word in enumerate(result['tokens'])}
            if prove_one:result['word_notes'][2].update(status='RESOLVED',method='independent_upright_crop')
            return result
        monkeypatch.setattr(pa,'adjudicate',unsettled)
        class Package:
            def __init__(self):self.data={}
            def image(self,name,data):self.data[name]=data
        package=Package();images={}
        page,record=vp.compile_page(answer(block(printed)),words,SourceDisplay(scan,0),0,
            package,images,pixel_words=words,regions={'b1':[[25,40,310,100]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    expected=[word for i,word in enumerate(printed.split())if not(prove_one and i==2)]
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(page['body'])+'</root>')
    flags=[n for n in root.iter()if n.get('class')=='reflow-flagged-reading']
    assert ' '.join(''.join(n.find('span').itertext())for n in flags).split()==expected
    def visible(node):
        if 'source-disclosure-ref' in node.get('class','').split():return ''
        return (node.text or '')+''.join(visible(child)+(child.tail or '') for child in node)
    assert visible(root).split()==printed.split(),'A proved neighbor cannot hide a plausible word'
    assert all(flag.find('.//img')is None for flag in flags)
    unit=root.find('.//div[@class="reflow-unit-print"]')
    assert unit is not None and len(list(root.iter('sup')))==1+int(prove_one)
    image=unit.find('.//img');metadata=images[image.get('src')]
    assert metadata['kind']=='flagged_unit_preview'
    assert metadata['reason']=='dense_flagged_reading'
    assert metadata['bbox'][1]<=words[0]['bbox'][1]
    assert metadata['bbox'][3]>=words[-1]['bbox'][3]
    assert image.get('style')is None
    assert '.reflow-unit-print img { max-width:95%; width:auto; height:auto;' in tiers.stylesheet(), 'Several printed lines retain their readable aspect ratio'


def test_appended_print_has_named_evidence_link_without_repeating_the_reading():
    root = ET.fromstring(tiers.flagged_html('modern', 'images/word.png', 'own-note'))
    link = root.find('a')
    assert link.get('title') == 'Inspect original print'
    assert link.get('href') == '#own-note'
    assert link.find('img').get('alt') == ''
    assert ''.join(root.itertext()) == 'modern'


@pytest.mark.parametrize('first,source', [('cāre', 'care'), ('garden’s', 'gardens')])
def test_dictionary_ranker_cannot_erase_a_reader_diacritic_or_possessive(first, source, monkeypatch):
    from cps.services.reflow import text_quality
    monkeypatch.setattr(text_quality, 'english_lexicon', lambda: {'care', 'gardens'})
    choice = tiers.choose(first, dict(status='UNRESOLVED', method='unproved_quote_identity',
                                     source=[source], model=[first]))
    assert choice['tier'] == 'flagged'
    assert choice['reading'] == first


@pytest.mark.parametrize('decoded_words',[True,False])
@pytest.mark.parametrize('whole,pieces',[('carefully',['care','fuily']),('11th',['I','Ith'])])
@pytest.mark.parametrize('split_source_ranges',[False,True])
@pytest.mark.parametrize('boundary',[True,False,None])
def test_real_compiler_assigns_a_tier_to_the_whole_word_not_its_optical_fragments(tmp_path,monkeypatch,boundary,split_source_ranges,whole,pieces,decoded_words):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    printed=f'The clear garden grows {whole} today.'
    with pymupdf.open()as drawn,pymupdf.open()as scan:
        pg=drawn.new_page(width=400,height=160);pg.insert_text((20,60),printed,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in pg.get_text('words')]
        pg=scan.new_page(width=400,height=160);pg.insert_image(pg.rect,pixmap=drawn[0].get_pixmap())
        witness=pa.CropWitness(words if decoded_words else [])
        seen_context=[]
        witness.fragment_boundary=lambda word:seen_context.append(dict(word)) or boundary
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:witness)
        old=pa.adjudicate
        def fragmented(*a,**kw):
            result=old(*a,**kw);box=words[4]['bbox'];middle=(box[0]+box[2])/2
            note=dict(source=pieces[:],model=[whole],chosen=pieces[:],
                status='UNRESOLVED',method='unproved_source_transcription_change',source_range=[4,6],model_range=[4,5])
            result.update(tokens=printed.split()[:4]+pieces+['today.'],marked={4,5},
                boxes={**{i:w['bbox']for i,w in enumerate(words[:4])},4:[box[0],box[1],middle-.1,box[3]],
                       5:[middle+.1,box[1],box[2],box[3]],6:words[5]['bbox']},
                decisions=[note],word_notes={i:dict(note,chosen=[t],mapping='unplaced_within_span')for i,t in zip((4,5),pieces)})
            if split_source_ranges:
                for i in (4,5):result['word_notes'][i]['source_range']=[i,i+1]
            return result
        monkeypatch.setattr(pa,'adjudicate',fragmented)
        class Package:
            def image(self,*a):pass
        page,receipt=vp.compile_page(answer(block(printed)),words,SourceDisplay(scan,0),0,
            Package(),{},pixel_words=words,regions={'b1':[[18,40,380,75]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    record=receipt['blocks'][0]
    if boundary is True:
        assert record['published_tokens']==printed.split()
        assert [v['reading']for v in record['flagged_readings'].values()]==[whole]
        root,flags=assert_flagged_print(''.join(page['body']),[whole])
        assert len(flags)==1
        if whole[0].isdigit():assert any(c.get('ordinal_candidate')==whole for c in seen_context)
        assert not any(d['status']=='RESOLVED'for d in record['adjudication'])
    else:
        assert record['published_tokens'][4:6]==pieces,'An unproved boundary cannot silently fuse printed words'


@pytest.mark.parametrize('ink',['leaders','letters','uneven','no_folio'])
def test_real_compiler_does_not_publish_contents_leader_dots_as_words(tmp_path,monkeypatch,ink):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa,vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer,block
    with pymupdf.open()as drawn,pymupdf.open()as scan:
        pg=drawn.new_page(width=300,height=150);pg.insert_text((20,60),'Garden',fontsize=12)
        pg.insert_text((200,60),'12'if ink!='no_folio'else'now',fontsize=12)
        if ink=='letters':
            pg.insert_text((100,60),'eee',fontsize=12)
            box=list(next(w for w in pg.get_text('words')if w[4]=='eee')[:4])
        else:
            centers=[100+i*4 for i in range(16)]
            if ink=='uneven':centers=[100,103,111,117,118,122,130,131,143,155]
            for x in centers:pg.draw_circle((x,59),.6,color=None,fill=(0,0,0))
            box=[min(centers)-1,58,max(centers)+1,60]
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=99)for w in pg.get_text('words')if w[4]!='eee']
        words.insert(1,dict(text='eee',bbox=box,confidence=99))
        copied=scan.new_page(width=300,height=150);copied.insert_image(copied.rect,pixmap=pg.get_pixmap(matrix=pymupdf.Matrix(3,3)))
        monkeypatch.setattr(pa,'read_crop',lambda *a,**k:pa.CropWitness(words))
        class Package:
            def image(self,*a):pass
        compiled,receipt=vp.compile_page(answer(block('Garden eee '+('12'if ink!='no_folio'else'now'))),words,
            SourceDisplay(scan,0),0,Package(),{},pixel_words=words,regions={'b1':[[15,40,240,70]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    record=receipt['blocks'][0]
    assert ('eee'in record['published_tokens'])is(ink!='leaders')
    if ink=='leaders':
        assert record['published_tokens']==['Garden','12']
        assert len(record['decorative_source_regions'])==1
        row=next(row for row in record['occurrence_coverage']if row['source_text']=='eee')
        assert row['disposition']=='excluded'and row['rule']=='measured_contents_dot_leader'


@pytest.mark.parametrize('source,model,expected',[('Ist','1st','1st'),('llh','11th','11th'),
    ('both','8th','both'),('9th','8th','9th')])
def test_typed_ordinal_assistance_prefers_a_complete_reader_over_opaque_letter_guesses(source,model,expected):
    decision=dict(source=[source],model=[model],chosen=[source],status='UNRESOLVED',method='pixel_read_inconclusive')
    choice=tiers.choose(source,decision)
    assert choice['tier']=='flagged'and choice['reading']==expected
    assert decision['status']=='UNRESOLVED'and decision['chosen']==[source]


@pytest.mark.parametrize('token,expected', [('Surnmer','Summer'),('SURNMER','SUMMER'),('surnmer','summer')])
def test_optical_map_candidate_can_rank_an_explicitly_flagged_heading(monkeypatch,token,expected):
    from cps.services.reflow import pixel_adjudication as pa,text_quality
    monkeypatch.setattr(text_quality,'english_lexicon',lambda:{'summer','garden'})
    decision=pa.optical_map_doubts([token])[0]
    assert decision['status']=='UNRESOLVED'
    choice=tiers.choose(token,decision)
    assert choice['tier']=='flagged' and choice['reading']==expected
    assert decision['chosen']==[token], 'Ranking assistance must not overwrite the optical receipt'


@pytest.mark.parametrize('optical,first', [('“Lgrow', ['“I', 'grow']),
    ('itorains', ['it', 'rains']), ('nearugarden', ['near', 'a', 'garden'])])
def test_fused_source_occurrence_keeps_complete_first_reader_assistance(optical, first):
    from cps.services.reflow import pixel_adjudication as pa
    source=[dict(text=t,bbox=[20+60*i,20,75+60*i,32],confidence=95)
            for i,t in enumerate(['Read',optical,'today.'])]
    witness=pa.CropWitness();witness.model_reading=True
    result=pa.adjudicate(['Read',*first,'today.'],source,witness)
    tiers.prepare(result)
    assert result['tokens']==['Read',' '.join(first),'today.']
    note=result['word_notes'][1]
    assert note['model']==first and note['source']==[optical]
    assert note['source_range']==[1,2] and note['model_range']==[1,1+len(first)]
    assert note['status']=='UNRESOLVED'
    assert note['publication']['tier']=='flagged'
    assert note['source_bbox']==source[1]['bbox']
    assert result['marked']=={1}


def test_a_valid_source_word_is_not_split_by_lexical_assistance():
    choice=tiers.choose('carpet',dict(source=['carpet'],model=['car','pet'],
        status='UNRESOLVED',method='unproved_source_transcription_change'))
    assert choice['reading']=='carpet' and choice['tier']=='flagged'


def test_real_compiler_does_not_publish_a_fused_optical_nonword_as_assistance(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import pixel_adjudication as pa, vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer, block
    printed='I grow plants today.'
    with pymupdf.open() as drawn,pymupdf.open() as scan:
        page=drawn.new_page(width=350,height=140)
        page.insert_text((25,60),printed,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]),confidence=95)for w in page.get_text('words')]
        fused=dict(text='Lgrow',bbox=[words[0]['bbox'][0],words[0]['bbox'][1],
            words[1]['bbox'][2],words[1]['bbox'][3]],confidence=95)
        optical=[fused,*words[2:]]
        page=scan.new_page(width=350,height=140);page.insert_image(page.rect,pixmap=drawn[0].get_pixmap())
        # A missing second crop read cannot certify the fused source token.
        witness=pa.CropWitness();witness.model_reading=True
        monkeypatch.setattr(pa,'read_crop',lambda *a,**kw:witness)
        class Package:
            def __init__(self):self.data={}
            def image(self,name,data):self.data[name]=data
        package=Package()
        page,record=vp.compile_page(answer(block(printed)),optical,SourceDisplay(scan,0),0,
            package,{},pixel_words=optical,regions={'b1':[[20,40,320,75]]},body_owners={},
            note_context={'cache':tmp_path/'cache','labels':{},'catalogue':{},'refs':{}})
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(page['body'])+'</root>')
    plausible=root.find('.//span[@class="reflow-plausible"]')
    assert plausible is not None and ''.join(plausible.itertext())=='I grow'
    assert 'Lgrow' not in ''.join(root.itertext())
    assert record['blocks'][0]['flagged_readings']['0']['tier']=='flagged'
    assert record['blocks'][0]['adjudication'][0]['status']=='UNRESOLVED'
    assert root.find('.//img').get('src') in package.data


@pytest.mark.parametrize('source,first',[(['l','llh'],'11th'),(['I','Ith'],'11th'),(['7','st'],'1st')])
@pytest.mark.parametrize('boundary',[True,False,None])
def test_typed_ordinal_fragments_get_one_flagged_whole_word_only_with_printed_boundary(source,first,boundary):
    from cps.services.reflow import pixel_adjudication as pa
    note=dict(source=source,model=[first],chosen=source,status='UNRESOLVED',
        method='unproved_source_transcription_change',source_range=[0,2],model_range=[0,1])
    result=dict(tokens=source[:],marked={0,1},boxes={0:[20,20,25,32],1:[25.2,20,40,32]},
        word_notes={i:dict(note,source=[token],chosen=[token],model=[],source_range=[i,i+1])for i,token in enumerate(source)},
        adjudication=[note],guard=[])
    witness=pa.CropWitness();witness.fragment_boundary=lambda word:boundary
    tiers.join_fragmented_readings(result,witness)
    tiers.prepare(result)
    if boundary is True:
        assert result['tokens']==[first]
        assert result['marked']=={0}
        assert result['word_notes'][0]['status']=='UNRESOLVED'
        assert result['word_notes'][0]['source']==source
        assert result['flagged_readings'][0]['reading']==first
        assert result['boxes'][0]==[20,20,40,32]
    else:
        assert result['tokens']==source
        assert result['marked']=={0,1}


@pytest.mark.parametrize('gap,clipped,count,expected',[(18,False,4,True),(26,False,4,False),(18,True,4,None),(18,False,3,None)])
def test_ordinal_context_groups_ambiguous_sidebearings_without_certifying_letters(gap,clipped,count,expected,tmp_path,monkeypatch):
    import pymupdf
    from PIL import Image,ImageDraw
    from cps.services.reflow import pixel_adjudication as pa
    image=Image.new('RGB',(128,80),'white');draw=ImageDraw.Draw(image)
    positions=[(0 if clipped else 4,19),(19+gap,34+gap),(70,85),(95,119)]
    for left,right in positions[:count]:draw.rectangle((left,8,right-1,66),fill='black')
    class Display:
        rect=pymupdf.Rect(0,0,16,10)
        def pixmap(self,*a,**kw):
            import io
            stream=io.BytesIO();image.save(stream,format='PNG');return pymupdf.Pixmap(stream.getvalue())
    word=dict(bbox=[0,0,16,10])
    ordinary=pa._fragment_word_boundary(Display(),word)
    grouped=pa._fragment_word_boundary(Display(),dict(word,ordinal_candidate='11th'))
    assert grouped is expected
    monkeypatch.setattr(pa,'_read_crop',lambda *a,**kw:[])
    witness=pa.read_crop(Display(),[word['bbox']],tmp_path/'cache')
    assert len(witness)==0 and witness.fragment_boundary(word)is ordinary
    assert witness.fragment_boundary(dict(word,ordinal_candidate='11th'))is expected
    if expected is True:
        assert ordinary is None,'Typed assistance does not change the stricter ordinary boundary proof'
        note=dict(source=['I','Ith'],model=['11th'],chosen=['11th'],status='UNRESOLVED',
            method='plausible_complete_word_over_optical_fragments')
        assert tiers.choose('11th',note)['tier']=='flagged'
