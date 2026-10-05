# SPDX-License-Identifier: GPL-3.0-or-later
"""Bind a repeated native marginal row without repairing its damaged lettering."""
import re
from collections import defaultdict
from difflib import SequenceMatcher


def boxes(raw_pages, em):
    if not em:return {}
    rows=[]
    for raw in raw_pages:
        if raw.is_page_scan or raw.width>=raw.height*1.2:continue
        lines=[ln for blk in raw.text_blocks for ln in blk.lines if ln.stripped]
        heads=[ln for ln in lines if ln.bbox[3]<=raw.height*.12
               and 24<=sum(c.isalpha() for c in ln.stripped)<=90
               and len(ln.stripped.split())>=4 and ln.size<=em*1.2
               and not re.match(r'^(?:fig(?:ure|\.)?|table|chart|plate|map|diagram)\b',ln.stripped,re.I)]
        for head in heads:
            a=head.bbox
            slots=[ln for ln in lines if ln is not head
                   and re.fullmatch(r'[0-9IiLlOo\s]{1,7}',ln.stripped)
                   and ln.size<=em*1.3 and ln.bbox[2]-ln.bbox[0]<=raw.width*.075
                   and min(ln.bbox[3],a[3])>max(ln.bbox[1],a[1])
                   and (ln.bbox[0]>a[2]+em*.4 and ln.bbox[0]>=raw.width*.8
                        or ln.bbox[2]<a[0]-em*.4 and ln.bbox[2]<=raw.width*.2)]
            if len(slots)!=1:continue
            slot=slots[0];bottom=max(a[3],slot.bbox[3]);top=min(a[1],slot.bbox[1])
            rest=[ln for ln in lines if ln is not head and ln is not slot]
            if (not rest or any(ln.bbox[1]<top for ln in rest)
                    or min(ln.bbox[1] for ln in rest)-bottom<em*.8):continue
            following=min(rest,key=lambda ln:(ln.bbox[1],ln.bbox[0]))
            # A row of table cells or a second display title does not establish
            # a detached prose boundary below a putative marginal header.
            if (len(following.stripped.split())<8 or following.bbox[2]-following.bbox[0]<raw.width*.5
                    or not em*.9<=following.size<=em*1.05):continue
            number=int(slot.stripped) if re.fullmatch(r'\d{1,5}',slot.stripped) else None
            rows.append((raw,head,slot,number,'right' if slot.bbox[0]>a[2] else 'left'))
    def letters(ln):return re.sub(r'[^a-z]','',ln.stripped.casefold())
    def compatible(a,b):
        raw,head,slot,_,side=a;other,peer,folio,_,edge=b
        return (side==edge and abs(other.pno-raw.pno)<=4
                and abs(head.bbox[1]/raw.height-peer.bbox[1]/other.height)<=.012
                and abs(head.bbox[0]/raw.width-peer.bbox[0]/other.width)<=.025
                and abs((head.bbox[2]-head.bbox[0])/raw.width-(peer.bbox[2]-peer.bbox[0])/other.width)<=.02
                and abs(head.size-peer.size)<=em*.1
                and abs(slot.bbox[0]/raw.width-folio.bbox[0]/other.width)<=.015
                and abs(slot.bbox[1]/raw.height-folio.bbox[1]/other.height)<=.012
                and SequenceMatcher(None,letters(head),letters(peer),autojunk=False).ratio()>=.92)
    proved=defaultdict(list)
    by_page=defaultdict(list)
    for row in rows:by_page[row[0].pno].append(row)
    for first in rows:
        pno=first[0].pno
        for second in by_page.get(pno+2,[]):
            for third in by_page.get(pno+4,[]):
                triple=(first,second,third)
                if not all(compatible(a,b) for i,a in enumerate(triple) for b in triple[i+1:]):continue
                readable=[row for row in triple if row[3] is not None]
                # Unreadable third slot corroborates geometry only. No character
                # is converted to a digit, and no printed folio is inferred.
                if len(readable)<2 or len({row[3]-row[0].pno for row in readable})!=1:continue
                for raw,head,slot,_,_ in triple:
                    for box in (head.bbox,slot.bbox):
                        if box not in proved[raw.pno]:proved[raw.pno].append(box)
    return dict(proved)
