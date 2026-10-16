"""Independent local pixel evidence for line-loss guards, never a decider model."""
import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path


def words(display,cache):
    pix=display.pixmap(scale=3,max_pixels=8_000_000)
    image=pix.tobytes('png');identity=hashlib.sha256(image).hexdigest()
    root=Path(cache);root.mkdir(parents=True,exist_ok=True)
    target=root/('literal-tsv-v2-'+('split-landscape-v1-' if pix.width>=pix.height*1.2 else '')+identity+'.json')
    with (root/(identity+'.lock')).open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        if target.exists():return json.loads(target.read_text())
        return _recognize(display,pix,image,target)


def _recognize(display,pix,image,target):
    engine=shutil.which('tesseract')
    if not engine:raise RuntimeError('Line guard requires independent local pixel recognition')
    # stdin passes image bytes directly without temporary files or path metadata.
    # A broad scan can carry two leaves. Recognizing each half prevents the
    # layout detector from silently dropping one leaf. This is source geometry,
    # independent of the model reading and of any book/page identity.
    tiles=[(image,0)]
    if pix.width>=pix.height*1.2:
        from PIL import Image
        picture=Image.open(io.BytesIO(image));mid=pix.width//2;tiles=[]
        for left,right in ((0,mid),(mid,pix.width)):
            stream=io.BytesIO();picture.crop((left,0,right,pix.height)).save(stream,format='PNG')
            tiles.append((stream.getvalue(),left))
    output=[];sx=display.rect.width/pix.width;sy=display.rect.height/pix.height
    for tile,offset in tiles:
        proc=subprocess.run([engine,'stdin','stdout','-l','eng','--psm','3','tsv'],
                            input=tile,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        if proc.returncode:raise RuntimeError('Independent pixel recognition failed')
        # Printed quotation marks are literal OCR text, not CSV syntax. The old
        # parser could absorb following TSV rows into one quoted "word".
        for row in csv.DictReader(io.StringIO(proc.stdout.decode('utf-8')),delimiter='\t',quoting=csv.QUOTE_NONE):
            if row['level']!='5' or not row['text'].strip():continue
            x,y,w,h=[int(row[k]) for k in ('left','top','width','height')];x+=offset
            output.append({'text':row['text'],'bbox':[x*sx,y*sy,(x+w)*sx,(y+h)*sy]})
    temp=target.with_suffix('.tmp');temp.write_text(json.dumps(output,ensure_ascii=False));os.replace(temp,target)
    return output


def ink_bounds(display, box):
    """Bound original ink inside a source-proven text-free territory.

    The caller establishes the territory from printed prose/caption rows. This
    only measures pixels; it never paints over prose or guesses a text role.
    """
    from PIL import Image
    pix=display.pixmap(box,scale=3,max_pixels=2_000_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    histogram=picture.histogram();total=picture.width*picture.height;count=0;paper=255
    for level,n in enumerate(histogram):
        count+=n
        if count>=total*.9:paper=level;break
    mask=picture.point(lambda value:255 if value<min(230,paper-25) else 0)
    bound=mask.getbbox()
    if bound is None:return None
    sx=(box[2]-box[0])/picture.width;sy=(box[3]-box[1])/picture.height
    return [max(box[0],box[0]+bound[0]*sx-.5),max(box[1],box[1]+bound[1]*sy-.5),
            min(box[2],box[0]+bound[2]*sx+.5),min(box[3],box[1]+bound[3]*sy+.5)]


def figure_ink_bounds(display, box, seed, prose_rows=()):
    """Keep the original artwork components owned by its source hint.

    Prose already bounds ``box``. A distant detached mark in that gap does not
    own the illustration. Connected artwork can extend beyond an undersized
    hint; nearby detached lettering is retained without altering any pixels.
    """
    from PIL import Image
    import math
    pix=display.pixmap(box,scale=3,max_pixels=2_000_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    histogram=picture.histogram();count=0;paper=255
    for level,n in enumerate(histogram):
        count+=n
        if count>=picture.width*picture.height*.9:paper=level;break
    data=bytearray(picture.point(lambda value:255 if value<min(230,paper-25) else 0).tobytes())
    width,height=picture.size;sx=(box[2]-box[0])/width;sy=(box[3]-box[1])/height
    components=[]
    for start in range(len(data)):
        if not data[start]:continue
        pending=[start];data[start]=0;x0=width;y0=height;x1=y1=-1
        while pending:
            point=pending.pop();y,x=divmod(point,width)
            x0=min(x0,x);x1=max(x1,x);y0=min(y0,y);y1=max(y1,y)
            neighbours=[]
            if x:neighbours.append(point-1)
            if x+1<width:neighbours.append(point+1)
            if y:neighbours.append(point-width)
            if y+1<height:neighbours.append(point+width)
            for other in neighbours:
                if data[other]:data[other]=0;pending.append(other)
        components.append([box[0]+x0*sx,box[1]+y0*sy,
                           box[0]+(x1+1)*sx,box[1]+(y1+1)*sy])
    # Source-bound text components do not belong to the artwork merely because
    # a coarse hint touches them. This classifies original components; it does
    # not modify the source image. A component crossing a prose row remains in
    # the evidence, so the caller must reject an inseparable object.
    components=[c for c in components if not any(
        row[0]-1<=c[0] and row[1]-1<=c[1] and
        c[2]<=row[2]+1 and c[3]<=row[3]+1 for row in prose_rows)]
    if not components:return None
    def gap(a,b):
        return math.hypot(max(a[0]-b[2],b[0]-a[2],0),
                          max(a[1]-b[3],b[1]-a[3],0))
    owned=[c for c in components if gap(c,seed)==0]
    if not owned:
        # A hint wholly inside an unfilled outline still owns its nearest
        # original component; this does not truncate that connected outline.
        nearest=min(gap(c,seed) for c in components)
        owned=[c for c in components if abs(gap(c,seed)-nearest)<min(sx,sy)]
    # Detached lettering beside a wide figure needs a wider horizontal gap
    # than ink above a small icon. Bind to the original owned components only:
    # a chain of detached flecks cannot grant successively more territory.
    anchors=list(owned)
    horizontal=max(3.,(seed[2]-seed[0])*.5)
    vertical=max(3.,(seed[3]-seed[1])*.25)
    owned.extend(c for c in components if c not in anchors and any(
        max(a[0]-c[2],c[0]-a[2],0)<=horizontal and
        max(a[1]-c[3],c[1]-a[3],0)<=vertical for a in anchors))
    return [max(box[0],min(c[0] for c in owned)-.5),
            max(box[1],min(c[1] for c in owned)-.5),
            min(box[2],max(c[2] for c in owned)+.5),
            min(box[3],max(c[3] for c in owned)+.5)]


def table_rules(display, box):
    """Witness a ruled data object, rather than a table-role text rectangle."""
    from PIL import Image
    import itertools
    pix=display.pixmap(box,scale=3,max_pixels=500_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    mask=picture.point(lambda value:255 if value<180 else 0)
    width,height=mask.size;data=mask.tobytes()
    def long_run(values,extent):
        return any(value and sum(1 for _ in group)>=extent*.4
                   for value,group in itertools.groupby(values))
    horizontal=[y for y in range(height) if long_run(data[y*width:(y+1)*width],width)]
    if not horizontal:return False
    vertical=any(long_run((data[y*width+x] for y in range(height)),height)
                 for x in range(width))
    return vertical or max(horizontal)-min(horizontal)>max(3,height*.1)


def header_separating_rule(display, box):
    """Prove a thin long original rule immediately below a marginal text row.

    This is an ink witness, not a text-role classifier. Broad scan frames are
    measured per leaf; thick borders and short word underlines prove nothing.
    """
    r=display.rect
    height=box[3]-box[1]
    if height<=0:return None
    left,right=0.,r.width
    if r.width>=r.height*1.2:
        if (box[0]+box[2])/2<r.width/2:right=r.width/2
        else:left=r.width/2
    width=right-left
    top=box[3]+.5
    bottom=min(r.height*.16,box[3]+height*3)
    if bottom<=top:return None
    area=[left,top,right,bottom]
    key=tuple(area)
    cache=getattr(display,'_vision_header_rules',None)
    if cache is None:cache={};display._vision_header_rules=cache
    if key in cache:return cache[key]
    from PIL import Image
    import itertools
    pix=display.pixmap(area,scale=3,max_pixels=500_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    histogram=picture.histogram();total=picture.width*picture.height;count=0;paper=255
    for level,n in enumerate(histogram):
        count+=n
        if count>=total*.9:paper=level;break
    mask=picture.point(lambda value:255 if value<min(230,paper-25) else 0)
    data=mask.tobytes();sx=(area[2]-area[0])/picture.width;sy=(bottom-top)/picture.height
    rows=[];cores={}
    for y in range(picture.height):
        row=data[y*picture.width:(y+1)*picture.width]
        offset=0;longest=0;core=None
        for value,group in itertools.groupby(row):
            length=sum(1 for _ in group)
            if value and length>longest:longest=length;core=offset+length//2
            offset+=length
        if longest*sx>=width*.4:rows.append(y);cores[y]=core
    groups=[]
    for y in rows:
        if groups and y==groups[-1][-1]+1:groups[-1].append(y)
        else:groups.append([y])
    result=None;visited=set();thin=max(1.5,height*.25)
    for group in groups:
        if group[0]==0 or group[-1]==picture.height-1 or len(group)*sy>thin:continue
        seed=group[len(group)//2]*picture.width+cores[group[len(group)//2]]
        if seed in visited:continue
        pending=[seed];visited.add(seed);x0=picture.width;x1=-1;y0=picture.height;y1=-1;columns={}
        while pending:
            point=pending.pop();y,x=divmod(point,picture.width)
            x0=min(x0,x);x1=max(x1,x);y0=min(y0,y);y1=max(y1,y)
            low,high=columns.get(x,(y,y));columns[x]=(min(low,y),max(high,y))
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)):
                nx,ny=x+dx,y+dy
                if 0<=nx<picture.width and 0<=ny<picture.height:
                    neighbor=ny*picture.width+nx
                    if data[neighbor] and neighbor not in visited:
                        visited.add(neighbor);pending.append(neighbor)
        # Thickness is the local vertical stroke, not the bounding height of
        # a slightly uneven scanned rule. Every connected column is checked,
        # so a thick border or vertical branch still vetoes the whole witness.
        if y0==0 or y1==picture.height-1:continue
        if any((high-low+1)*sy>thin for low,high in columns.values()):continue
        # A long horizontal core alone cannot authorize an oblique appendage.
        if (y1-y0+1)*sy>max(thin,(x1-x0+1)*sx*.025):continue
        result=[area[0]+x0*sx,top+y0*sy,area[0]+(x1+1)*sx,top+(y1+1)*sy]
        break
    cache[key]=result
    return result


def page_gutter_bounds(display):
    """Measure source-supported gutter bounds without interpreting text content."""
    cached=getattr(display,'_vision_page_gutters',None)
    if cached is not None:return cached
    from PIL import Image
    pix=display.pixmap(scale=1,max_pixels=1_000_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    mask=picture.point(lambda value:255 if value<180 else 0)
    width,height=mask.size;r=display.rect
    edges=[0,width]
    if r.width>=r.height*1.2:edges.append(width/2)
    cores=[x for x in range(width)
           if min(abs(x-edge) for edge in edges)<width*.08
           and mask.crop((x,0,x+1,height)).histogram()[255]>=height*.8]
    visited=set();bounds=[];pixels=mask.load()
    for x in cores:
        for y in range(height):
            if not pixels[x,y] or (x,y) in visited:continue
            stack=[(x,y)];visited.add((x,y));left=right=x;top=bottom=y
            while stack:
                a,b=stack.pop();left=min(left,a);right=max(right,a);top=min(top,b);bottom=max(bottom,b)
                for c,d in ((a-1,b),(a+1,b),(a,b-1),(a,b+1)):
                    if 0<=c<width and 0<=d<height and pixels[c,d] and (c,d) not in visited:
                        visited.add((c,d));stack.append((c,d))
            if bottom-top>=height*.8 and right-left<width*.12:
                bounds.append([max(0,(left-1)*r.width/width),max(0,(top-1)*r.height/height),
                    min(r.width,(right+2)*r.width/width),min(r.height,(bottom+2)*r.height/height)])
    display._vision_page_gutters=bounds
    return bounds


def page_spine_fragments(display, protected=()):
    """Detached aligned edge ink supported by several original-page segments.

    This is separate from the dense-strip witness: antialiased, interrupted
    binding marks need broad page context, narrow connected components and
    original illustration ownership. A single line or a branch connected to
    artwork cannot authorize a crop boundary. Pixels are never modified.
    """
    protected=tuple(protected)
    cached=getattr(display,'_vision_spine_fragments',None)
    if cached is None:
        from PIL import Image
        pix=display.pixmap(scale=1,max_pixels=1_000_000)
        picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
        width,height=picture.size;r=display.rect
        histogram=picture.histogram();count=0;paper=255
        for level,n in enumerate(histogram):
            count+=n
            if count>=width*height*.9:paper=level;break
        mask=picture.point(lambda value:255 if value<min(230,paper-25) else 0)
        pixels=mask.load();edges=[0,width]
        if r.width>=r.height*1.2:edges.append(width/2)
        visited=set();parts=[]
        for x in range(width):
            if min(abs(x-edge) for edge in edges)>=width*.04:continue
            for y in range(height):
                if not pixels[x,y] or (x,y) in visited:continue
                stack=[(x,y)];visited.add((x,y));left=right=x;top=bottom=y
                while stack:
                    a,b=stack.pop();left=min(left,a);right=max(right,a);top=min(top,b);bottom=max(bottom,b)
                    for c,d in ((a-1,b),(a+1,b),(a,b-1),(a,b+1)):
                        if 0<=c<width and 0<=d<height and pixels[c,d] and (c,d) not in visited:
                            visited.add((c,d));stack.append((c,d))
                if right-left+1<=max(5,width*.012) and bottom-top+1>=height*.025:
                    parts.append([left,top,right+1,bottom+1])
        groups=[]
        for part in sorted(parts,key=lambda b:b[0]):
            if not groups or part[0]-max(b[2] for b in groups[-1])>max(2,width*.005):groups.append([])
            groups[-1].append(part)
        cached=[]
        for group in groups:
            if len(group)<2:continue
            left=min(b[0] for b in group);right=max(b[2] for b in group)
            top=min(b[1] for b in group);bottom=max(b[3] for b in group)
            intervals=[]
            for a,b in sorted((b[1],b[3]) for b in group):
                if intervals and a<=intervals[-1][1]:intervals[-1][1]=max(b,intervals[-1][1])
                else:intervals.append([a,b])
            if (right-left>max(4,width*.02) or bottom-top<height*.5
                    or len(intervals)<2 or sum(b-a for a,b in intervals)<height*.1):continue
            cached.append([max(0,(left-1)*r.width/width),max(0,(top-1)*r.height/height),
                           min(r.width,(right+1)*r.width/width),min(r.height,(bottom+1)*r.height/height)])
        display._vision_spine_fragments=cached
    return [box for box in cached if not any(box[0]<seed[2] and box[2]>seed[0]
        and box[1]<seed[3] and box[3]>seed[1] for seed in protected)]


def _prose_ink_baseline(display,box):
    """Measure the dominant glyph bottoms, excluding a word's descender tail."""
    from PIL import Image
    from collections import Counter
    pix=display.roi_pixmap(box,scale=6,max_pixels=200_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    threshold=min(160,max(picture.getdata())-35)
    columns=[]
    for x in range(picture.width):
        rows=[y for y in range(picture.height) if picture.getpixel((x,y))<threshold]
        if rows:columns.append((min(rows),max(rows)+1))
    if not columns:return None
    height=max(b for a,b in columns)-min(a for a,b in columns)
    bottoms=Counter(b for a,b in columns if b-a>=height*.4)
    if not bottoms:return None
    # Adjacent raster rows belong to one typographic baseline. Ties prefer the
    # higher row: a descender cannot provide affirmative superscript evidence.
    baseline=max(bottoms,key=lambda b:(sum(bottoms.get(b+d,0) for d in (-1,0,1)),-b))
    return box[1]+baseline*(box[3]-box[1])/pix.height


def parenthetical_ink(display,box,neighbours=()):
    """Prove raised inner ink against nearby original prose baselines.

    Parentheses extend below an ordinary digit's baseline. Their descenders
    alone cannot distinguish a normal parenthetical number from a reference.
    Source word boxes select nearby prose; its actual ink supplies the baseline.
    """
    from PIL import Image
    pix=display.roi_pixmap(box,scale=6,max_pixels=200_000)
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    # Soft scan edges can connect a tiny digit to its enclosing parenthesis.
    # Measure the dark component cores, rather than those interpolated halos.
    threshold=min(160,max(picture.getdata())-35)
    ink={(x,y) for y in range(picture.height) for x in range(picture.width)
         if picture.getpixel((x,y))<threshold};components=[]
    while ink:
        start=ink.pop();stack=[start];points=[]
        while stack:
            x,y=stack.pop();points.append((x,y))
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                q=(x+dx,y+dy)
                if q in ink:ink.remove(q);stack.append(q)
        if len(points)>=3:
            components.append((min(x for x,y in points),min(y for x,y in points),
                               max(x for x,y in points)+1,max(y for x,y in points)+1))
    components.sort()
    if len(components)<3:return None
    tallest=max(b[3]-b[1] for b in components)
    delimiters=[b for b in components if b[3]-b[1]>=tallest*.7]
    if len(delimiters)<2:return None
    left,right=delimiters[0],delimiters[-1]
    stems=(left,right)
    height=min(left[3]-left[1],right[3]-right[1])
    if height<8 or abs(left[1]-right[1])>height*.3:return None
    inner=[]
    for b in components:
        if b in stems:continue
        center=(b[0]+b[2])/2
        if not (sum(left[::2])/2<center<sum(right[::2])/2):continue
        # A detached parenthesis tip is low and adjacent to its own stem. Keep
        # raw glyph components: a digit can overlap that stem's x projection
        # while remaining physically separate above its baseline.
        if (b[3]-b[1]<height*.25 and b[2]>=stems[1][0]-height*.25
                and (b[1]>=stems[1][3]-height*.25 or b[3]<=stems[1][1]+height*.15)):
            right=(min(right[0],b[0]),min(right[1],b[1]),max(right[2],b[2]),max(right[3],b[3]))
        elif (b[3]-b[1]<height*.25 and b[0]<=stems[0][2]+height*.25
                and (b[1]>=stems[0][3]-height*.25 or b[3]<=stems[0][1]+height*.15)):
            left=(min(left[0],b[0]),min(left[1],b[1]),max(left[2],b[2]),max(left[3],b[3]))
        else:inner.append(b)
    if not inner:return None
    top=min(b[1] for b in inner);bottom=max(b[3] for b in inner)
    if bottom-top>=height*.8 or bottom>=min(left[3],right[3])-height*.2:return None
    sx=(box[2]-box[0])/pix.width;sy=(box[3]-box[1])/pix.height
    baselines=[_prose_ink_baseline(display,b) for b in neighbours]
    baselines=[b for b in baselines if b is not None]
    if not baselines or box[1]+bottom*sy>=min(baselines)-max(.7,height*sy*.1):return None
    return {'bbox':[box[0]+left[0]*sx,box[1]+min(top,left[1])*sy,
                    box[0]+right[2]*sx,box[1]+max(left[3],right[3])*sy],
            'inner_bbox':[box[0]+min(b[0] for b in inner)*sx,box[1]+top*sy,
                          box[0]+max(b[2] for b in inner)*sx,box[1]+bottom*sy],
            'method':'raised_inner_connected_source_ink'}


def _word_ink_components(pix):
    """Measure connected ink without changing any exported source pixels."""
    from PIL import Image
    picture=Image.open(io.BytesIO(pix.tobytes('png'))).convert('L')
    mask=picture.point(lambda value:255 if value<180 else 0)
    pixels=mask.load();visited=set();components=[]
    for x in range(picture.width-1,-1,-1):
        for y in range(picture.height):
            if not pixels[x,y] or (x,y) in visited:continue
            stack=[(x,y)];visited.add((x,y));left=right=x;top=bottom=y
            while stack:
                a,b=stack.pop();left=min(left,a);right=max(right,a);top=min(top,b);bottom=max(bottom,b)
                for c,d in ((a-1,b),(a+1,b),(a,b-1),(a,b+1)):
                    if 0<=c<picture.width and 0<=d<picture.height and pixels[c,d] and (c,d) not in visited:
                        visited.add((c,d));stack.append((c,d))
            if bottom>top and right>left:components.append((left,top,right+1,bottom+1))
    return picture,components


def unrecognized_terminal_symbol_ink(display,box,closing_punctuation):
    """Bound extra original ink between a terminal period and its bracket.

    This supplies geometry, never a symbol identity. A local glyph reader must
    independently identify the component before a caption note can link to it.
    Ordinary final letters, periods and brackets cannot earn that verification.
    """
    if closing_punctuation not in (')',']'):return None
    pix=display.roi_pixmap(box,scale=12,max_pixels=200_000)
    picture,components=_word_ink_components(pix)
    tall=[c for c in components if c[3]-c[1]>=picture.height*.45]
    if len(tall)<2:return None
    bracket=max(tall,key=lambda c:c[2])
    before=[c for c in tall if c[2]<=bracket[0]]
    if not before:return None
    candidate=max(before,key=lambda c:c[2])
    dots=[c for c in components if c[3]-c[1]<=picture.height*.35
          and c[2]-c[0]<=picture.height*.5 and c[3]>=picture.height*.55]
    preceding=[c for c in dots if c[2]<=candidate[0]
               and 0<=candidate[0]-c[2]<=picture.height]
    # Without an extra symbol, the ordinary terminal period lies AFTER the
    # final letter. It must not authorize a reading of that letter as a note.
    if not preceding or any(candidate[2]<=c[0]<bracket[0] for c in dots):return None
    left,top,right,bottom=candidate
    sx=(box[2]-box[0])/picture.width;sy=(box[3]-box[1])/picture.height
    return [box[0]+left*sx,box[1]+top*sy,box[0]+right*sx,box[1]+bottom*sy]


def terminal_symbol_ink(display, box, closing_punctuation=''):
    """Bound the rightmost original component of a corroborated symbol token.

    The caller proves the literal terminal symbol and its finite note owner.
    Character recognition can miss a full-height dagger fused to punctuation;
    this measures its original component without changing the exported pixels.
    """
    pix=display.pixmap(box,scale=6,max_pixels=200_000)
    picture,components=_word_ink_components(pix)
    if not components:return None
    ordered=sorted(components,key=lambda c:(c[2],c[3]-c[1]),reverse=True)
    glyph_height=max(c[3]-c[1] for c in ordered)
    offset=0
    for punctuation in reversed(closing_punctuation):
        if offset>=len(ordered):return None
        first=ordered[offset];offset+=1
        if punctuation in (')',']'):
            # Rasterisation can disconnect a narrow bracket's tip from its
            # stem. Its vertically adjacent ink at the same x is one glyph.
            while offset<len(ordered):
                second=ordered[offset]
                horizontal=min(first[2],second[2])-max(first[0],second[0])
                gap=max(0,max(first[1],second[1])-min(first[3],second[3]))
                if horizontal<=0 or gap>max(2,glyph_height*.1):break
                first=(min(first[0],second[0]),min(first[1],second[1]),
                       max(first[2],second[2]),max(first[3],second[3]))
                offset+=1
        if punctuation in ('"','”') and offset<len(ordered):
            # One closing double quote can have two disconnected short strokes.
            # Group only adjacent upper ink with matching vertical extent; the
            # full-height dagger to its left cannot become a second quote stroke.
            second=ordered[offset]
            h1=first[3]-first[1];h2=second[3]-second[1]
            overlap=min(first[3],second[3])-max(first[1],second[1])
            gap=max(0,first[0]-second[2])
            if (max(h1,h2)<glyph_height*.6 and max(h1,h2)<=min(h1,h2)*2
                    and overlap>=min(h1,h2)*.5
                    and gap<=max(h1,h2)*1.5):offset+=1
    if offset>=len(ordered):return None
    left,top,right,bottom=ordered[offset]
    sx=(box[2]-box[0])/picture.width;sy=(box[3]-box[1])/picture.height
    return [box[0]+left*sx,box[1]+top*sy,box[0]+right*sx,box[1]+bottom*sy]


def raised_markers(display, box, cache):
    """Independently read small raised digits, including full-height parentheses.

    Character geometry, rather than a whole-word height, distinguishes an inner
    superscript from an ordinary parenthetical list item. Optical word readings
    are deliberately not an input: an erroneous word-level marker cannot lock
    the printed reference to the wrong note.
    """
    import re
    import statistics
    pix=display.pixmap(box,scale=6,max_pixels=3_000_000)
    data=pix.tobytes('png');identity=hashlib.sha256(data+json.dumps(list(box)).encode()).hexdigest()
    root=Path(cache);root.mkdir(parents=True,exist_ok=True)
    target=root/('raised-character-v7-'+identity+'.json')
    if target.exists():return json.loads(target.read_text())
    engine=shutil.which('tesseract')
    if not engine:raise RuntimeError('Printed note references require local pixel recognition')
    proc=subprocess.run([engine,'stdin','stdout','-l','eng','--psm','6','makebox'],
        input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    if proc.returncode:raise RuntimeError('Printed note character recognition failed')
    chars=[];sx=(box[2]-box[0])/pix.width;sy=(box[3]-box[1])/pix.height
    for line in proc.stdout.decode().splitlines():
        fields=line.rsplit(' ',5)
        if len(fields)!=6:continue
        char,x0,y0,x1,y1,_=fields
        try:x0,y0,x1,y1=map(int,(x0,y0,x1,y1))
        except ValueError:continue
        chars.append((char,[box[0]+x0*sx,box[1]+(pix.height-y1)*sy,
                            box[0]+x1*sx,box[1]+(pix.height-y0)*sy]))
    text=''.join(c for c,_ in chars);result=[]
    for match in re.finditer(r'\(([^()]{1,4})\)',text):
        digits=chars[match.start()+1:match.end()-1]
        left,right=chars[match.start()][1],chars[match.end()-1][1]
        top=min(b[1] for _,b in digits);bottom=max(b[3] for _,b in digits)
        height=bottom-top;paren_height=max(left[3]-left[1],right[3]-right[1])
        neighbours=[b for c,b in chars if c.isalpha() and abs(b[3]-left[3])<paren_height*.4
                    and abs(b[0]-left[0])<paren_height*12]
        baseline=statistics.median(b[3] for b in neighbours) if neighbours else min(left[3],right[3])
        if not (height<paren_height*.75 and bottom<baseline-max(.7,paren_height*.18)):
            # Character segmentation may merge a raised digit with a full-height
            # parenthesis. Those boxes cannot establish its role. Measure the
            # original connected ink and adjacent prose baselines instead, then
            # recognize only the independently bounded inner glyphs.
            territory=[left[0],min(left[1],right[1],top),
                       right[2],max(left[3],right[3],bottom)]
            evidence=parenthetical_ink(display,territory,neighbours)
            if evidence:
                from PIL import Image,ImageOps
                inner=evidence['inner_bbox']
                crop=display.roi_pixmap([inner[0]-.5,inner[1]-.5,
                                       inner[2]+.5,inner[3]+.5],
                                      scale=12,max_pixels=200_000)
                picture=Image.open(io.BytesIO(crop.tobytes('png'))).convert('L')
                stream=io.BytesIO();ImageOps.expand(picture,border=24,fill=255).save(stream,'PNG')
                reply=subprocess.run([engine,'stdin','stdout','-l','eng','--psm','7',
                                      '-c','tessedit_char_whitelist=0123456789'],
                    input=stream.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
                label=reply.stdout.decode().strip()
                if reply.returncode==0 and re.fullmatch(r'\d{1,4}',label):
                    result.append({'label':label,'bbox':inner,
                                   'method':'raised_inner_connected_source_ink'})
            continue
        if height<paren_height*.75 and bottom<baseline-max(.7,paren_height*.18):
            label=match[1]
            if not label.isdigit():
                # At text OCR scale, a superscript seven can look like an
                # apostrophe. Read only the independently bounded inner ink;
                # no surrounding parentheses or vocabulary dictate its label.
                from PIL import Image,ImageOps
                inner=[left[2]+.1,min(top,left[1],right[1])-.7,right[0]-.1,baseline]
                if inner[2]<=inner[0]:continue
                crop=display.pixmap(inner,scale=12,max_pixels=200_000)
                picture=Image.open(io.BytesIO(crop.tobytes('png'))).convert('L')
                stream=io.BytesIO();ImageOps.expand(picture,border=24,fill=255).save(stream,'PNG')
                reply=subprocess.run([engine,'stdin','stdout','--psm','7','-c','tessedit_char_whitelist=0123456789'],
                    input=stream.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
                label=reply.stdout.decode().strip()
            if re.fullmatch(r'\d{1,4}',label):
                result.append({'label':label,'bbox':[digits[0][1][0],top,digits[-1][1][2],bottom],
                               'method':'raised_inner_character_pixels'})
    occupied=set(i for match in re.finditer(r'\([^()]{1,4}\)',text) for i in range(match.start(),match.end()))
    for match in re.finditer(r'(?:\d{1,4}|[*†‡+]|[’\'])',text):
        if any(i in occupied for i in range(match.start(),match.end())):continue
        cluster=chars[match.start():match.end()];bounds=[b for _,b in cluster]
        top=min(b[1] for b in bounds);bottom=max(b[3] for b in bounds)
        left=min(b[0] for b in bounds);right=max(b[2] for b in bounds)
        symbol=match[0] in ('*','†','‡','+')
        neighbours=[b for c,b in chars if c.isalpha() and b[0]<left and left-b[2]<25
                    and (abs((b[1]+b[3]-top-bottom)/2)<max(b[3]-b[1],bottom-top)
                         if symbol else b[1]<=bottom and b[3]>bottom)]
        if not neighbours:continue
        baseline=statistics.median(b[3] for b in neighbours)
        height=statistics.median(b[3]-b[1] for b in neighbours)
        if not symbol and (bottom>=baseline-max(.7,height*.18) or bottom-top>=height*.8):continue
        label=match[0]
        if label in ("'",'’'):
            from PIL import Image,ImageOps
            crop=display.pixmap([left-.5,top-.5,right+.5,bottom+.5],scale=12,max_pixels=200_000)
            stream=io.BytesIO();ImageOps.expand(Image.open(io.BytesIO(crop.tobytes('png'))),border=24,fill='white').save(stream,'PNG')
            reply=subprocess.run([engine,'stdin','stdout','--psm','7','-c','tessedit_char_whitelist=0123456789'],input=stream.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            label=reply.stdout.decode().strip()
        if re.fullmatch(r'\d{1,4}|[*†‡+]',label):
            result.append({'label':label,'bbox':[left,top,right,bottom],'method':'raised_bare_character_pixels','kind':'bare'})
    temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(result));os.replace(temp,target)
    return result
