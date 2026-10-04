"""Optional local-model artwork observations, bound before canonical source sealing.

This adapter does not run a detector or infer missing text. Only disjoint,
nonblank source crops in an explicitly supported frame may become figures.
"""
import hashlib
import json
import math
import weakref
from dataclasses import asdict, dataclass

from . import extract

VERSION = 'local-visual-objects-1'
RASTER_VERSION = 'visual-object-raster-1'
MODEL_ID = 'PaddlePaddle/PP-DocLayoutV3_safetensors'
MODEL_REVISION = '97d101e6db2642e162a1d05392d1b0231c91033e'
MIN_CONFIDENCE = .8
_issued = weakref.WeakKeyDictionary()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _rect(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4 or
            not all(type(v) in (int, float) and math.isfinite(v) for v in value)):
        raise ValueError('invalid visual object rectangle')
    if value[0] >= value[2] or value[1] >= value[3]:
        raise ValueError('empty visual object rectangle')
    return tuple(value)


def _touches(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def render_input(doc, pno):
    """Reproducible, bounded detector input; cached results must bind these bytes."""
    page = doc[pno]
    if page.rotation or page.rect.x0 or page.rect.y0:
        raise ValueError('unsupported visual object rotation/frame')
    if math.ceil(page.rect.width * 1.5) * math.ceil(page.rect.height * 1.5) > 4_000_000:
        raise ValueError('visual object raster exceeds pixel bound')
    image = page.get_pixmap(matrix=extract.pymupdf.Matrix(1.5, 1.5),
                           colorspace=extract.pymupdf.csRGB, alpha=False)
    raster = image.tobytes('jpeg', jpg_quality=80)
    if len(raster) > 900 * 1024:
        raise ValueError('visual object raster exceeds byte bound')
    return raster


@dataclass(frozen=True, eq=False)
class _Observation:
    page: int
    raw_digest: str
    entries: tuple

    def identity(self):
        return _digest([self.page, self.raw_digest, self.entries])


def prepare(doc, raw, result):
    """Validate a detector result against the actual PDF, raster and retained text.

    Rotated/transformed frames are deliberately unsupported in this first adapter.
    A caller must run the detector on render_input(), or reuse a result whose
    complete binding still matches. A different JPEG with equal dimensions is
    insufficient. No weights, downloads, or remote credentials are used here.
    """
    from . import visual_coverage
    if isinstance(result, dict) and result.get('version') in (visual_coverage.VERSION, visual_coverage.REGION_VERSION):
        return visual_coverage.prepare(doc, raw, result)
    from .model import _jpeg_dimensions
    if not doc.name:
        raise ValueError('visual objects require a named source PDF')
    frame = list(doc[raw.pno].rect)
    geometry = getattr(raw, 'source_geometry', {})
    if (geometry and geometry != dict(space='reading', orientation=0,
                                     source_rotation=0, page_rect=frame)):
        raise ValueError('unsupported or stale visual object source frame')
    if (geometry.get('orientation', 0) or geometry.get('source_rotation', 0) or
            abs(raw.width - frame[2]) > .001 or abs(raw.height - frame[3]) > .001):
        raise ValueError('unsupported visual object source frame')
    raster = render_input(doc, raw.pno)
    size = list(_jpeg_dimensions(raster))
    expected = dict(version=VERSION, model_id=MODEL_ID, model_revision=MODEL_REVISION,
                    raster_version=RASTER_VERSION, page=raw.pno,
                    pdf_sha256=extract.document_fingerprint(doc), rotation=0,
                    image_sha256=hashlib.sha256(raster).hexdigest(),
                    image_size=size, page_rect=frame)
    if (not isinstance(result, dict) or set(result) != set(expected) | {'detections'} or
            any(result.get(k) != v for k, v in expected.items())):
        raise ValueError('visual object source/model/raster binding mismatch')
    encoded = _json(result)
    if len(encoded.encode()) > 65536:
        raise ValueError('visual object evidence exceeds bound')
    result = json.loads(encoded)  # detach all caller-owned mutable values
    detections = result['detections']
    if not isinstance(detections, list) or len(detections) > 256:
        raise ValueError('invalid visual object detection count')
    text_boxes = [_rect(line.bbox) for block in raw.text_blocks for line in block.lines]
    probe = extract.ScanPixelProbe(doc, raw.pno, mask=text_boxes)
    raw_digest = _digest(asdict(raw))
    entries = []
    for index, item in enumerate(detections):
        if not isinstance(item, dict):
            raise ValueError('invalid visual object detection')
        if item.get('label') != 'image':
            continue
        confidence = item.get('confidence')
        if type(confidence) not in (int, float) or not math.isfinite(confidence):
            raise ValueError('invalid visual object confidence')
        if not MIN_CONFIDENCE <= confidence <= 1:
            continue
        pixel = _rect(item.get('bbox'))
        if pixel[0] < 0 or pixel[1] < 0 or pixel[2] > size[0] or pixel[3] > size[1]:
            raise ValueError('visual object outside bound raster')
        box = tuple(v * frame[2 + i % 2] / size[i % 2] for i, v in enumerate(pixel))
        if any(_touches(box, line) for line in text_boxes):
            continue
        if not probe.source_has_ink(box):
            continue
        proof = dict(expected, detector_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
                     raw_digest=raw_digest, detection_index=index, detection=item,
                     source_bbox=list(box), confidence_floor=MIN_CONFIDENCE)
        entries.append((box, _json(proof)))
        if len(entries) > 32:
            raise ValueError('visual object candidate count exceeds bound')
    observation = _Observation(raw.pno, raw_digest, tuple(entries))
    _issued[observation] = observation.identity()
    return observation


def append_regions(raw, skel, observation, layout=None):
    """Append checked figures after transcript ownership is settled; never absorb text."""
    from . import visual_coverage
    if isinstance(observation, visual_coverage._Observation):
        return visual_coverage.append_regions(raw, skel, observation, layout)
    from .skeleton import Region
    if (not isinstance(observation, _Observation) or
            _issued.get(observation) != observation.identity() or
            observation.page != raw.pno or observation.raw_digest != _digest(asdict(raw))):
        raise ValueError('unissued or stale visual object source')
    occupied = [r.bbox for r in skel.regions if r.kind in ('figure', 'artwork')]
    for box, proof in observation.entries:
        if any(_touches(box, other) for other in occupied):
            continue
        band, column = layout.place(box) if layout else (0, 0)
        skel.regions.append(Region(kind='figure', bbox=box, needs_ink=True,
                                   reason='local_visual_object', band=band, column=column,
                                   visual_evidence=json.loads(proof)))
        occupied.append(box)
