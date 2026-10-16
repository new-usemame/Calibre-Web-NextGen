"""Public-safe constructed native note identity boundaries through the parser.

The reliable note-opening control, native Times metrics/frame and printed divider are
adapted unchanged from test_reflow_reading_flow. Only a second separately raised
marker and its continuation are added to exercise uncertain-marker identity.
Pixel authority proves a territory, never the identity of an uncertain marker.
"""
from collections import Counter
from dataclasses import asdict
import copy
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
import pytest
from cps.services.reflow import extract,skeleton
from tests.unit.test_reflow_reading_flow import opaque_page_fixture

pytestmark=pytest.mark.unit

class PrintedDividerProbe:
 def ink_bounds(self,box):return None
 def source_ink_bounds(self,box):return (60,136,130,138)

def inputs(flag=None):
 raw=opaque_page_fixture()
 assert raw.source_geometry=={} and not raw.is_page_scan
 assert (raw.pno,raw.width,raw.height)==(0,420,600)
 first=raw.blocks[3].lines[0];continuation=raw.blocks[4].lines[0]
 assert first.spans[0].text=='109'and first.spans[0].bbox==(64,148,74,154)
 assert first.spans[1].bbox==(78,149,360,160)
 if flag:
  marker=extract.Span('169',5,'Times',0,(64,190,74,196))
  setattr(marker,flag,True)
  second=extract.Line([marker,extract.Span(' Another independently printed note.',9,'Times',0,(78,191,360,202))],(64,190,360,202))
  tail=extract.Line([extract.Span('This second note continues in its own original source lane.',9,'Times',0,(60,206,360,217))],(60,206,360,217))
  raw.blocks += [extract.Block(5,second.bbox,[second]),extract.Block(6,tail.bbox,[tail])]
  return raw,[first,continuation],[second,tail]
 return raw,[first,continuation],[]

def split(raw):
 before=asdict(raw)
 body,notes=skeleton._split_off_notes(raw,skeleton.BookStyle(body_size=11),skeleton.PageSkeleton(0,420,600),PrintedDividerProbe())
 assert asdict(raw)==before
 key=lambda row:hashlib.sha256(json.dumps(asdict(row),sort_keys=True,separators=(',',':')).encode()).hexdigest()
 assert Counter(key(l)for b in raw.text_blocks for l in b.lines)==Counter(key(l)for b in body for l in b.lines)+Counter(key(l)for n in notes for l in n.lines)
 return body,notes

def test_reliable_native_109_and_unnumbered_continuation_remain_one_complete_note():
 raw,first,_=inputs();body,notes=split(raw)
 assert len(notes)==1 and notes[0].number==109 and notes[0].lines==first,[(n.number,n.text)for n in notes]
 assert 'cotton-folding and'in' '.join(b.text for b in body)

@pytest.mark.parametrize('flag',['uncertain','transcription_uncertain','encoding_unresolved'])
def test_distinct_raised_uncertain_native_marker_never_inherits_previous_note_identity(flag):
 raw,first,second=inputs(flag);body,notes=split(raw)
 assert getattr(second[0].spans[0],flag)is True
 assert len(notes)==2,[(n.number,n.text)for n in notes]
 assert notes[0].number==109 and notes[0].lines==first,[(n.number,n.text)for n in notes]
 assert notes[1].number is None and notes[1].lines==second,[(n.number,n.text)for n in notes]
 assert not any(l is row for b in body for l in b.lines for row in first+second)
 # Every original uncertainty flag, literal and raised-marker bbox is retained.
 assert second[0].spans[0].text=='169'and second[0].spans[0].bbox==(64,190,74,196)
