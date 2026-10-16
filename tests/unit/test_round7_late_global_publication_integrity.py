"""Admitted source reading order, package parts and page destinations survive late globals."""
import copy,zipfile
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import build_epub
from tests.unit.round7_recovery_test_support import fixture,publish,h,TEXTS

@pytest.mark.parametrize('failure',['chapter_duplicate','chapter_reorder','page_home','nav_page_list','opf_spine','successful_archive_corruption'])
def test_late_success_or_failure_cannot_change_admitted_source_publication(tmp_path,monkeypatch,failure):
    doc,reader,_=fixture(tmp_path,monkeypatch);reached=[]
    if failure.startswith('chapter_'):
        actual=build_epub._chapters
        def broken(pages):
            result=actual(pages);reached.append(failure)
            if failure=='chapter_duplicate':result[0].blocks.append(result[0].blocks[0])
            else:
                positions=[(c,i) for c in result for i,b in enumerate(c.blocks) if b.startswith('<p>')]
                (a,i),(b,j)=positions[0],positions[-1];a.blocks[i],b.blocks[j]=b.blocks[j],a.blocks[i]
            return result
        monkeypatch.setattr(build_epub,'_chapters',broken)
    elif failure=='page_home':
        actual=build_epub._page_homes
        def broken(chapters):
            reached.append(failure);result=actual(chapters);result.pop(1,None);return result
        monkeypatch.setattr(build_epub,'_page_homes',broken)
    elif failure in ('nav_page_list','opf_spine'):
        name='_nav' if failure=='nav_page_list' else '_opf';actual=getattr(build_epub,name)
        def broken(*args,**kwargs):
            reached.append(failure);root=ET.fromstring(actual(*args,**kwargs))
            tag='nav' if failure=='nav_page_list' else 'spine'
            target=next(e for e in root.iter() if e.tag.rsplit('}',1)[-1]==tag and (failure!='nav_page_list' or e.get('id')=='page-list'))
            target.remove(list(target)[0]);return ET.tostring(root,encoding='unicode')
        monkeypatch.setattr(build_epub,name,broken)
    else:
        actual=build_epub._Package.finish
        def broken(package,parts):
            reached.append(failure);parts=copy.deepcopy(parts)
            href=next(n for n in parts['documents'] if n.startswith('ch'))
            parts['documents'][href]='<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Empty</title></head><body/></html>'
            return actual(package,parts)
        monkeypatch.setattr(build_epub._Package,'finish',broken)
    path,report,docs=publish(doc,reader,tmp_path)
    assert reached,'the actual public late-global injection must run'
    assert h.body_texts(docs)==list(TEXTS),'all SOURCE paragraphs survive once and in original reading order'
    assert report.get('degraded_units',[])==[] and reader.calls==[0,1,2]
    with zipfile.ZipFile(path) as archive:
        nav=ET.fromstring(archive.read('OEBPS/nav.xhtml'))
        page_list=next(e for e in nav.iter() if e.tag.rsplit('}',1)[-1]=='nav' and e.get('id')=='page-list')
        links=[e.get('href') for e in page_list.iter() if e.tag.rsplit('}',1)[-1]=='a']
        assert len(links)==3
        for n in range(3):
            href=report['page_homes'].get(n,report['page_homes'].get(str(n)))
            assert href+'#pg_%04d'%n in links
            root=ET.fromstring(archive.read('OEBPS/'+href));assert any(e.get('id')=='pg_%04d'%n for e in root.iter())
