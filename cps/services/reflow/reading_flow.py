"""Keep generated disclosures and printed notes outside running source prose.

Opaque images have retained-source witnesses for boundary checks only. Those
witnesses never become a claimed transcription or visible replacement text.
"""
import re
from xml.etree import ElementTree as ET
from . import assemble, unit_recovery, native_text
from ._layout_flow import NOTICE_CLASSES

END = re.compile(r'[.!?][\"\u201d\u2019\)\]]*\s*$')


def image_witnesses(book):
    result = {}
    counters = {}
    for figure in book.figures:
        with unit_recovery.guard(figure['pno'],'source_image_witnesses'):
            pno = figure['pno']; index = counters.get(pno, 0)
            counters[pno] = index+1
            box = figure['bbox']
            rows = [a for a in book.artwork if a['pno'] == pno and
                    box[0] <= a['bbox'][0] and box[1] <= a['bbox'][1] and
                    a['bbox'][2] <= box[2] and a['bbox'][3] <= box[3]]
            result['images/fig_p%04d_%d.jpg' % (pno, index)] = dict(
                page=pno, text=' '.join(a['text'] for a in rows),
                bbox=list(box), kind=figure.get('found', 'figure'))
    for pno, elements in book.pages.items():
        with unit_recovery.guard(pno,'source_image_witnesses'):
            for element in elements:
                for run in element.runs:
                    if run[0] == 'glyph':
                        result[native_text.image_name(run[2])] = dict(
                            page=pno, text=run[1], bbox=run[2]['bbox'],
                            kind='glyph', reason=run[2].get('reason', 'encoding'))
    return result


def projection(node, images):
    """Source-only endpoint witness, including opaque source text atoms."""
    tag = node.tag.rsplit('}', 1)[-1]
    classes = node.get('class', '').split()
    if 'reflow-flagged-reading' in classes:
        # The attached print repeats the plausible reading; its spacing is
        # presentation, not a new source-word boundary.
        live=next((c for c in node if 'reflow-plausible' in c.get('class','').split()),None)
        return ((live.text or '')+''.join(projection(c,images)for c in live) if live is not None else '')+(node.tail or '')
    if 'source-glyph-presentation' in classes:
        # The visible ink expansion has a different resource identity. Its
        # sealed canonical sibling supplies the same source word witness.
        original = next((e for e in node.iter() if
                         'source-glyph-original' in e.get('class', '').split()), None)
        image = next((e for e in original.iter() if e.tag.rsplit('}', 1)[-1] == 'img'), None) if original is not None else None
        return (images.get(image.get('src'), {}).get('text', '') if image is not None else '')+(node.tail or '')
    if set(classes).intersection(NOTICE_CLASSES | {'source-reading-annotation',
                                                 'source-glyph-original', 'pdf-return'}):
        return node.tail or ''
    if tag == 'img':
        src = node.get('src', '')
        return images.get(src, {}).get('text', '')+(node.tail or '')
    if tag == 'sup' or 'noteref' in classes:
        return node.tail or ''
    return (node.text or '')+''.join(projection(c, images) for c in node)+(node.tail or '')


def finish(chapters, book):
    """Retain disclosures and notes after the complete body flow.

    Run after admission and source joins, before link binding. Note IDs and
    source-page destinations survive, so existing links bind across chapters.
    A figure's descriptive prose can finish while a body sentence is suspended
    across it. Local punctuation therefore cannot release retained disclosures.
    """
    from .build_epub import _is_aside
    images = image_witnesses(book)
    pending = []; moved = 0; note_count = 0; raster_note_count = 0
    for chapter in chapters:
        kept = []
        for block in chapter.blocks:
            # Bound note ownership is authorized by the original canonical
            # markup. Annotation extraction can rename the epub namespace
            # prefix during serialization without changing that ownership.
            controller=unit_recovery.current()
            with unit_recovery.guard([], 'reading_flow_relocation', units=controller.fragment_owners(block) if controller else None):
                is_aside = _is_aside(block)
                root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+block+'</root>')
                if not len(root):
                    kept.append(block); continue
                node = root[0]
                classes = node.get('class', '').split()
                is_notice = bool(NOTICE_CLASSES.intersection(classes))
                # Alternative-reading annotations are generated copy too. Preserve
                # their IDs and inspection links, but never insert them in a word.
                parents = {c: p for p in root.iter() for c in p}
                changed = False
                for child in list(root.iter()):
                    if (is_notice or
                            'source-reading-annotation' not in child.get('class', '').split()):
                        continue
                    parent = parents[child]; index = list(parent).index(child)
                    tail = child.tail or ''; child.tail = None
                    parent.remove(child)
                    if index:
                        previous = parent[index-1]; previous.tail = (previous.tail or '')+tail
                    else:
                        parent.text = (parent.text or '')+tail
                    notice = ET.Element('p', {'class': 'source-evidence-notice'})
                    notice.append(child)
                    pending.append(ET.tostring(notice, encoding='unicode')); moved += 1
                    changed = True
                if changed:
                    block = ''.join(ET.tostring(c, encoding='unicode') for c in root)
                if node.tag.rsplit('}', 1)[-1] == 'aside' and any(
                        e.tag.rsplit('}', 1)[-1] == 'img' for e in node.iter()):
                    # Uncertain printed notes retain their pixels in the notes
                    # section, never as rasters in the main reading flow. IDs and
                    # embedded Return actions remain available to link binding.
                    pending.append(block); moved += 1; note_count += 1
                    raster_note_count += 1
                    continue
                if 'source-native-returns' in classes:
                    # The action retains its href; its body destination span stays
                    # in place even when the action is deferred across chapters.
                    pending.append(block); moved += 1; continue
                if is_aside or 'reflow-retained-furniture' in classes:
                    pending.append(block)
                    moved += 1; note_count += 1; continue
                if is_notice:
                    pending.append(block); moved += 1; continue
                kept.append(block)
        chapter.blocks = kept
    if pending and chapters:
        # Keep every retained channel in encounter order, outside the body
        # lane. Source text and destination anchors remain in their own order.
        chapters[-1].blocks.append('<section class="source-notes" epub:type="endnotes" '
            'aria-label="Notes and source disclosures">'+''.join(pending)+'</section>')
    return dict(version='source-reading-flow-2', moved_blocks=moved,
                notes_checked=note_count, trailing_disclosures=len(pending)-raster_note_count,
                raster_notes_in_notes_section=raster_note_count,
                image_witnesses=images)
