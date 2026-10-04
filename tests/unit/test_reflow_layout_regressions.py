"""No-network behavioral regression: local invalid lexical composition must fall back."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from cps.services.reflow import assemble, skeleton, extract, build_epub, pipeline, layout_pipeline
from cps.services.reflow.ledger import Ledger

import pytest
pytestmark=pytest.mark.unit

class PipelineFallback(unittest.TestCase):
    def test_lexical_chain_cannot_abort_unrelated_valid_page(self):
        doc=pymupdf.open()
        for _ in range(2):doc.new_page(width=500,height=700)
        for y,text in [(100,'anti-'),(125,'dis-'),(150,'establishment')]:
            doc[0].insert_text((50,y),text,fontsize=12)
        doc[1].insert_text((50,100),'The other page has complete ordinary source words.',fontsize=12)
        fixture_dir=tempfile.TemporaryDirectory();self.addCleanup(fixture_dir.cleanup)
        pdf=Path(fixture_dir.name)/'source.pdf';doc.save(str(pdf));doc.close();doc=pymupdf.open(pdf)
        self.addCleanup(doc.close)
        pages={}
        raw=[]
        for p in range(2):
            r=extract.read_page(doc,p);raw.append(r)
            lines=[l for b in r.text_blocks for l in b.lines]
            bbox=(min(l.bbox[0] for l in lines),min(l.bbox[1] for l in lines),max(l.bbox[2] for l in lines),max(l.bbox[3] for l in lines))
            pages[p]=[assemble.Element(kind='p',pno=p,runs=[['t',' '.join(l.stripped for l in lines)]],bbox=bbox,line_boxes=[l.bbox for l in lines])]
        book=assemble.Book(elements=[e for v in pages.values() for e in v],pages=pages,style=skeleton.BookStyle(body_size=12))
        result=pipeline.ReflowResult(book=book,raw_pages=raw,
            page_html={p:build_epub.page_fragment(book,p) for p in pages},fingerprint=extract.document_fingerprint(doc))
        stages=[]
        def local_stage(client,stage,prepared,request,ledger,cache,pno,records,should_stop):
            stages.append(stage)
            view=json.loads(request['messages'][1]['content'])
            if stage=='proposer':
                return dict(contract='layout-range-choices-1',snapshot=view['source_snapshot'],
                    groups=[dict(role='paragraph',ranges=[[0,len(view['atoms'])-1]])],
                    joins=[],incoming={'continue':False,'previous':None,'current':None,'hyphen':None},emphasis=[])
            if stage=='reviewer':
                from cps.services.reflow.layout_ranges import decision_count
                return dict(snapshot=view['snapshot'],accept=True,continuation_accept=False,
                    problems=[],decisions='1'*decision_count(view['decisions']))
            if stage=='lexical':
                self.assertEqual([(r['left_text'],r['right_text']) for r in view],[('anti-','dis-'),('dis-','establishment')])
                return dict(decisions=[dict(id=r['id'],decision='drop') for r in view])
            raise AssertionError(stage)
        with tempfile.TemporaryDirectory() as tmp, patch.object(layout_pipeline,'_stage',local_stage):
            client=layout_pipeline.LayoutClient('local-test-only',enabled=True)
            out=layout_pipeline.run_layout(doc,client=client,prepared_result=result,
                ledger=Ledger(str(Path(tmp)/'ledger'),cap_usd=1),cache=pipeline.PageCache(Path(tmp)/'cache'))
        self.assertIn('lexical',stages, out.structural)
        self.assertEqual({p.prepared.page for p in out.layout_plans},{1})
        self.assertEqual(out.page_html[0],out.source_pages[0].html)

class OrderingEvidence(unittest.TestCase):
    def test_ordering_context_describes_actual_range_order(self):
        from cps.services.reflow import _layout_atoms as atoms, layout_ops as ops, layout_ordering as ordering
        source=atoms.prepare('<p>First segment</p><figure><img src="f.jpg" /></figure><p>Second segment</p>','fixture',0)
        response=dict(snapshot=source['snapshot'],joins=[],groups=[
            dict(role='paragraph',ranges=[['a3','a4'],['a0','a1']]),
            dict(role='source',ranges=[['a2','a2']])])
        atoms.validate(source,response)
        prepared=ops.PreparedLayout(0,'pdf',source['snapshot'],'fixture',json.dumps(source),'{}',b'')
        plan=ops.LayoutPlan(prepared,json.dumps(response))
        row=ordering.candidates([plan])[0]
        self.assertEqual(row['groups'][0]['text_start'],'Second segment First segment')

    def test_real_factory_figure_location_reaches_ordering_evidence(self):
        from tests.unit.test_reflow_structural_ops import source as fixture
        from tests.unit.test_reflow_layout_ops import prepared, answer
        from cps.services.reflow import layout_ops as ops, layout_ordering as ordering
        with tempfile.TemporaryDirectory() as tmp:
            gen=fixture.__wrapped__(Path(tmp));sample=next(gen)
            try:
                book,doc,page,raw,value=prepared(sample)
                response=answer(value)
                contract=json.loads(value.contract_json)
                figure=next(b for b in contract['blocks'] if b['kind']=='figure')
                fg=next(g for g in response['groups'] if g['ranges']==[figure['range']])
                response['groups'].remove(fg);response['groups'].insert(0,fg)
                plan=value.accept(book,doc,response,source_page=page,raw_page=raw)
                row=ordering.candidates([plan])[0]
                self.assertTrue(book.figures[0]['bbox'])
                self.assertTrue(row['groups'][0]['source_rectangles'],row['groups'][0])
            finally:
                gen.close()

if __name__=='__main__':unittest.main()
