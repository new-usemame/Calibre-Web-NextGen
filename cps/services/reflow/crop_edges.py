"""Detect detached, broad scan bars; retain the original pixels between trims.

A thin table rule or lettering does not qualify. Full source pages remain
untouched; this projection is only for reading crops with a white gutter.
"""
import io


SOURCE_EDGE_VERSION = 'source-scan-edge-1'


def source_scan_edges(data, page_bbox):
    """Prove a vertical scan edge from the complete source frame.

    The solid core must reach both physical ends of the scan. A printed rule
    inside a figure cannot satisfy that proof. Interior candidates additionally
    require a landscape spread and its central gutter; outer candidates occupy
    the page margin. Each row retains the measured silhouette, rather than
    treating the entire gutter rectangle as disposable pixels.
    """
    from PIL import Image
    gray = Image.open(io.BytesIO(data)).convert('L')
    w, h = gray.size
    if min(w, h) < 120 or sum(gray.histogram()[245:]) < w*h*.45:
        return []
    dark = gray.point(lambda value: 255 if value < 60 else 0)
    counts = [dark.crop((x, 0, x+1, h)).histogram()[255]/h
              for x in range(w)]
    runs, start = [], None
    for x, value in enumerate(counts + [0]):
        if value >= .85 and start is None:
            start = x
        elif value < .85 and start is not None:
            runs.append((start, x)); start = None
    edges = []
    for left, right in runs:
        if not max(3, w*.002) <= right-left <= w*.12:
            continue
        centre = (left+right)/2
        position = ('margin' if centre <= w*.12 or centre >= w*.88 else
                    'gutter' if w >= h*1.2 and w*.40 <= centre <= w*.60 else None)
        if position is None:
            continue
        end = max(2, int(h*.01))
        if any(dark.crop((left, y0, right, y1)).histogram()[255] <
               (right-left)*(y1-y0)*.60 for y0, y1 in ((0, end), (h-end, h))):
            continue
        # A margin carrier needs a detached paper channel. A physical binding
        # gutter may touch already damaged lettering; its source-end anchors
        # and varying silhouette prove the carrier, while glyph protection
        # below preserves every recorded overlap.
        lo, hi = left, right
        while lo and counts[lo-1] >= .08:
            lo -= 1
        while hi < w and counts[hi] >= .08:
            hi += 1
        if hi-lo > w*.12:
            continue
        sides = [('left', lo), ('right', hi)] if position == 'gutter' else [
            ('right', hi) if centre < w/2 else ('left', lo)]
        if position == 'margin' and any(not any(all(counts[q] <= .01 for q in range(a, a+2))
                       for a in range(max(0, bound-int(w*.025)), min(w-1, bound+int(w*.025))))
               for _, bound in sides):
            continue
        pixels = gray.tobytes()
        seed = (left+right)//2
        profile = []
        for y in range(h):
            row = pixels[y*w:(y+1)*w]
            present = [x for x in range(lo, hi) if row[x] < 90]
            if len(present) < max(3, (right-left)*.25):
                profile.append(None); continue
            at = min(present, key=lambda x: abs(x-seed))
            a, b = at, at+1
            # The core proves the carrier, while the envelope bounds growth.
            # Connected lettering extending beyond that envelope is retained.
            while a > lo and row[a-1] < 200:
                a -= 1
            while b < hi and row[b] < 200:
                b += 1
            profile.append([a, b])
        widths = [r[1]-r[0] for r in profile if r]
        # A straight black panel or printed rule is not a binding silhouette.
        # Margin bars remain handled by the established detached-bar proof.
        if position == 'gutter' and (not widths or
                max(widths)-min(widths) < max(3, (right-left)*.15)):
            continue
        edges.append(dict(version=SOURCE_EDGE_VERSION, axis='x', position=position,
                          core=[left, right], envelope=[lo, hi], profile=profile,
                          size=[w, h], page_bbox=list(page_bbox)))
    return edges


