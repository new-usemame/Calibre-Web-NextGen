"""SOURCE visibility and no invented selected-note binding under public recovery."""
import io,posixpath,zipfile
from xml.etree import ElementTree as ET
from PIL import Image
NS={'x':'http://www.w3.org/1999/xhtml'}
def source_visible(path,report,expected_pages):
    units=report.get('degraded_units',[])
    assert units,'public recovery must report affected SOURCE unit'
    assert set(map(int,report['page_homes']))==set(expected_pages)
    with zipfile.ZipFile(path) as z:
        roots={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith('.xhtml')}
        shown={posixpath.normpath(posixpath.join(posixpath.dirname(n),img.get('src'))) for n,root in roots.items() for img in root.findall('.//x:img',NS)}
        for unit in units:
            assert unit.get('stage') and unit.get('reason')
            src=unit['source_image'];member=src if src.startswith('OEBPS/') else 'OEBPS/'+src
            assert member in z.namelist() and member in shown,'SOURCE unit must actually display original image'
            assert Image.open(io.BytesIO(z.read(member))).convert('L').getextrema()[0]<100,'SOURCE unit must preserve original ink'
        controls=[a for root in roots.values() for a in root.findall('.//x:a',NS) if 'source-disclosure-ref' in (a.get('class') or '').split()]
        for unit in units:
            if unit.get('unit_kind')=='footnote':
                targets=[a for root in roots.values() for a in root.findall('.//x:aside',NS) if a.get('id')==unit['source_reference'] and 'vision-disclosure' in (a.get('class') or '').split()]
                assert len(targets)==1 and targets[0].find('.//x:img',NS) is not None,'SOURCE note must visibly disclose its actual retained image without inventing an origin'
                assert ''.join(targets[0].itertext()).strip(),'SOURCE note must display an explicit reason'
            else:
                assert controls,'affected non-note unit requires a visible source control'


