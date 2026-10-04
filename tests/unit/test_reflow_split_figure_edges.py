"""Padding a split source band must not pull in its neighboring artwork."""
import pymupdf,pytest
from cps.services.reflow import extract,skeleton
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('label_above',[False,True])
def test_two_independently_captioned_crops_keep_their_own_source_ink(label_above):
    doc=pymupdf.open();page=doc.new_page(width=300,height=400)
    page.draw_rect((30,70,110,220),fill=(0,0,1),color=None)
    page.draw_rect((125,70,205,220),fill=(1,0,0),color=None)
    page.insert_text((20,45),'RUNNING HEAD ACROSS THE CHARTS',fontsize=12,color=(.5,.5,.5))
    def line(text,box):return extract.Line([extract.Span(text,12,'Helvetica',0,box)],box)
    lines=[line('Figure 1. First chart',(30,280,110,288)),
           line('Figure 2. Second chart',(125,280,205,288)),
           line('Ordinary source prose resumes below the two figures.',(20,290,280,305))]
    if label_above:
        page.insert_text((40,60),'A',fontsize=10,color=(0,1,0))
        page.insert_text((60,60),'B',fontsize=10,color=(0,1,0))
        label=line('A',(40,53,50,63));label.spans[0].size=36;lines.insert(0,label)
        label=line('B',(60,53,70,63));label.spans[0].size=36;lines.insert(0,label)
    block=extract.Block(0,(20,280,280,305),lines)
    raw=extract.RawPage(0,300,400,[block],images=[extract.Image((0,0,300,400),1,True)])
    probe=extract.ScanPixelProbe(doc,0,mask=[(20,30,280,50)]+[l.bbox for l in lines])
    # A measured empty-channel centre can be near one art edge. The real
    # corpus has this shape because sparse labels affect the masked probe.
    probe.channels=lambda rect:[123]
    figures=skeleton._scan_figures([(block,lines)],raw,skeleton.BookStyle(12),probe)
    assert len(figures)==2
    first,second=sorted(figures,key=lambda f:f.bbox[0])
    a=page.get_pixmap(clip=pymupdf.Rect(first.bbox),alpha=False)
    b=page.get_pixmap(clip=pymupdf.Rect(second.bbox),alpha=False)
    colors=lambda pix:set(zip(pix.samples[0::3],pix.samples[1::3],pix.samples[2::3]))
    assert (0,0,255) in colors(a) and (255,0,0) not in colors(a),'the first crop must not contain a sliver of the second source figure'
    assert (255,0,0) in colors(b) and (0,0,255) not in colors(b)
    assert not any(r==g==blue and r<200 for r,g,blue in colors(a)|colors(b)),'recognized running furniture must not appear as a clipped chart fragment'
    if label_above:assert any(g>r and g>blue for r,g,blue in colors(a)),'a retained source label above the chart must remain inside its own image'
    doc.close()
