"""A readable source-word preview measured against its printed line.

The complete inspection asset remains separate. Optional previews retain gray
edges and own-word components, with explicit line-relative size and descent.
A refused preview leaves the original evidence available.
"""
import io
import math
import statistics
from PIL import Image, ImageFilter

MAX_PIXELS = 300_000
X_HEIGHT_EM = .481


def _components(mask, width, height):
    remaining = bytearray(mask)
    for start in range(len(remaining)):
        if not remaining[start]:
            continue
        remaining[start] = 0
        stack = [start]; found = []
        while stack:
            pos = stack.pop(); found.append(pos)
            y,x = divmod(pos,width)
            for yy in range(max(0,y-1),min(height,y+2)):
                for xx in range(max(0,x-1),min(width,x+2)):
                    q=yy*width+xx
                    if remaining[q]:remaining[q]=0;stack.append(q)
        yield found


def preview(display, word_box):
    """Return PNG bytes, CSS metrics and actual crop geometry, or None.

    The bounded line strip estimates x-height/baseline from its dense rows. The
    source word box selects whole connected ink islands, not arbitrary slices.
    No glyph recognition or transcription decision occurs here.
    """
    from .extract import pymupdf
    box=pymupdf.Rect(word_box)
    if not box or box.width <= 0 or box.height <= 0:
        return None
    line=pymupdf.Rect(box.x0-60,box.y0-3,box.x1+60,box.y1+3) & display.rect
    scale=min(6,math.sqrt(MAX_PIXELS/max(1,line.get_area())))
    pix=display.pixmap(line,scale=scale,gray=True,max_pixels=MAX_PIXELS)
    im=Image.frombytes('L',(pix.width,pix.height),pix.samples)
    sx,sy=im.width/line.width,im.height/line.height
    pixels=im.tobytes(); w,h=im.size
    rows=[sum(v<140 for v in pixels[y*w:(y+1)*w]) for y in range(h)]
    peak=max(rows,default=0)
    dense=[i for i,n in enumerate(rows) if peak and n>=peak*.5]
    if not dense:return None
    # A gap between dense ink bands identifies separate printed rows. Their
    # combined height is not a font x-height; keep the complete inspection
    # asset rather than shrink several lines into one inline word.
    if any(b-a>max(2,round(2.5*sy)) for a,b in zip(dense,dense[1:])):
        return None
    xheight=max(4,dense[-1]-dense[0]+1)/sy
    baseline=line.y0+(dense[-1]+1)/sy
    # Disjoint printed rows need individual previews; a broad block rectangle
    # cannot supply credible inline metrics.
    if not .15*box.height <= xheight <= 1.3*box.height:return None
    region=pymupdf.Rect(box.x0-3,min(box.y0-3,baseline-2.4*xheight),
                         box.x1+3,max(box.y1+3,baseline+1.2*xheight)) & display.rect
    scale=min(6,math.sqrt(MAX_PIXELS/max(1,region.get_area())))
    pix=display.pixmap(region,scale=scale,gray=True,max_pixels=MAX_PIXELS)
    im=Image.frombytes('L',(pix.width,pix.height),pix.samples)
    sx,sy=im.width/region.width,im.height/region.height
    w,h=im.size; raw=im.tobytes(); keep=bytearray(w*h)
    bounds=[]
    for component in _components((v<160 for v in raw),w,h):
        xs=[q%w for q in component];ys=[q//w for q in component]
        left,right=min(xs),max(xs)+1;top,bottom=min(ys),max(ys)+1
        cx=region.x0+(left+right)/2/sx;cy=region.y0+(top+bottom)/2/sy
        # A disconnected capital crossbar or accent can sit above the dense
        # lowercase band. The source word's complete vertical box owns that
        # ink too; line metrics set display size, not a glyph clipping mask.
        if not(box.x0-.5<=cx<=box.x1+.5 and
               min(box.y0-1,baseline-1.7*xheight)<=cy<=max(box.y1+1,baseline+.45*xheight)):continue
        # Touching the work boundary can mean a clipped component: decline the
        # optional preview rather than certifying the surviving tail as whole.
        if min(left,top)<=0 or right>=w or bottom>=h:return None
        for q in component:keep[q]=255
        bounds.append((left,top,right,bottom))
    if not bounds:return None
    top=min(b[1] for b in bounds);bottom=max(b[3] for b in bounds)
    if (bottom-top)/sy>3.4*xheight:return None
    left=min(b[0] for b in bounds);right=max(b[2] for b in bounds)
    upper=min(baseline-1.75*xheight,region.y0+top/sy-.1*xheight)
    lower=max(baseline+.75*xheight,region.y0+bottom/sy+.1*xheight)
    crop=pymupdf.Rect(region.x0+left/sx-.12*xheight,upper,
                       region.x0+right/sx+.12*xheight,lower) & display.rect
    mask=Image.frombytes('L',(w,h),bytes(keep)).filter(ImageFilter.MaxFilter(3))
    clean=Image.new('L',(w,h),255);clean.paste(im,(0,0),mask)
    # Preserve the complete band even beyond the sampled work rectangle.
    output=Image.new('L',(max(1,math.ceil(crop.width*sx)),max(1,math.ceil(crop.height*sy))),255)
    output.paste(clean,(round((region.x0-crop.x0)*sx),round((region.y0-crop.y0)*sy)))
    factor=30/(xheight*sy)
    output=output.resize((max(1,round(output.width*factor)),max(1,round(output.height*factor))),Image.Resampling.LANCZOS)
    # The original dark core can be gray in a faded scan. Stretch only its
    # measured tone range, retaining antialiasing and exact stroke positions.
    # This creates near-black ink on white paper for monochrome dithering;
    # it neither recognizes letters nor redraws their shapes.
    ink=sorted(raw[i]for i,value in enumerate(keep)if value)
    blackpoint=min(150,ink[len(ink)//5])
    output=output.point(lambda v:round(max(0,min(1,(v-blackpoint)/(230-blackpoint)))**1.1*255))
    stream=io.BytesIO();output.save(stream,format='PNG',optimize=True)
    height=crop.height/xheight*X_HEIGHT_EM
    descent=(crop.y1-baseline)/xheight*X_HEIGHT_EM
    return stream.getvalue(),dict(bbox=list(crop),xheight=xheight,baseline=baseline,
        height_em=height,vertical_align_em=-descent,
        style=f'height:{height:.5f}em;vertical-align:{-descent:.5f}em;width:auto',
        method='gray_complete_components_and_printed_line_band')