def local_ocr_visible(path, report, printed_phrases, *, non_local_pages=(), no_note_refs=True):
    """Require unverified live OCR and its own source/return controls.

    The caller supplies phrases written into the generated source pixels. Neither
    a rejected answer nor a rejected cache entry supplies an expected reading.
    Contiguous uncertain words share one source control at the end of their run.
    """
    import re
    from collections import Counter

    lexical = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
    pages = {int(number): page for number, page in report['pages'].items()}
    for number in non_local_pages:
        assert not pages[number].get('local_ocr'), 'healthy cached neighbors keep their accepted reading'
    with zipfile.ZipFile(path) as archive:
        roots = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                 if name.endswith('.xhtml')}
        indexed = {}
        for name, root in roots.items():
            for element in root.iter():
                if element.get('id'):
                    key = (name, element.get('id'))
                    assert key not in indexed, 'source and return targets must be unique'
                    indexed[key] = element
        def destination(name, href):
            assert href and '#' in href, 'a source control requires a working fragment target'
            document, fragment = href.split('#', 1)
            return (posixpath.normpath(posixpath.join(posixpath.dirname(name), document))
                    if document else name, fragment)
        def image_member(name, element, number, *, complete=False, centroids=()):
            member = posixpath.normpath(posixpath.join(posixpath.dirname(name), element.get('src')))
            assert member in archive.namelist(), 'owned source evidence must be packaged'
            image_key = member.removeprefix('OEBPS/')
            metadata = report['images'][image_key]
            other = metadata['page']
            if other != number:
                # Complete pages with identical rasters may share encoded bytes.
                # The canonical metadata has one page, so prove the alias from
                # both independently source-bound OCR image identities instead.
                assert complete, 'detail evidence belongs to its own physical page'
                assert pages[other].get('local_ocr') is True, 'a resource alias requires an independently identified local source raster'
                ours = pages[number]['local_ocr_evidence']
                theirs = pages[other]['local_ocr_evidence']
                assert ours['image'] == theirs['image'] and ours['rect'] == theirs['rect']
                peer = [(key, node) for key, node in indexed.items()
                        if key[1] == f'v4-local-ocr-source-{other}']
                assert len(peer) == 1
                peer_key, peer_node = peer[0]
                peer_images = peer_node.findall('.//x:img', NS)
                assert len(peer_images) == 1
                assert posixpath.normpath(posixpath.join(posixpath.dirname(peer_key[0]),
                    peer_images[0].get('src'))) == member
            if complete:
                assert metadata['bbox'] == pages[number]['local_ocr_evidence']['rect']
            else:
                # A page id alone cannot distinguish two paragraphs on that page.
                # The crop may include surrounding context, but it must contain
                # every printed word center belonging to its own uncertain run.
                box = metadata['bbox']
                assert centroids and all(box[0] <= x <= box[2] and box[1] <= y <= box[3]
                    for x, y in centroids), 'detail crop must contain its own source-word centroids'
            with Image.open(io.BytesIO(archive.read(member))) as image:
                assert image.convert('L').getextrema()[0] < 100, 'source evidence retains real printed ink'
            return member
        result = {}
        for number, phrases in printed_phrases.items():
            page = pages[number]
            assert page.get('local_ocr') is True and page.get('unverified_source_reading') is True
            assert page['source'] == 'vision' and page['words'] > 0
            assert not any(unit['page'] == number and unit.get('block') is None
                           for unit in report.get('degraded_units', [])), 'legible local text cannot vanish into a whole-page image'
            complete = [(key, node) for key, node in indexed.items()
                        if key[1] == f'v4-local-ocr-source-{number}']
            assert len(complete) == 1, 'local reading preserves its complete original page'
            full_key, full = complete[0]
            full_images = full.findall('.//x:img', NS)
            assert len(full_images) == 1
            full_member = image_member(full_key[0], full_images[0], number, complete=True)
            live_text = []
            checked_runs = 0
            for block in page['blocks']:
                tokens = block.get('published_tokens', [])
                if not tokens:
                    continue
                units = [unit for unit in page['local_ocr_units'] if unit['id'] == block['id']]
                assert len(units) == 1, 'the local block has exactly one source-word owner'
                source_words = units[0]['words']
                originals = [word['text'] for word in source_words]
                assert len(originals) == len(tokens), 'a local repair preserves one source occurrence per word'
                corrected = set()
                for index, (original, chosen) in enumerate(zip(originals, tokens)):
                    if original == chosen:
                        continue
                    assert units[0].get('published_words') == tokens
                    decisions = [d for d in block['adjudication']
                                 if d['status'] == 'RESOLVED'
                                 and d['source'] == [original] and d['chosen'] == [chosen]]
                    assert len(decisions) == 1, 'each changed local word requires its own positive pixel receipt'
                    corrected.add(index)
                marked = set(block['marked'])
                lexical_indices = {i for i, token in enumerate(tokens) if lexical.search(token)}
                assert lexical_indices and lexical_indices <= marked, 'every local lexical token is marked unverified'
                owned = [(name, node) for name, root in roots.items() for node in root.iter()
                         if node.get('data-source-page') == str(number)
                         and node.get('data-source-block') == block['id']]
                assert len(owned) == 1, 'a live OCR block is published exactly once in its source ownership span'
                name, owner = owned[0]
                uncertain = [node for node in owner.iter()
                             if 'uncertain' in (node.get('class') or '').split()]
                text = ''.join(owner.itertext())
                from tests.unit.test_round4_defects import plain
                live_text.append(plain(owner))
                assert Counter(lexical.findall(text)) == Counter(
                    word for token in tokens for word in lexical.findall(token)
                ), 'the live reading preserves every recorded local lexical token'
                assert Counter(lexical.findall(text)) == Counter(
                    word for node in uncertain for word in lexical.findall(''.join(node.itertext()))
                ), 'all rendered local words remain inside uncertainty markup'
                assert all(node.get('title') == 'Unverified local OCR reading' for node in uncertain)
                # Each contiguous marked run owns its own control and return;
                # checking any unrelated link elsewhere would miss misbinding.
                ends = [i for i in sorted(marked)
                        if i + 1 not in marked or i in corrected or i + 1 in corrected]
                for end in ends:
                    ident = f'v4-d-{number}-{block["id"]}-{end}'
                    origin = (name, 'r-' + ident)
                    assert origin in indexed and indexed[origin] in list(owner.iter())
                    control = indexed[origin]
                    if control.get('title')=='Compare this reading with original print':
                        assert control.tag.endswith('}a')
                        assert control.get('href')=='#'+ident
                        assert ''.join(control.itertext()).strip(), 'The origin is the live reading itself'
                        target=destination(name,control.get('href'))
                    else:
                        assert 'source-disclosure-ref' in (control.get('class') or '').split()
                        target = destination(name, control.get('href'))
                    assert target in indexed and target[1] == ident
                    aside = indexed[target]
                    assert aside.tag.endswith('}aside') and 'vision-disclosure' in (aside.get('class') or '').split()
                    note_text = ''.join(aside.itertext())
                    assert 'Unverified local OCR reading' in note_text
                    if end in corrected:
                        assert 'pixel-supported correction' in note_text
                        assert originals[end] + ' → ' + tokens[end] in note_text
                    start = end
                    while start > 0 and start not in corrected and start - 1 in marked and start - 1 not in corrected:
                        start -= 1
                    centroids = [((word['bbox'][0] + word['bbox'][2]) / 2,
                                  (word['bbox'][1] + word['bbox'][3]) / 2)
                                 for word in source_words[start:end + 1]
                                 if lexical.search(word['text'])]
                    images = aside.findall('.//x:img', NS)
                    assert images
                    for image in images:
                        image_member(target[0], image, number, centroids=centroids)
                    links = aside.findall('.//x:a', NS)
                    assert any(destination(target[0], back.get('href')) == origin
                               for back in links if '#' in (back.get('href') or '')), 'source evidence returns to its own uncertain run'
                    assert any(destination(target[0], link.get('href')) == full_key
                               for link in links if '#' in (link.get('href') or '')), 'enlarged evidence opens its own complete original page'
                    checked_runs += 1
                if no_note_refs:
                    assert not [node for node in owner.iter()
                                if 'noteref' in (node.get('class') or '').split()], 'local OCR cannot invent a selected-note association'
            assert checked_runs and live_text
            visible = ' '.join(' '.join(live_text).split())
            for phrase in phrases:
                assert ' '.join(phrase.split()) in visible, ('printed source phrase must remain live', number, phrase, visible)
            result[number] = dict(full_image=full_member, full_target=full_key,
                                  live_text=visible, checked_runs=checked_runs)
        return result
