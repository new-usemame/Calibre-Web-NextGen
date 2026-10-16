"""Post-implementation independent actual-operation controls for two new guards.

Only synthetic original source pages; faults originate inside the actual guard,
never from replacing the guard-owning helper or a provider/model operation.
"""
import copy,inspect,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import assemble,skeleton,build_epub,vision_pipeline as vp
from tests.unit.round7_recovery_test_support import TEXTS,h
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('fault',['source_dict','owned_words','invalid_span_font'])
def test_native_caption_source_operation_failure_is_visible_and_page_owned(tmp_path,monkeypatch,fault):
    doc=pymupdf.open();pages={};figures=[];caption_box=None
    for n,prose in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600);page.insert_text((40,60),prose,fontsize=8)
        words=page.get_text('words');box=(min(w[0]for w in words),min(w[1]for w in words),max(w[2]for w in words),max(w[3]for w in words))
        pages[n]=[assemble.Element('p',runs=[['t',prose]],pno=n,bbox=box)]
        if n==1:
            art=(60,100,360,200);page.draw_rect(art,color=(0,0,0),width=2);page.draw_line((70,110),(350,190),color=(0,0,0),width=2)
            pages[n].append(assemble.Element('fig',pno=n,bbox=art,placed='geometry'));figures.append(dict(pno=n,bbox=art,found='native'))
            label='A Synthetic Plate';width=pymupdf.get_text_length(label,fontsize=8);page.insert_text((210-width/2,215),label,fontsize=8)
            span=next(sp for b in page.get_text('dict')['blocks'] for line in b.get('lines',[]) for sp in line['spans'] if sp['text']==label);caption_box=tuple(span['bbox'])
            pages[n].append(assemble.Element('h',runs=[['t',label]],pno=n,bbox=caption_box,line_boxes=[caption_box],level=2))
    book=assemble.Book(elements=[e for group in pages.values()for e in group],pages=pages,figures=figures,style=skeleton.BookStyle(body_size=8,page_count=3));before=copy.deepcopy(book);reached=[]
    if fault in ('source_dict','invalid_span_font'):
        actual=pymupdf.Page.get_text
        def operation(page,*args,**kwargs):
            caller=inspect.currentframe().f_back
            inside=caller.f_code.co_name=='_native_caption_ownership'
            if page.number==1 and inside and args and args[0]=='dict' and not reached:
                assert kwargs.get('flags')==vp.extract.pymupdf.TEXTFLAGS_TEXT
                reached.append({'page':1,'operation':'Page.get_text(dict,TEXTFLAGS_TEXT)','fault':fault})
                if fault=='source_dict':raise ValueError('synthetic native caption original dictionary failure')
                result=copy.deepcopy(actual(page,*args,**kwargs));changed=0
                for b in result['blocks']:
                    for line in b.get('lines',[]):
                        for span in line['spans']:
                            if span['text']=='A Synthetic Plate':span['size']='invalid synthetic font size';changed+=1
                assert changed==1;return result
            return actual(page,*args,**kwargs)
        monkeypatch.setattr(pymupdf.Page,'get_text',operation)
    else:
        actual=vp.owned_words
        def operation(words,box,*args,**kwargs):
            caller=inspect.currentframe().f_back
            if caller.f_code.co_name=='_native_caption_ownership' and tuple(box)==caption_box and not reached:
                reached.append({'page':1,'operation':'owned_words(physical,actual_caption_bbox)','fault':fault});raise ValueError('synthetic native caption source ownership operation failure')
            return actual(words,box,*args,**kwargs)
        monkeypatch.setattr(vp,'owned_words',operation)
    class Reader:
        cache=tmp_path/'offline'
        def read(self,*args,**kwargs):raise AssertionError('native source guard controls cannot dispatch a model')
        def reread(self,*args,**kwargs):raise AssertionError('native source guard controls have no authorized reread')
    Reader.cache.mkdir();out=tmp_path/'actual.epub'
    try:report=vp.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached and len(reached)==1,'exact source operation must fail inside the real new guard'
    assert book==before,'caller-owned cached native preparation stays immutable'
    assert build_epub.validate(out)==[] and set(map(int,report['page_homes']))=={0,1,2}
    stage='native_caption_source' if fault=='source_dict' else 'native_caption_ownership'
    units=report['degraded_units'];assert units and all(u['page']==1 for u in units)
    assert any(u['stage']==stage and u['reason'] for u in units),'visible report retains original failing stage and nonempty reason'
    assert any(e.get('stage')==stage for e in report['unit_recovery_events'])
    source_visible(out,report,{0,1,2})
    with zipfile.ZipFile(out)as z:
        docs={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
        bodies=h.body_texts(docs);assert bodies.count(TEXTS[0])==bodies.count(TEXTS[2])==1,'healthy neighbor live prose remains once'
        refs=[(home,a)for home,root in docs.items()for a in root.findall('.//x:a',h.NS)if a.get('class')=='source-disclosure-ref']
        for unit in units:
            owned=[(home,a)for home,a in refs if a.get('href') and a.get('href').split('#')[-1]==unit['source_reference']];assert owned,'degraded SOURCE unit requires an actual working source control'
            for home,ref in owned:
                dest,ident=h.destination(home,ref.get('href'));target=next(n for n in docs[dest].iter()if n.get('id')==ident)
                assert target.findall('.//x:img',h.NS)
                assert any(a.text=='Return' and h.destination(dest,a.get('href'))==(home,ref.get('id'))for a in target.findall('.//x:a',h.NS))
