"""Observe actual roles, including partial output; approval alone proves nothing."""
import zipfile
import pytest
from cps.services.reflow import operation_audit as audit
pytestmark=pytest.mark.unit


def test_observer_scopes_same_text_to_source_page_and_preserves_uncertainty(tmp_path):
    path=tmp_path/'actual.epub'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('OEBPS/content.opf','''<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="ch001.xhtml"/><item id="b" href="ch002.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>''')
        z.writestr('OEBPS/ch001.xhtml','''<html xmlns="http://www.w3.org/1999/xhtml"><body><span id="pg_0000" class="reflow-page"/><blockquote><p>Same source words</p></blockquote><span id="pg_0001" class="reflow-page"/><p>Same source words</p></body></html>''')
        z.writestr('OEBPS/ch002.xhtml','''<html xmlns="http://www.w3.org/1999/xhtml"><body><span id="pg_0001" class="reflow-page"/><blockquote><p>Qualified source <sup><span class="reflow-uncertain" title="number or association read from a damaged text layer">39 (?)</span></sup></p></blockquote><h2>Repeated</h2><h2>Repeated</h2></body></html>''')
    def row(cid,page,role):return dict(candidate_id=cid,page_index0=page,proposed_state=role)
    rows=[row('yes',0,'blockquote'),row('wrong-page',1,'blockquote'),row('uncertain',1,'blockquote'),row('ambiguous',1,'h2')]
    observed=audit.observe(path,rows,{'yes':('Same source words',set()),'wrong-page':('Same source words',set()),'uncertain':('Qualified source 39',{'39'}),'ambiguous':('Repeated',set())})
    assert [r['status'] for r in observed]==['verified','not_found','verified','ambiguous']
    assert observed[2]['epub_entry']=='OEBPS/ch002.xhtml'
    with zipfile.ZipFile(path) as z:assert b'39 (?)' in z.read('OEBPS/ch002.xhtml')


def test_chapter_prefix_without_page_marker_cannot_inherit_previous_page(tmp_path):
    path=tmp_path/'unscoped.epub'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('OEBPS/content.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="ch001.xhtml"/><item id="b" href="ch002.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        z.writestr('OEBPS/ch001.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><body><span class="reflow-page" id="pg_0000"/><p>ordinary prose</p></body></html>')
        z.writestr('OEBPS/ch002.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><body><blockquote>Source words</blockquote><span class="reflow-page" id="pg_0001"/><blockquote>Actually scoped</blockquote></body></html>')
    rows=[dict(candidate_id='unscoped',page_index0=0,proposed_state='blockquote'),dict(candidate_id='scoped',page_index0=1,proposed_state='blockquote')]
    found=audit.observe(path,rows,{'unscoped':('Source words',set()),'scoped':('Actually scoped',set())})
    assert [r['status'] for r in found]==['not_found','verified']


from tests.unit.test_reflow_structural_ops import source


@pytest.fixture
def image_quote(source):
    from types import SimpleNamespace
    from cps.services.reflow import enriched_source, structural_ops as ops, extract, build_epub
    from cps.services.reflow.native_text import descriptor
    book,doc,tmp=source
    element=book.pages[0][1]
    element.runs[2]=['glyph','source',descriptor(0,(110,113,143,128),12,'Times',reason='transcript')]
    element.runs[3:4]=[['t',' '],['glyph','sentence.',descriptor(0,(144,113,190,128),12,'Times',reason='transcript')],['t','"']]
    canonical=enriched_source.prepare_source_page(book,0,{'layer':'native'})
    prepared=ops.prepare(book,doc,0,'10.1',{'layer':'native'},source_page=canonical,raw_page=extract.read_page(doc,0))
    candidate=next(c for c in prepared.candidates() if c['kind']=='quote')
    plan=prepared.accept(book,doc,dict(protocol=ops.PROTOCOL,snapshot_id=prepared.snapshot_id,select=[candidate['candidate_id']]),source_page=canonical)
    result=SimpleNamespace(book=book,operation_plans=[plan],stage_records=[])
    path=tmp/'image-quote.epub'
    build_epub.build(book,str(path),doc=doc,source_pages={0:canonical},operation_plans=[plan])
    return result,doc,path


def test_current_source_image_quote_is_observed_without_inventing_unicode(image_quote):
    result,doc,path=image_quote
    rows,matching=audit.capture(result,567,document=doc)
    observed=audit.observe(path,rows,matching)
    assert observed[0]['status']=='verified'
    assert observed[0]['role']=='blockquote'
    # A source binding is mandatory; a path/label alone is not evidence.
    rows,matching=audit.capture(result,567)
    assert audit.observe(path,rows,matching)[0]['status']=='not_found'


@pytest.mark.parametrize('mutation',['pixels','substitute','reorder','missing','text','role','label','link','resource_missing','ancestry'])
def test_image_quote_rejects_changed_content_or_source_binding(image_quote,mutation):
    from xml.etree import ElementTree as ET
    result,doc,path=image_quote
    rows,matching=audit.capture(result,567,document=doc)
    with zipfile.ZipFile(path) as z:files={name:z.read(name) for name in z.namelist()}
    name=next(name for name in files if name.startswith('OEBPS/ch') and name.endswith('.xhtml'))
    root=ET.fromstring(files[name]);quote=next(root.iter(audit.N+'blockquote'))
    atoms=[n for n in quote.iter(audit.N+'a') if n.get('class')=='source-glyph']
    images=[next(n.iter(audit.N+'img')) for n in atoms]
    if mutation=='pixels':files['OEBPS/'+images[0].get('src')]=b'forged pixels'
    elif mutation=='substitute':images[0].set('src',images[1].get('src'))
    elif mutation=='reorder':
        left,right=images[0].get('src'),images[1].get('src')
        images[0].set('src',right);images[1].set('src',left)
    elif mutation=='missing':atoms[0].remove(images[0])
    elif mutation=='text':quote.text='Added invented words'
    elif mutation=='role':quote.tag=audit.N+'p'
    elif mutation=='label':images[0].set('alt','source')
    elif mutation=='link':atoms[0].set('href','original-p0001.xhtml#page')
    elif mutation=='resource_missing':del files['OEBPS/'+images[0].get('src')]
    elif mutation=='ancestry':
        parent=next(n for n in quote.iter() if atoms[0] in list(n))
        index=list(parent).index(atoms[0]);parent.remove(atoms[0])
        raised=ET.Element(audit.N+'sup');raised.append(atoms[0]);parent.insert(index,raised)
    files[name]=ET.tostring(root)
    with zipfile.ZipFile(path,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    assert audit.observe(path,rows,matching)[0]['status']=='not_found'


def test_forged_canonical_provenance_cannot_mint_image_evidence(image_quote):
    from dataclasses import replace
    from cps.services.reflow.structural_ops import ContractError
    result,doc,_=image_quote
    plan=result.operation_plans[0]
    forged=replace(plan.prepared.source_page, provenance_json='{"layer":"forged"}')
    forged=replace(forged, seal=forged._identity())
    result.operation_plans=[replace(plan,prepared=replace(plan.prepared,source_page=forged))]
    with pytest.raises(ContractError,match='factory preparation'):
        audit.capture(result,567,document=doc)


def test_missing_supervised_native_capture_fails_closed():
    from types import SimpleNamespace
    with pytest.raises(ValueError,match='supervised operation-audit capture'):
        audit.capture(SimpleNamespace(operation_plans=[]),567,document=SimpleNamespace(name='source.pdf'))
