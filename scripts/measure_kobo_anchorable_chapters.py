"""Does the split actually give every chapter an identity a Kobo can anchor to?

#1657: Nickel renders a Bookmark only when Bookmark.ContentID exactly equals a
`content` row with ContentType=9. The chapter identity it derives from a TOC
entry is

    <book-uuid>!<opf-dir>!<href>

and the ContentType=9 row for a spine document carries the SAME shape with no
fragment. So the test of the fix is not "were files split" but: does every
chapter-TOC entry derive an identity that (a) carries no fragment and (b) matches
exactly one spine document?
"""
import collections
import os
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile
from urllib.parse import unquote

from lxml import etree

P = etree.XMLParser(resolve_entities=False, recover=False)


def _normalizer():
    """Load the application normalizer only when the instrument is executed."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    return normalize_kepub_package


def _is_anchorable(identity, chapter_rows):
    return "#" not in identity and identity in chapter_rows


def _duplicate_excess(count):
    return count - 1 if count > 1 else 0


def _is_footer_fragment(fragment):
    return "footer" in fragment.lower()


def package(z):
    c = etree.fromstring(z.read("META-INF/container.xml"), parser=P)
    opf = c.xpath("//*[local-name()='rootfile']/@full-path")[0]
    return opf, etree.fromstring(z.read(opf), parser=P)

def analyse(path):
    with zipfile.ZipFile(path) as z:
        opf_path, pkg = package(z)
        opf_dir = posixpath.dirname(opf_path)
        uuid = "BOOKUUID"
        items = {i.get("id"): unquote(i.get("href")) for i in pkg.iter("{*}item")}
        spine_hrefs = [items[r.get("idref")] for r in pkg.iter("{*}itemref") if r.get("idref") in items]
        # ContentType=9 identities the device would hold, one per spine document
        chapter_rows = {"%s!%s!%s" % (uuid, opf_dir, h) for h in spine_hrefs}

        targets = []
        for n in z.namelist():
            low = n.lower()
            if low.endswith(".ncx"):
                d = z.read(n).decode("utf-8", "replace")
                m = re.search(r"<navMap\b", d)
                if not m:
                    continue
                end = d.find("</navMap>", m.start())
                targets += re.findall(r'<content\s+src="([^"]+)"', d[m.start():end])
            elif low.endswith("nav.xhtml"):
                d = z.read(n).decode("utf-8", "replace")
                for m in re.finditer(r'<nav\b[^>]*epub:type="([^"]*)"[^>]*>', d):
                    if "toc" not in m.group(1).split():
                        continue
                    end = d.find("</nav>", m.start())
                    targets += re.findall(r'href="([^"]+)"', d[m.start():end])
    derived = ["%s!%s!%s" % (uuid, opf_dir, unquote(t)) for t in targets]
    anchored = [d for d in derived if _is_anchorable(d, chapter_rows)]
    fragmented = [d for d in derived if "#" in d]
    counts = collections.Counter(anchored)
    # two TOC entries deriving the SAME identity = only one chapter is reachable
    duplicated = sum(_duplicate_excess(c) for c in counts.values())
    # the fragment NAMES, so --residue can say WHICH targets are still unreachable
    frag_names = [d.split("#", 1)[1] for d in fragmented]
    return len(derived), len(anchored) - duplicated, len(fragmented), duplicated, frag_names


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    lib = args[0]
    # `--no-split` runs the normalize-only control, which is what attributes the
    # splitter's share instead of crediting it with normalization's work too.
    split = "--no-split" not in args[1:]
    # `--residue` explains the leftovers instead of only counting them.
    residue = "--residue" in args[1:]
    normalize_kepub_package = _normalizer()
    rows = []
    with tempfile.TemporaryDirectory(prefix="cid-") as tmp:
        for root, _, files in os.walk(lib):
            for f in files:
                if not f.lower().endswith(".kepub"):
                    continue
                src = os.path.join(root, f)
                try:
                    before = analyse(src)
                except Exception:
                    continue
                cp = os.path.join(tmp, "w.kepub")
                shutil.copy2(src, cp)
                try:
                    normalize_kepub_package(cp, split_chapters=split)
                    after = analyse(cp)
                except Exception:
                    after = before
                os.remove(cp)
                rows.append((os.path.basename(root)[:36], before, after))

    print(f"{'book':38}{'BEFORE anchorable/total':>26}{'AFTER anchorable/total':>25}")
    tb = ta = tt = 0
    for name, before, after in sorted(rows, key=lambda row: row[1][1] - row[1][0]):
        if before[0] == 0:
            continue
        tb += before[1]
        ta += after[1]
        tt += before[0]
        if before[1] != after[1]:
            print(
                f"{name:38}{f'{before[1]}/{before[0]}':>26}"
                f"{f'{after[1]}/{after[0]}':>25}"
            )
    print(f"\nCHAPTERS A KOBO COULD ANCHOR A HIGHLIGHT IN, across {len(rows)} books:")
    print(f"  before: {tb} of {tt}    after: {ta} of {tt}")

    # The residue is worth naming rather than leaving as a number. Measured
    # 2026-08-19 on the 41-book library: of 69 remaining, 35 were
    # `#pg-footer-heading` — Project Gutenberg's licence footer, which is a TOC
    # entry but not a chapter and not a thing anyone highlights. Reporting the
    # total alone overstates how much reading material is still unreachable.
    print("\n  Of what remains, a TOC entry is not necessarily a chapter: run this")
    print("  with --residue to break it down by fragment name.")

    if residue:
        kinds = collections.Counter()
        fragments = collections.Counter()
        with tempfile.TemporaryDirectory(prefix="residue-") as tmp:
            for root, _dirs, files in os.walk(lib):
                for f in files:
                    if not f.lower().endswith(".kepub"):
                        continue
                    cp = os.path.join(tmp, "r.kepub")
                    shutil.copy2(os.path.join(root, f), cp)
                    try:
                        normalize_kepub_package(cp, split_chapters=split)
                        total, anchored, fragmented, duplicate, fragment_names = analyse(cp)
                    except Exception:
                        os.remove(cp)
                        continue
                    os.remove(cp)
                    kinds["still carries a #fragment"] += fragmented
                    kinds["two TOC entries share one document"] += duplicate
                    fragments.update(fragment_names)

        print("\nRESIDUE BY KIND")
        for kind, count in kinds.most_common():
            print(f"  {count:4}  {kind}")
        print("\nRESIDUE BY FRAGMENT NAME")
        for fragment, count in fragments.most_common(15):
            print(f"  {count:4}  #{fragment}")
        footer = sum(
            count for fragment, count in fragments.items()
            if _is_footer_fragment(fragment)
        )
        if footer:
            print(
                f"\n  {footer} of {sum(fragments.values())} fragmented targets "
                "are a licence footer,"
            )
            print("  not a chapter. The real unreachable-chapter count is the remainder.")
        print("\n  A `#pg-footer-heading` target is Project Gutenberg's licence")
        print("  footer — a TOC entry, but not a chapter and not something a reader")
        print("  highlights. Counting it as an unreachable chapter overstates the gap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
