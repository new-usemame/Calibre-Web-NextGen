"""Independent source-free ordinary-sentence continuation around artwork."""
import io
import posixpath
import zipfile
from PIL import Image
import pytest
if __package__:
    from .test_round2_assembly import answer,block,optical,convert,NS
else:
    from test_round2_assembly import answer,block,optical,convert,NS

@pytest.mark.parametrize('case',['ordinary','footer','uncertain_head','terminal','indented','heading','art_below','art_other_column','inflow_note'])
def test_source_bound_ordinary_sentence_can_continue_around_separate_owned_artwork(tmp_path,monkeypatch,case):
    left='Quiet travelers carry ordinary tools along the northern road with the'
    right='patient workers before the first light arrives.'
    printed_right=right if case!='uncertain_head' else right.replace('patient','careful',1)
    if case=='terminal':left=left[:-3]+'workers.'
    caption='A simple compass.'
    suffix=optical(printed_right,320,190,columns=4,step=45)
    if case=='indented':
        suffix=[dict(w,bbox=[w['bbox'][0]+(14 if i<4 else 0),w['bbox'][1],w['bbox'][2]+(14 if i<4 else 0),w['bbox'][3]]) for i,w in enumerate(suffix)]
    art_x,art_y=(350,230) if case=='art_below' else ((180,35) if case=='art_other_column' else (350,35))
    pixels=optical(left,20,280,columns=5,step=45)+optical('North East South West',art_x,art_y,columns=2,step=60)+optical(caption,320,150)+suffix
    artbox=(530,540,950,700) if case=='art_below' else ((270,30,750,320) if case=='art_other_column' else (530,30,950,320))
    readings=[block(left,bbox=(20,650,480,950)),block('North East South West',ident='f1',role='heading' if case=='heading' else 'figure',bbox=artbox),block(caption,ident='c1',role='caption',bbox=(520,350,980,425)),block(right,ident='b2',bbox=(520,450,980,630),low=(0,) if case=='uncertain_head' else ())]
    if case in ('footer','inflow_note'):
        note_y=350 if case=='footer' else 180
        pixels+=optical('2 A separate printed explanation.',20,note_y,columns=5,step=45)
        readings.insert(1,block('A separate printed explanation.',ident='n1',role='footnote',marker='2',bbox=(20,850,480,960) if case=='footer' else (20,420,480,520)))
    body,documents,result=convert(tmp_path,monkeypatch,[answer(*readings)],[pixels],[[]])
    if case in ('ordinary','footer','uncertain_head'):
        assert left+' '+right in body,'Source-supported ordinary sentence remained split around separate artwork'
        assert left not in body and right not in body,'Fragment paragraphs survived rather than a single sentence paragraph'
    else:
        assert left in body and right in body,'Terminal, indentation, heading, in-flow note and unsupported artwork position are barriers'
    assert body.count(caption)==1,'Caption must remain separate exactly once'
    if case!='heading':
        figures=[e for root in documents.values() for e in root.findall('.//x:figure',NS)]
        if case not in ('art_below','art_other_column'):
            assert len(figures)==1 and len(figures[0].findall('.//x:img',NS))==1,'Original artwork must remain once outside prose'
        else:
            # A complete crop or optional whole-source context carries the art.
            # Original sentence, caption and boundary assertions above remain.
            assert len(figures)==1,'Original artwork must remain once outside prose'
            image=figures[0].find('.//x:img',NS)
            if image is None:
                ref=figures[0].find('.//x:a',NS)
                assert ref is not None and ref.get('class')=='source-disclosure-ref','A fallback must expose its source context'
                name=next(name for name,root in documents.items() if any(n is ref for n in root.iter()))
                file,ident=ref.get('href','').split('#',1)
                target=posixpath.normpath(posixpath.join(posixpath.dirname(name),file)) if file else name
                context=next(n for n in documents[target].iter() if n.get('id')==ident)
                assert context.tag.endswith('}aside') and 'vision-disclosure' in context.get('class','')
                images=context.findall('.//x:img',NS)
                assert len(images)==1,'The source context must actually display the complete original object'
                image=images[0]
                records=[rec for src,rec in result['images'].items() if src==image.get('src') or image.get('src','').endswith('/'+src)]
                assert len(records)==1 and records[0]['kind']=='figure_source_reference'
                assert records[0]['page']==0 and records[0]['bbox']==[0.0,0.0,600.0,400.0],'Source context must retain the full original extent'
                assert any(''.join(a.itertext())=='Return' and a.get('href','').endswith('#'+ref.get('id')) for a in context.findall('.//x:a',NS)),'Return must resolve to the exact figure origin'
                with zipfile.ZipFile(tmp_path/'synthetic.epub') as archive:
                    member=posixpath.normpath(posixpath.join(posixpath.dirname(target),image.get('src')))
                    assert member in archive.namelist(),'Visible source image must be packaged'
                    assert Image.open(io.BytesIO(archive.read(member))).size==(1200,800),'Whole-page source context must not truncate original artwork'
            else:
                assert len(figures[0].findall('.//x:img',NS))==1
                records=[rec for src,rec in result['images'].items() if src==image.get('src') or image.get('src','').endswith('/'+src)]
                assert len(records)==1 and records[0]['kind']=='figure'
                art_words=[w['bbox'] for w in pixels if w['text'] in ('North','East','South','West')]
                extent=[min(b[0] for b in art_words),min(b[1] for b in art_words),max(b[2] for b in art_words),max(b[3] for b in art_words)]
                box=records[0]['bbox']
                assert box[0]<=extent[0] and box[1]<=extent[1] and box[2]>=extent[2] and box[3]>=extent[3],'A direct crop must contain the complete authored object'
    if case=='uncertain_head':
        record=next(b for b in result['pages'][0]['blocks'] if b['id']=='b2')
        assert record['published_tokens'][0]=='patient' and 0 in record['marked'],'Continuation must preserve uncertain published spelling'
