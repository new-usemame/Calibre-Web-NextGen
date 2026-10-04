"""Sparse source tables: aligned cells bounded by actual horizontal ruling.

Keep the original layout as pixels. Coordinate evidence does not assign cell
semantics or authorize a transcription, reordered words, or guessed headers.
"""
from statistics import median
from . import extract

VERSION='source-ruled-table-1'


def _rows(lines):
    rows=[]
    for line in sorted(lines,key=lambda l:(sum(l.bbox[1::2])/2,l.bbox[0])):
        center=(line.bbox[1]+line.bbox[3])/2
        row=next((r for r in reversed(rows) if abs(center-r[0])<=.4*min(line.size,r[2])),None)
        if row is None:rows.append([center,[line],line.size])
        else:row[1].append(line);row[2]=min(row[2],line.size)
    return rows


def regions(raw,kept,probe,candidates):
    if probe is None or not hasattr(probe,'rules') or not hasattr(probe,'ink_bounds'):return kept,candidates,[]
    if getattr(raw,'transcript_unverified',False):return kept,candidates,[]
    from .skeleton import Region,_lines_bbox
    lines=[line for _,group in kept for line in group if line.stripped and line.size>0]
    if not lines:return kept,candidates,[]
    rows=_rows(lines);eligible=[]
    for y,cells,size in rows:
        cells.sort(key=lambda l:l.bbox[0])
        if len(cells)<2 or cells[-1].bbox[2]-cells[0].bbox[0]<raw.width*.3:continue
        if any(b.bbox[0]-a.bbox[2]<size*.75 for a,b in zip(cells,cells[1:])):continue
        eligible.append((y,cells,size))
    groups=[]
    for row in eligible:
        last=groups[-1][-1] if groups else None
        shared=(sum(any(abs(a.bbox[0]-b.bbox[0])<=min(row[2],last[2])*.5 for b in last[1]) for a in row[1]) if last else 0)
        if last and row[0]-last[0]<=4*max(row[2],last[2]) and shared>=2:groups[-1].append(row)
        else:groups.append([row])
    claimed=set();added=[];remaining=list(candidates)
    for group in groups:
        if len(group)<2:continue
        cells=[line for _,row,_ in group for line in row];em=median(line.size for line in cells)
        box=_lines_bbox(cells,cells[0].bbox)
        left=max(0,min(line.bbox[0] for line in lines)-2*em)
        right=min(raw.width,max(line.bbox[2] for line in lines)+2*em)
        query=(left,max(0,box[1]-4*em),right,min(raw.height,box[3]+4*em))
        rules=probe.rules(query)
        upper=[y for y in rules if y<box[1]-.2*em]
        lower=[y for y in rules if y>box[3]+.2*em]
        if not upper or not lower:continue
        top,bottom=max(upper),min(lower)
        if box[1]-top>4*em or bottom-box[3]>4*em:continue
        # A rule crossing a source row cannot supply an outer boundary by
        # silently dropping its tail or treating that tail as unrelated prose.
        if any(box[1]<y<box[3] for y in rules):continue
        edges=[probe.ink_bounds((left,y-1,right,y+1)) for y in (top,bottom)]
        if not all(edges):continue
        x0=min(edge[0] for edge in edges);x1=max(edge[2] for edge in edges)
        if max(abs(edges[0][i]-edges[1][i]) for i in (0,2))>em:continue
        rect=(max(0,x0-2),max(0,top-2),min(raw.width,x1+2),min(raw.height,bottom+2))
        if any(not(rect[0]<=line.bbox[0] and line.bbox[2]<=rect[2]) for line in cells):continue
        inside=[line for line in lines if rect[0]<=line.bbox[0] and line.bbox[2]<=rect[2] and top<line.bbox[1] and line.bbox[3]<bottom]
        # A multiline cell can continue on a row whose other cells are empty.
        # Require every additional line to remain in an already measured
        # column, with no crossing into the next column's source territory.
        anchors=[]
        for line in sorted(cells,key=lambda line:line.bbox[0]):
            if not anchors or abs(line.bbox[0]-median(anchors[-1]))>em*.5:anchors.append([line.bbox[0]])
            else:anchors[-1].append(line.bbox[0])
        starts=[median(anchor) for anchor in anchors]
        def same_column(line):
            matches=[i for i,x in enumerate(starts) if abs(line.bbox[0]-x)<=em*.5]
            return len(matches)==1 and (matches[0]==len(starts)-1 or line.bbox[2]<starts[matches[0]+1]-.25*em)
        if any(not same_column(line) for line in inside):continue
        if not set(map(id,cells)).issubset(map(id,inside)):continue
        touching=[line for line in lines if line.bbox[0]<rect[2] and rect[0]<line.bbox[2] and line.bbox[1]<rect[3] and rect[1]<line.bbox[3]]
        if any(line not in inside for line in touching):continue
        kept_candidates=[];safe=True
        for candidate in remaining:
            b=candidate.bbox
            overlap=(min(b[2],rect[2])-max(b[0],rect[0]),min(b[3],rect[3])-max(b[1],rect[1]))
            if min(overlap)<=0:kept_candidates.append(candidate);continue
            if rect[0]<=b[0] and b[2]<=rect[2] and rect[1]<=b[1] and b[3]<=rect[3]:continue
            # A scan gap crop can start on the final table row before the next
            # diagram. Only a shallow complete-width overlap and all caption
            # evidence below the table authorize removing that table strip.
            if (overlap[0]>=.9*(b[2]-b[0]) and b[1]<rect[3]<b[3]
                    and overlap[1]<.25*(b[3]-b[1])
                    and all(line.bbox[1]>=rect[3] for line in candidate.caption_lines)):
                from dataclasses import replace
                kept_candidates.append(replace(candidate,bbox=(b[0],rect[3],b[2],b[3])))
            else:safe=False;break
        if not safe:continue
        remaining=kept_candidates;claimed.update(map(id,inside))
        # Retain the text-layer inventory in its original block order. The
        # printed original supplies the actual visual row/column associations.
        owned=set(map(id,inside))
        ordered=[line for line in lines if id(line) in owned]
        added.extend([Region(kind='artwork',lines=ordered,bbox=rect,reason='source_ruled_table'),
                      Region(kind='figure',bbox=rect,reason='source_ruled_table')])
    retained=[(block,[line for line in group if id(line) not in claimed]) for block,group in kept]
    return [(block,group) for block,group in retained if group],remaining,added
