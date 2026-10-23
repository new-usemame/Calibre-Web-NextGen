"""Publication budgets and one-page raster reuse for inspection evidence.

Reading/cache images are independent of this display encoder. A page preview is
144 dpi; smaller details retain 216 dpi, bounded before raster allocation. Only
one page's RGB samples remain live, and identical resources are packaged once.
"""
import io
import hashlib
import math
from pathlib import Path

from .unit_recovery import SourceIntegrityError

MIN_BOOK_BYTES = 150 * 1024 * 1024
SOURCE_MULTIPLIER = 3
EVIDENCE_PIXELS = 6_000_000
EVIDENCE_QUALITY = 75
# Overviews preserve the full144dpi page. High-resolution inspection crops
# retain their separate216dpi/quality75 profile and do not spend this reserve.
EVIDENCE_PREVIEW_QUALITY = 30
EVIDENCE_SIDE = 4096


class OutputTooLarge(SourceIntegrityError):
    """A candidate cannot be published within its source-relative size budget."""


def source_size(doc):
    """Use the actual input file; in-memory primitive callers get the floor."""
    name = getattr(doc, 'name', None)
    return Path(name).stat().st_size if name and Path(name).is_file() else 0


def limit(source_bytes):
    if type(source_bytes) is not int or source_bytes < 0:
        raise ValueError('invalid source byte count')
    return max(MIN_BOOK_BYTES, SOURCE_MULTIPLIER * source_bytes)


def check(source_bytes, output_bytes):
    """The same strict gate applies to ordinary and repaired archives."""
    budget = limit(source_bytes)
    if type(output_bytes) is not int or output_bytes < 0:
        raise ValueError('invalid output byte count')
    report = dict(source_bytes=source_bytes, limit_bytes=budget,
                  output_bytes=output_bytes, passed=output_bytes <= budget)
    if not report['passed']:
        raise OutputTooLarge('EPUB exceeds its source-relative size budget: '
                             '%d > %d bytes' % (output_bytes, budget))
    return report


def native_evidence(display, rect, *, owner):
    """Use exact native bits only after complete visible-page proof.

    Prepared pages with an original-frame mapping must use the original ROI
    renderer instead. Keeping that boundary prevents a prepared bitmap from
    replacing the original source selected by its provenance record.
    """
    import weakref
    previous = getattr(owner, '_native_display', lambda: None)()
    if previous is not display:
        if previous is not None:
            previous.__dict__.pop('_bitonal', None)
        # Release both possible raster forms before proving the next page.
        owner.image = None
        owner.preview = None
        owner.identity = None
        owner._native_display = weakref.ref(display)
    if getattr(display, 'provenance', {}).get('original_frame') is not None:
        return None
    prove = getattr(display, '_native_bitonal', None)
    if prove is None or prove() is None:
        return None
    return display.source_image(rect)


def _validate_original_frame(display):
    frame = display.provenance.get('original_frame')
    if frame is not None:
        try:
            def sha(path):
                with Path(path).open('rb') as handle:
                    return hashlib.file_digest(handle, 'sha256').hexdigest()
            if (display.doc.is_dirty or sha(display.doc.name) != frame['prepared_pdf_sha256']
                    or sha(frame['original_pdf']) != frame['original_pdf_sha256']):
                raise SourceIntegrityError('Original source frame identity changed')
        except (OSError, KeyError, TypeError) as error:
            raise SourceIntegrityError('Invalid original source frame provenance') from error


class EvidenceRaster:
    """Reuse a bounded original page, never a recognition or reconstructed page."""
    def __init__(self):
        self.identity = None
        self.image = None
        self.preview = None
        self._raster_source_identity = None
        self._raster_display = lambda: None

    @staticmethod
    def _source_identity(display):
        from .unit_recovery import page_fingerprint
        return (display.pno, display.angle, tuple(display.rect),
                repr(display.provenance), page_fingerprint(display.doc, display.pno))

    def cached_preview(self, display):
        """Return only an existing complete preview of this unchanged source.

        A lookup allocates no raster. The page digest covers current encoded
        PDF objects and resources, including in-memory edits. Original-frame
        files receive the same validation as a fresh evidence query.
        """
        previous = self._raster_display()
        if not self.preview or previous is None or previous.doc is not display.doc:
            return None
        _validate_original_frame(display)
        if self._raster_source_identity != self._source_identity(display):
            return None
        return self.preview

    def jpeg(self, display, rect, *, detail=False):
        from PIL import Image, ImageChops
        from . import extract
        reading = extract.pymupdf.Rect(rect) & display.rect
        if reading.is_empty or reading.is_infinite:
            raise ValueError('invalid inspection evidence region')
        _validate_original_frame(display)
        native = native_evidence(display, reading, owner=self)
        if native is not None:
            return native
        identity = (id(display.doc), display.pno, display.angle,
                    tuple(display.rect), repr(display.provenance.get('original_frame')))
        if self.identity != identity:
            # Discard the prior frame before allocating the next one.
            self.image = None
            self.preview = None
            scale = min(3, EVIDENCE_SIDE/display.rect.width, EVIDENCE_SIDE/display.rect.height)
            source_identity = self._source_identity(display)
            pix = display.roi_pixmap(scale=scale, max_pixels=EVIDENCE_PIXELS)
            import weakref
            self._raster_source_identity = source_identity
            self._raster_display = weakref.ref(display)
            self.image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            self.identity = identity
        whole = not detail and tuple(reading) == tuple(display.rect)
        if whole and self.preview is not None:
            return self.preview
        image = self.image
        sx, sy = image.width / display.rect.width, image.height / display.rect.height
        box = (max(0, math.floor((reading.x0-display.rect.x0)*sx)),
               max(0, math.floor((reading.y0-display.rect.y0)*sy)),
               min(image.width, math.ceil((reading.x1-display.rect.x0)*sx)),
               min(image.height, math.ceil((reading.y1-display.rect.y0)*sy)))
        crop = image.crop(box)
        if (min(sx,sy) < 2.99 and reading.get_area() < display.rect.get_area()/4
                and reading.get_area()*9 <= EVIDENCE_PIXELS):
            # A large page must not reduce the sampling of small printed type.
            # The page preview stays shared; a tight enlargement can use its own
            # bounded original ROI at the full requested detail resolution.
            scale = min(3, EVIDENCE_SIDE/reading.width, EVIDENCE_SIDE/reading.height)
            pix = display.roi_pixmap(reading, scale=scale, max_pixels=EVIDENCE_PIXELS)
            crop = Image.frombytes('RGB', (pix.width,pix.height), pix.samples)
        # Broad context is a page preview. Tight details retain the full frame's
        # resolution; a reader can inspect small printed marks without upscaling.
        if not detail and reading.get_area() >= display.rect.get_area() / 4:
            crop.thumbnail((max(1, math.ceil(reading.width*2)),
                            max(1, math.ceil(reading.height*2))), Image.Resampling.LANCZOS)
        r, g, b = crop.split()
        if ImageChops.difference(r,g).getbbox() is None and ImageChops.difference(r,b).getbbox() is None:
            crop = r
        stream = io.BytesIO()
        preview=not detail and reading.get_area() >= display.rect.get_area()/4
        crop.save(stream, format='JPEG', quality=EVIDENCE_PREVIEW_QUALITY if preview else EVIDENCE_QUALITY, optimize=True,
                  subsampling=2 if preview else 0)
        data = stream.getvalue()
        if whole:
            self.preview = data
        return data
