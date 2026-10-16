"""Publish an unresolved unit from its original page without inventing a reading.

The recovery controller owns retry allowances and source ownership. This module
only renders that controller's selected source page and supplies valid markup.
It neither resolves note owners nor grants live-text or transcription credit.
"""
from html import escape
import math
import re


_ROLES = {"page", "body", "other", "heading", "caption", "figure", "table",
          "footnote", "note", "running_head", "folio"}
_NOTICE = "This source unit could not be transcribed safely. Its original source is retained."


def _rectangle(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in value)
            and value[0] < value[2] and value[1] < value[3])


def source_unit(display, pno, bid, role, package, image_records, reason, bbox=None):
    """Return ``(fragment, asides, report)`` for a source-backed degraded unit.

    ``bbox`` is an optional already proved reading-space unit rectangle. The
    image always contains the complete original page; the rectangle is never
    used to crop away unknown content. Notes preserve their canonical target
    and leave backlink insertion to the normal note-finishing pass. Other
    known units use an optional source context, while a page fallback displays
    the original image directly in reading flow.

    A rendering or semantic asset failure remains owned by this source unit.
    The publication controller can escalate a failed unit to its original page;
    fatal source identity or storage failures still propagate.
    """
    if not isinstance(pno, int) or isinstance(pno, bool) or pno < 0:
        raise ValueError("Invalid source unit page")
    if not isinstance(bid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", bid):
        raise ValueError("Invalid source unit identity")
    if role not in _ROLES:
        raise ValueError("Invalid source unit role")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Source unit requires a failure reason")
    page_box = list(display.rect)
    if not _rectangle(page_box):
        raise ValueError("Invalid original source page rectangle")
    if bbox is not None and not _rectangle(bbox):
        raise ValueError("Invalid source unit rectangle")
    unit_box = list(bbox) if bbox is not None else list(page_box)
    if not (page_box[0] <= unit_box[0] < unit_box[2] <= page_box[2]
            and page_box[1] <= unit_box[1] < unit_box[3] <= page_box[3]):
        raise ValueError("Source unit rectangle is outside its page")

    note = role in ("note", "footnote")
    art = role in ("figure", "table")
    kind = "footnote" if note else role
    if note:
        target = f"v4-note-{pno}-{bid}"
    elif art:
        # Retain the existing figure-source context/control identity contract.
        target = f"v4-figure-source-{pno}-{bid}"
    else:
        target = f"v4-{ 'page' if role == 'page' else 'unit' }-source-{pno}-{bid}"
    ref = "r-" + target
    src = f"images/{target}.jpg"
    from . import unit_recovery
    with unit_recovery.guard(pno, "source_unit_render", block=None if role == "page" else bid):
        try:
            data = display.roi_jpeg(display.rect, scale=2, quality=90)
        except Exception as error:
            controller = unit_recovery.current()
            if controller is None or unit_recovery.fatal(error):
                raise
            event = dict(page=pno, stage="source_unit_render", block=bid,
                         cause_type=type(error).__name__, reason=str(error),
                         transition="DETERMINISTIC_SOURCE_RENDER_REPAIR")
            if controller.claim_source_render_repair(pno):
                try:
                    # Repeat the same proved original page and bounded encoder,
                    # never a prepared-image substitution or a model request.
                    data = display.roi_jpeg(display.rect, scale=2, quality=90)
                except Exception as repeated:
                    if unit_recovery.fatal(repeated):
                        raise
                    event.update(status="original_source_unrenderable",
                                 repeated_cause_type=type(repeated).__name__,
                                 repeated_reason=str(repeated))
                    controller.events.append(event)
                    failure = unit_recovery.SourceIntegrityError("Original SOURCE page is unrenderable after one local encode repair")
                    failure.source_recovery_event = event
                    raise failure from repeated
                event["status"] = "SOURCE_RENDER_REPAIRED"
                controller.events.append(event)
            else:
                event["status"] = "original_source_unrenderable"
                event["repair_allowance_used"] = True
                controller.events.append(event)
                failure = unit_recovery.SourceIntegrityError("Original SOURCE page is unrenderable; local encode repair allowance was used")
                failure.source_recovery_event = event
                raise failure from error
        writer = getattr(package, "source_image", None)
        if writer is None:
            # Compatibility for direct primitive users predating trusted assets.
            writer = package.image
        writer(src, data)
    src = getattr(package, "aliases", {}).get(src, src)
    image_records[src] = dict(page=pno, bbox=page_box,
                             kind="figure_source_reference" if art else "unit_source_reference",
                             unit_kind=kind, block=bid)

    notice = escape(_NOTICE)
    why = escape(reason)
    image = (f'<img src="{escape(src, quote=True)}" '
             f'alt="Complete original source page for unresolved {escape(kind, quote=True)}"/>')
    disclosure = f"{notice} Reason: {why}"
    if note:
        # _finish_notes recognizes this first <p> and adds only actual origins.
        aside = (f'<aside id="{target}" class="footnote vision-disclosure" '
                 f'epub:type="footnote"><p>{disclosure}</p>{image}</aside>')
        fragment, asides = "", [aside]
    elif role == "page":
        control = (f'<a id="{ref}" class="source-disclosure-ref" epub:type="noteref" '
                   f'href="#{target}">View retained original source page</a>')
        fragment = (f'<figure id="{target}" class="source-original">'
                    f'<figcaption>{disclosure} {control}</figcaption>{image}'
                    f'<a href="#{ref}">Return</a></figure>')
        asides = []
    else:
        label = "View figure in original page" if art else "View original source page"
        control = (f'<a id="{ref}" class="source-disclosure-ref" epub:type="noteref" '
                   f'href="#{target}">{label}</a>')
        fragment = (f'<figure><figcaption>{disclosure}</figcaption>{control}</figure>'
                    if art else f'<div class="source-disclosure"><p>{disclosure} {control}</p></div>')
        asides = [(f'<aside id="{target}" class="footnote vision-disclosure" '
                   f'epub:type="footnote"><p>{disclosure}</p>{image}'
                   f'<a href="#{ref}">Return</a></aside>')]
        if role in ("running_head", "folio"):
            # Furniture never belongs to BODY, including its generated control.
            # Both the control and retained pixels travel through the notes lane,
            # leaving the authored BODY endpoints available to the seam joiner.
            asides.insert(0, f'<aside class="footnote vision-disclosure" '
                          f'epub:type="footnote"><p>{disclosure} {control}</p></aside>')
            fragment = ""

    block = dict(id=bid, role="footnote" if note else role, bbox=unit_box,
                 source_regions=[], printed_head=None, printed_tail=None,
                 printed_head_box=None, printed_tail_box=None,
                 published_tokens=[], marked=[], fallback=True,
                 guard=[dict(kind="source_context_fallback", reason=reason)],
                 continues_previous=False, continues_next=False,
                 degraded_source_context=True, source_bound_art=False)
    report = dict(page=pno, block=bid, unit_kind=kind, source_bbox=page_box,
                  unit_bbox=unit_box, image=src, source_reference=target,
                  reason=reason, status="DEGRADED_SOURCE_CONTEXT",
                  blocks=[block], words=0, marks=0, live_words=0,
                  source_image=src, source_reference_origin=None if note else ref)
    frame = getattr(display, "provenance", {}).get("original_frame")
    if frame:
        report["original_frame"] = dict(frame)
        report["original_page_index0"] = frame.get("original_page_index0")
    return fragment, asides, report
