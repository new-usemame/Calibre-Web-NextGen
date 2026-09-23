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
        z.writestr('OEBPS/ch002.xhtml','''<html xmlns="http://www.w3.org/1999/xhtml"><body><blockquote><p>Qualified source <sup><span class="reflow-uncertain" title="number or association read from a damaged text layer">39 (?)</span></sup></p></blockquote><h2>Repeated</h2><h2>Repeated</h2></body></html>''')
    def row(cid,page,role):return dict(candidate_id=cid,page_index0=page,proposed_state=role)
    rows=[row('yes',0,'blockquote'),row('wrong-page',1,'blockquote'),row('uncertain',1,'blockquote'),row('ambiguous',1,'h2')]
    observed=audit.observe(path,rows,{'yes':('Same source words',set()),'wrong-page':('Same source words',set()),'uncertain':('Qualified source 39',{'39'}),'ambiguous':('Repeated',set())})
    assert [r['status'] for r in observed]==['verified','not_found','verified','ambiguous']
    assert observed[2]['epub_entry']=='OEBPS/ch002.xhtml'
    with zipfile.ZipFile(path) as z:assert b'39 (?)' in z.read('OEBPS/ch002.xhtml')
