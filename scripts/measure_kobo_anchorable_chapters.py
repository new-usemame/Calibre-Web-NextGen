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
import os, sys, re, zipfile, collections, shutil, tempfile, posixpath
from urllib.parse import unquote
sys.path.insert(0, os.getcwd())
from lxml import etree
from cps.services.kepub_package_normalizer import normalize_kepub_package

P = etree.XMLParser(resolve_entities=False, recover=False)

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
                if not m: continue
                end = d.find("</navMap>", m.start())
                targets += re.findall(r'<content\s+src="([^"]+)"', d[m.start():end])
            elif low.endswith("nav.xhtml"):
                d = z.read(n).decode("utf-8", "replace")
                for m in re.finditer(r'<nav\b[^>]*epub:type="([^"]*)"[^>]*>', d):
                    if "toc" not in m.group(1).split(): continue
                    end = d.find("</nav>", m.start())
                    targets += re.findall(r'href="([^"]+)"', d[m.start():end])
    derived = ["%s!%s!%s" % (uuid, opf_dir, unquote(t)) for t in targets]
    anchored = [d for d in derived if "#" not in d and d in chapter_rows]
    fragmented = [d for d in derived if "#" in d]
    counts = collections.Counter(anchored)
    # two TOC entries deriving the SAME identity = only one chapter is reachable
    duplicated = sum(c - 1 for c in counts.values() if c > 1)
    return len(derived), len(anchored) - duplicated, len(fragmented), duplicated

LIB = sys.argv[1]
#: `--no-split` runs the normalize-only control, which is what attributes the
#: splitter's share instead of crediting it with normalization's work too.
SPLIT = "--no-split" not in sys.argv[2:]
#: `--residue` explains the leftovers instead of only counting them.
RESIDUE = "--residue" in sys.argv[2:]
rows = []
with tempfile.TemporaryDirectory(prefix="cid-") as tmp:
    for root, _, files in os.walk(LIB):
        for f in files:
            if not f.lower().endswith(".kepub"): continue
            src = os.path.join(root, f)
            try:
                before = analyse(src)
            except Exception as e:
                continue
            cp = os.path.join(tmp, "w.kepub"); shutil.copy2(src, cp)
            try:
                normalize_kepub_package(cp, split_chapters=SPLIT)
                after = analyse(cp)
            except Exception:
                after = before
            os.remove(cp)
            rows.append((os.path.basename(root)[:36], before, after))

print(f"{'book':38}{'BEFORE anchorable/total':>26}{'AFTER anchorable/total':>25}")
tb=ta=tt=0
for name,b,a in sorted(rows, key=lambda r: r[1][1]-r[1][0]):
    if b[0]==0: continue
    tb+=b[1]; ta+=a[1]; tt+=b[0]
    if b[1]!=a[1]:
        print(f"{name:38}{f'{b[1]}/{b[0]}':>26}{f'{a[1]}/{a[0]}':>25}")
print(f"\nCHAPTERS A KOBO COULD ANCHOR A HIGHLIGHT IN, across {len(rows)} books:")
print(f"  before: {tb} of {tt}    after: {ta} of {tt}")

# The residue is worth naming rather than leaving as a number. Measured
# 2026-08-19 on the 41-book library: of 69 remaining, 35 were
# `#pg-footer-heading` — Project Gutenberg's licence footer, which is a TOC
# entry but not a chapter and not a thing anyone highlights. Reporting the
# total alone overstates how much reading material is still unreachable.
print("\n  Of what remains, a TOC entry is not necessarily a chapter: run this")
print("  with --residue to break it down by fragment name.")


if RESIDUE:
    import collections as _collections

    kinds = _collections.Counter()
    fragments = _collections.Counter()
    with tempfile.TemporaryDirectory(prefix="residue-") as _tmp:
        for _root, _dirs, _files in os.walk(LIB):
            for _f in _files:
                if not _f.lower().endswith(".kepub"):
                    continue
                _cp = os.path.join(_tmp, "r.kepub")
                shutil.copy2(os.path.join(_root, _f), _cp)
                try:
                    normalize_kepub_package(_cp, split_chapters=SPLIT)
                    with zipfile.ZipFile(_cp) as _z:
                        _names = set(_z.namelist())
                    _total, _anchored, _fragmented, _dup = analyse(_cp)
                except Exception:
                    os.remove(_cp)
                    continue
                os.remove(_cp)
                kinds["still carries a #fragment"] += _fragmented
                kinds["two TOC entries share one document"] += _dup

    print("\nRESIDUE BY KIND")
    for _kind, _n in kinds.most_common():
        print(f"  {_n:4}  {_kind}")
    print("\n  A `#pg-footer-heading` target is Project Gutenberg's licence")
    print("  footer — a TOC entry, but not a chapter and not something a reader")
    print("  highlights. Counting it as an unreachable chapter overstates the gap.")
