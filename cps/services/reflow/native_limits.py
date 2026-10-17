"""Bound decoded PDF streams before page interpretation in the native worker."""

# These count decoded stream bytes, not compressed file bytes or image pixels.
# A 128 MiB stream accommodates a 40-megapixel RGB image. An 8 GiB aggregate
# accommodates long scanned sources while rejecting disproportionate expansion.
MAX_STREAM_DECODED_BYTES = 128 * 1024**2
MAX_DOCUMENT_DECODED_BYTES = 8 * 1024**3


class DecodedBudgetExceeded(ValueError):
    pass


def check_document(doc):
    """Discard bounded decoded chunks; never build a complete decoded buffer.

    The supported PyMuPDF binding exposes MuPDF's decoded stream reader and fz_skip. This
    applies the actual filter chain, including indirect lengths and image filters,
    rather than trusting a declared stream length or a FlateDecode-only estimate.
    Each physical stream is counted once even when multiple pages reuse it.
    """
    import pymupdf
    mupdf = pymupdf.mupdf
    pdf = pymupdf._as_pdf_document(doc)
    total = 0
    for xref in range(1, doc.xref_length()):
        if not doc.xref_is_stream(xref):
            continue
        remaining = MAX_DOCUMENT_DECODED_BYTES - total
        limit = min(MAX_STREAM_DECODED_BYTES, remaining)
        stream = mupdf.pdf_open_stream_number(pdf, xref)
        try:
            # fz_skip reads/discards through MuPDF's bounded stream buffer. Its
            # limit+1 distinguishes exactly-at-limit EOF from overflow.
            decoded = mupdf.fz_skip(stream, limit + 1)
        finally:
            stream = None  # release the reference-counted reader promptly
        if decoded > limit:
            if remaining < MAX_STREAM_DECODED_BYTES:
                raise DecodedBudgetExceeded('The decoded PDF document exceeds the 8 GiB safety budget.')
            raise DecodedBudgetExceeded('A decoded PDF stream exceeds the 128 MiB safety budget.')
        total += decoded
    return total
