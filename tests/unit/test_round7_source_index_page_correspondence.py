"""Existing target IDs do not prove the index label names the correct source page."""
import pymupdf,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
from tests.unit.round7_recovery_test_support import TEXTS,h


def test_source_index_wrong_existing_targets_are_rebuilt_from_admitted_page_homes(tmp_path,monkeypatch):
    doc=pymupdf.open();pages={}
    for n,text in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
        pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=(40,80,340,94),punctuation_uncertain=n==1)]
    book=assemble.Book(elements=[e for group in pages.values() for e in group],pages=pages)
    original=build_epub._source_index;reached=[]
    def incorrect(homes,language,evidence=None):
        result=ET.fromstring(original(homes,language,evidence));anchors=[a for a in result.findall('.//x:a',h.NS) if a.get('href','').find('#pg_')>=0]
        assert len(anchors)>=2
        first,second=anchors[:2];first_target,second_target=first.get('href'),second.get('href')
        first.set('href',second_target);second.set('href',first_target)
        reached.append((first_target,second_target))
        return ET.tostring(result,encoding='unicode')
    monkeypatch.setattr(build_epub,'_source_index',incorrect)
    class Reader:
        cache=tmp_path/'readings'
        def read(self,image):raise AssertionError('Source index repair cannot dispatch a model')
    Reader.cache.mkdir();path=tmp_path/'native-index.epub'
    try:report=vp.convert(doc,str(path),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached,'Actual SOURCE index producer must return wrong but existing targets'
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as archive:
        root=ET.fromstring(archive.read('OEBPS/'+build_epub.SOURCE_INDEX_HREF))
        links=[a for a in root.findall('.//x:a',h.NS) if '#pg_' in a.get('href','')]
        assert len(links)==3
        for n,anchor in enumerate(links):
            home=report['page_homes'].get(n,report['page_homes'].get(str(n)))
            assert ''.join(anchor.itertext())=='PDF page '+str(n+1)
            assert anchor.get('href')==home+'#pg_%04d'%n,'Each SOURCE index label must point to that exact physical page'
        docs={n:ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        assert h.body_texts(docs)==list(TEXTS)
    assert report.get('degraded_units',[])==[],'Generated index corruption must not degrade healthy source prose'