def trim_source_edges(data, crop_bbox, edges, protected_boxes=()):
    """Trim a source-proven edge, refusing every cut through a source glyph.

    A crop containing only the measured scan carrier may be omitted. That
    requires no protected glyph and no other dark source pixels; ordinary art,
    including thin figure/table borders, remains an image.
    """
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    gray = image.convert('L')
    cw, ch = image.size
    x0, y0, x1, y1 = map(float, crop_bbox)
    if x1 <= x0 or y1 <= y0:
        return data, [], False
    retained = [0, 0, cw, ch]
    audits = []
    cleared = []
    active = [b for b in protected_boxes if b[0] < x1 and b[2] > x0
              and b[1] < y1 and b[3] > y0]
    for edge in edges:
        px0, py0, px1, py1 = edge['page_bbox']
        fw, fh = edge['size']
        sy0 = max(0, int((y0-py0)/(py1-py0)*fh))
        sy1 = min(fh, int((y1-py0)/(py1-py0)*fh)+1)
        rows = [r for r in edge['profile'][sy0:sy1] if r]
        if not rows:
            continue
        a = px0+min(r[0] for r in rows)/fw*(px1-px0)
        b = px0+max(r[1] for r in rows)/fw*(px1-px0)
        if b <= x0 or a >= x1:
            continue
        common = dict(version=SOURCE_EDGE_VERSION, axis='x',
                      position=edge['position'], source_band=[a, b],
                      source_core=edge['core'], source_frame=edge['page_bbox'],
                      crop_bbox=list(crop_bbox), size=[cw, ch])
        # Check every pixel outside the independently measured carrier before
        # dropping a standalone candidate, including faint artwork at <230.
        if not active:
            outside = gray.copy()
            from PIL import ImageDraw
            draw = ImageDraw.Draw(outside)
            for y in range(ch):
                sy = max(0, min(fh-1, int((y0+(y+.5)/ch*(y1-y0)-py0)/(py1-py0)*fh)))
                row = edge['profile'][sy]
                if row:
                    l = int((px0+row[0]/fw*(px1-px0)-x0)/(x1-x0)*cw)-2
                    r = int((px0+row[1]/fw*(px1-px0)-x0)/(x1-x0)*cw)+2
                    draw.line((max(0,l),y,min(cw-1,r),y),fill=255)
            if outside.point(lambda v: 255 if v < 230 else 0).getbbox() is None:
                return data, [dict(common, action='drop_scan_carrier')], True
        needs_clear = False
        if a > x0 and b >= x1:
            # Round toward the retained paper; the source profile already
            # includes antialiased pixels, and glyph boxes veto the cut.
            cut = max(0, int((a-x0)/(x1-x0)*cw))
            boundary = x0+cut/cw*(x1-x0)
            if any(box[2] > boundary for box in active):
                needs_clear = True
            else:
                retained[2] = min(retained[2], cut)
                audits.append(dict(common, action='trim', side='high', cut=cut))
        elif a <= x0 and b < x1:
            cut = min(cw, int((b-x0)/(x1-x0)*cw)+2)
            boundary = x0+cut/cw*(x1-x0)
            if any(box[0] < boundary for box in active):
                needs_clear = True
            else:
                retained[0] = max(retained[0], cut)
                audits.append(dict(common, action='trim', side='low', cut=cut))
        if needs_clear or x0 < a < b < x1 or (active and a <= x0 and b >= x1):
            # Preserve both sides, and every glyph rectangle in the carrier.
            # Only unprotected pixels in its measured silhouette become paper.
            footprint = []
            overlap = []
            protected_pixels = 0
            for y in range(ch):
                top = y0+y/ch*(y1-y0)
                bottom = y0+(y+1)/ch*(y1-y0)
                sy = max(0,min(fh-1,int(((top+bottom)/2-py0)/(py1-py0)*fh)))
                row = edge['profile'][sy]
                if not row:
                    continue
                l = max(0,int((px0+row[0]/fw*(px1-px0)-x0)/(x1-x0)*cw)-1)
                r = min(cw,int((px0+row[1]/fw*(px1-px0)-x0)/(x1-x0)*cw)+2)
                intervals = [(l,r)]
                for box in active:
                    if box[1] >= bottom or box[3] <= top:
                        continue
                    # Outward rounding protects every pixel touched by the
                    # literal source box; no damaged glyph is reconstructed.
                    import math
                    pl = max(0,math.floor((box[0]-x0)/(x1-x0)*cw))
                    pr = min(cw,math.ceil((box[2]-x0)/(x1-x0)*cw))
                    safe = []
                    for il,ir in intervals:
                        if pl >= ir or pr <= il:
                            safe.append((il,ir)); continue
                        protected_pixels += min(pr,ir)-max(pl,il)
                        overlap.append(y)
                        if il < pl:safe.append((il,pl))
                        if pr < ir:safe.append((pr,ir))
                    intervals = safe
                footprint.extend((y,il,ir) for il,ir in intervals if ir>il)
            if overlap:
                audits.append(dict(common,action='refused_glyph_overlap',
                                   overlapping_pixel_rows=len(set(overlap)),
                                   protected_overlap_pixels=protected_pixels))
            cleared.extend(footprint)
            if footprint:
                audits.append(dict(common,action='clear_scan_carrier',
                                   cleared_pixel_rows=len({y for y,_,_ in footprint}),
                                   cleared_pixels=sum(r-l for _,l,r in footprint),
                                   protected_overlap_pixels=protected_pixels))
    if (retained == [0,0,cw,ch] and not cleared) or retained[2] <= retained[0]:
        return data, audits, False
    if cleared:
        from PIL import ImageDraw
        image = image.convert('RGB')
        draw = ImageDraw.Draw(image)
        for y,left,right in cleared:
            draw.line((left,y,right-1,y),fill='white')
    output = io.BytesIO()
    image.crop(retained).save(output,format='PNG')
    return output.getvalue(), [dict(a,retained_pixel_bbox=retained) for a in audits], False


def scan_borders(image):
    """Return solid border bands separated from content by a white gutter."""
    gray = image.convert('L')
    width, height = gray.size
    if min(width, height) < 120:
        return []
    # A dark printed cover/photo border is content, not a scan-edge defect.
    # Require the white-paper carrier typical of the damaged scan crops.
    histogram = gray.histogram()
    if sum(histogram[245:]) < width*height*.45:
        return []
    result = []
    for axis, rotated in [('x', gray), ('y', gray.transpose(2))]:
        w, h = rotated.size
        mask = rotated.point(lambda value: 255 if value < 60 else 0)
        dark = [mask.crop((x, 0, x+1, h)).histogram()[255] / h for x in range(w)]
        runs = []
        start = None
        for x, value in enumerate(dark + [0]):
            if value >= .80 and start is None:
                start = x
            elif value < .80 and start is not None:
                runs.append((start, x)); start = None
        for left, right in runs:
            # Four-pixel separators in enlarged note/table crops are printed
            # source rules. A detached scan bar must have substantial depth.
            if right-left < max(8, w*.01) or right-left > w*.18:
                continue
            # A second broad solid region in the interior is source content
            # (for example a neighboring printed row or table fill), not a
            # detached margin defect. Refuse trimming this ambiguous carrier.
            if any(a < w*.75 and b > w*.25 and b-a >= 8
                   and (a, b) != (left, right) for a, b in runs):
                continue
            while left > 0 and dark[left-1] > .02:
                left -= 1
            while right < w and dark[right] > .02:
                right += 1
            side = 'low' if right <= w*.25 else 'high' if left >= w*.75 else None
            if side is None:
                continue
            # There must be blank paper toward the content, and no competing
            # ink territory on the outside. This refuses dark artwork edges.
            interior = range(right, min(w, right+int(w*.12))) if side == 'low' else range(left-1, max(-1, left-int(w*.12)), -1)
            gutter = []
            for x in interior:
                if dark[x] <= .004:
                    gutter.append(x)
                    if len(gutter) >= max(4, int(w*.01)):
                        break
                else:
                    gutter = []
            outside = dark[:left] if side == 'low' else dark[right:]
            if len(gutter) < max(4, int(w*.01)) or any(v > .15 for v in outside):
                continue
            cut = max(gutter)+1 if side == 'low' else min(gutter)
            result.append(dict(axis=axis, side=side, band=[left, right], cut=cut,
                               solid_fraction=.80, size=[width, height]))
    return result


def trim_scan_borders(data):
    """Lossless retained rectangle, or the unchanged input when no proof exists."""
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    bands = scan_borders(image)
    if not bands:
        return data, []
    box = [0, 0, image.width, image.height]
    for band in bands:
        axis = 0 if band['axis'] == 'x' else 1
        if band['side'] == 'low':
            box[axis] = max(box[axis], band['cut'])
        else:
            box[axis+2] = min(box[axis+2], band['cut'])
    if box[2] <= box[0] or box[3] <= box[1]:
        return data, []
    output = io.BytesIO()
    image.crop(box).save(output, format='PNG')
    return output.getvalue(), [dict(b, retained_pixel_bbox=box) for b in bands]
