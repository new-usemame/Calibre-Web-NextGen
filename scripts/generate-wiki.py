#!/usr/bin/env python3
"""
Deterministic GitHub-wiki generator for Calibre-Web-NextGen.

The wiki is a BUILD ARTIFACT of the repo's own docs, not a hand-maintained
mirror. That is the drift-prevention: the substantive prose lives once (in
repo/README.md and repo/docs/), and each wiki page is a template that
TRANSCLUDES the relevant sections. Re-running this on every release keeps the
wiki in lock-step with the README at near-zero token cost.

Templates live in wiki-src/*.md and contain:
  - static content (page title, intro line, nav, tables), plus
  - transclusion directives:
        {{repo:README.md#some-slug}}          -> section BODY only (no heading)
        {{repo:README.md#some-slug|heading}}  -> section rendered under an H2
                                                 heading (for multi-section pages)
        {{repo:docs/file.md#__document__}}    -> the complete source document
Pulling a section also pulls its nested sub-sections.

Link rewriting (transcluded content only):
  - `](#slug)` anchors  -> the wiki page that owns that section
  - `](relative/path)`  -> absolute blob|tree/main URL (blob vs tree decided by
                            looking at the real repo filesystem)
  - `[[Wiki Links]]` and absolute URLs are left untouched.

DRIFT TRIPWIRE: if a source doc grows a heading that no template consumes (and
that isn't on IGNORED_SLUGS), generation FAILS. That failure is the signal for
a human/agent to decide where the new content belongs — the one place AI
judgment is needed. Ordinary releases add no new sections and cost nothing.

Usage:
  generate-wiki.py [--repo <repo dir>] [--src <wiki-src dir>] [--out <output dir>]

With no arguments, paths default to the repository root, ``wiki-src/``, and
``wiki-generated/`` respectively.
Exit codes: 0 ok, 1 tripwire/other error, 2 bad args.
"""
import argparse
import os
import re
import sys

REPO_HTTP = "https://github.com/new-usemame/Calibre-Web-NextGen"

# Source-doc sections deliberately NOT mirrored into the wiki. Keep this short
# and commented — the tripwire relies on it being an honest, conscious list.
IGNORED_SLUGS = {
    # In-README nav, replaced by the wiki sidebar.
    "README.md#table-of-contents",
    # Auto-generated every push to main by generate_translation_status.py and
    # mirrored to its own wiki page by the update-translations CI workflow; the
    # Contributing page links to the live table instead of freezing a copy.
    "README.md#translations",
}

# Wiki pages this generator does NOT own (managed elsewhere). sync-wiki.sh
# overlays generated files and must never delete these.
FOREIGN_PAGES = {"Contributing-Translations.md"}

DIRECTIVE_RE = re.compile(r"\{\{repo:([^#}|]+)#([^}|]+?)(\|heading)?\}\}")


def gh_slug(text):
    """Reproduce GitHub's heading-anchor slug algorithm."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)   # \w keeps [a-z0-9_]; drop other punctuation
    s = s.replace(" ", "-")          # each space -> a hyphen (runs NOT collapsed)
    return s


class Heading:
    __slots__ = ("level", "title", "slug", "start", "body_end", "children")

    def __init__(self, level, title, slug, start):
        self.level, self.title, self.slug, self.start = level, title, slug, start
        self.body_end = None
        self.children = []


def parse_headings(lines):
    heads, in_fence = [], False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("```") or s.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
        if m:
            heads.append(Heading(len(m.group(1)), m.group(2),
                                 gh_slug(m.group(2)), i))
    n = len(lines)
    for idx, h in enumerate(heads):
        end = n
        for j in range(idx + 1, len(heads)):
            if heads[j].level <= h.level:
                end = heads[j].start
                break
        h.body_end = end
    stack, roots = [], []
    for h in heads:
        while stack and stack[-1].level >= h.level:
            stack.pop()
        (stack[-1].children if stack else roots).append(h)
        stack.append(h)
    return heads, roots


def descendants(h):
    out = [h]
    for c in h.children:
        out += descendants(c)
    return out


class RepoDoc:
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.lines = f.read().split("\n")
        self.heads, self.roots = parse_headings(self.lines)
        self.by_slug = {}
        for h in self.heads:
            self.by_slug.setdefault(h.slug, h)

    def preamble_end(self):
        # The masthead is everything before the first real content section
        # (first H2+). A leading `# Title` H1, if present, is part of it.
        for h in self.heads:
            if h.level >= 2:
                return h.start
        return len(self.lines)

    def preamble_heads(self):
        end = self.preamble_end()
        return [h for h in self.heads if h.start < end]

    def body(self, slug):
        if slug == "__document__":
            h = Heading(1, "", "__document__", -1)
            return h, "\n".join(self.lines).strip("\n")
        if slug == "__preamble__":
            end = self.preamble_end()
            h = Heading(1, "", "__preamble__", -1)
            raw = "\n".join(self.lines[0:end]).strip("\n")
            raw = re.sub(r"\n\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", raw).strip("\n")
            return h, raw
        h = self.by_slug[slug]
        body = "\n".join(self.lines[h.start + 1:h.body_end]).strip("\n")
        # README separates top-level sections with a `---` rule; strip a single
        # trailing horizontal rule so it doesn't collide with the wiki
        # template's own separators.
        body = re.sub(r"\n\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", body).strip("\n")
        return h, body


def make_link_rewriter(current_page, owner, docroot, repo_dir):
    def is_external(t):
        return bool(re.match(r"^[a-z][a-z0-9+.-]*://", t)) or \
            t.startswith(("#", "/", "mailto:", "data:"))

    def abs_url(target, image):
        """Relative repo path -> absolute GitHub URL. Images use /raw/ so they
        render inline; files use blob, directories use tree."""
        frag = ""
        if "#" in target:
            target, frag = target.split("#", 1)
            frag = "#" + frag
        rel = os.path.normpath(os.path.join(docroot, target))
        if image:
            return f"{REPO_HTTP}/raw/main/{rel}{frag}"
        on_disk = os.path.join(repo_dir, rel)
        if os.path.isdir(on_disk):
            kind = "tree"
        elif os.path.isfile(on_disk):
            kind = "blob"
        else:
            kind = "blob" if "." in rel.split("/")[-1] else "tree"
        return f"{REPO_HTTP}/{kind}/main/{rel}{frag}"

    def anchor_sub(m):
        label, slug = m.group(1), m.group(2)
        info = owner.get(slug)
        if info is None:          # same-page static anchor (template-authored)
            return m.group(0)
        page, has_heading = info
        if page == current_page:
            return m.group(0)
        frag = "#" + slug if has_heading else ""
        return f"[{label}]({REPO_HTTP}/wiki/{page}{frag})"

    def md_sub(m):
        bang, label, target = m.group(1), m.group(2), m.group(3)
        if is_external(target):
            return m.group(0)
        return f"{bang}[{label}]({abs_url(target, image=bool(bang))})"

    def attr_sub(m):
        attr, target = m.group(1), m.group(2)
        if is_external(target):
            return m.group(0)
        return f'{attr}="{abs_url(target, image=(attr == "src"))}"'

    def rewrite(text):
        # Markdown anchor links first, then markdown links/images, then raw HTML
        # attributes (the README masthead uses <img src=...>).
        text = re.sub(r"\[([^\]]+)\]\(#([\w-]+)\)", anchor_sub, text)
        text = re.sub(r"(?<!\])(!?)\[([^\]]+)\]\(([^)]+)\)", md_sub, text)
        text = re.sub(r'\b(src|href)="([^"]+)"', attr_sub, text)
        return text
    return rewrite


def main():
    ap = argparse.ArgumentParser()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--repo", default=project_root)
    ap.add_argument("--src", default=os.path.join(project_root, "wiki-src"))
    ap.add_argument("--out", default=os.path.join(project_root, "wiki-generated"))
    args = ap.parse_args()

    src_files = sorted(f for f in os.listdir(args.src) if f.endswith(".md"))
    if not src_files:
        print("ERROR: no *.md templates in", args.src, file=sys.stderr)
        return 2

    docs = {}

    def get_doc(rel):
        if rel not in docs:
            full = os.path.join(args.repo, rel)
            if not os.path.isfile(full):
                print(f"ERROR: transclusion source missing: {rel}", file=sys.stderr)
                sys.exit(1)
            docs[rel] = RepoDoc(full)
        return docs[rel]

    templates = {}
    directives = {}   # page -> list of (raw, path, slug, heading)
    owner = {}        # slug -> (page, has_visible_heading)
    consumed = {}     # docpath -> set(consumed slugs incl. descendants)

    # Pass 1: parse templates, resolve directives, compute ownership.
    for fname in src_files:
        page = fname[:-3]
        with open(os.path.join(args.src, fname), encoding="utf-8") as f:
            templates[page] = f.read()
        directives[page] = [
            (m.group(0), m.group(1).strip(), m.group(2).strip(), bool(m.group(3)))
            for m in DIRECTIVE_RE.finditer(templates[page])
        ]
        for _, path, slug, heading in directives[page]:
            doc = get_doc(path)
            if slug == "__preamble__":
                cons = consumed.setdefault(path, set())
                cons.add(slug)
                owner[slug] = (page, False)
                for ph in doc.preamble_heads():   # a leading H1 title, if any
                    cons.add(ph.slug)
                    owner.setdefault(ph.slug, (page, False))
                continue
            if slug == "__document__":
                cons = consumed.setdefault(path, set())
                cons.add(slug)
                for dh in doc.heads:
                    cons.add(dh.slug)
                    owner.setdefault(dh.slug, (page, True))
                continue
            if slug not in doc.by_slug:
                print(f"ERROR: {fname}: no section '#{slug}' in {path}",
                      file=sys.stderr)
                return 1
            top = doc.by_slug[slug]
            for d in descendants(top):
                consumed.setdefault(path, set()).add(d.slug)
                owner[d.slug] = (page, True if d.slug != slug else heading)

    # Pass 2: render each page once with the complete owner map.
    os.makedirs(args.out, exist_ok=True)
    for fname in src_files:
        page = fname[:-3]
        text = templates[page]
        for raw, path, slug, heading in directives[page]:
            doc = get_doc(path)
            h, body = doc.body(slug)
            rewrite = make_link_rewriter(page, owner, os.path.dirname(path),
                                         args.repo)
            body = rewrite(body)
            block = f"## {h.title}\n\n{body}" if heading else body
            text = text.replace(raw, block)
        with open(os.path.join(args.out, fname), "w", encoding="utf-8") as f:
            f.write(text)

    # Pass 3: DRIFT TRIPWIRE.
    problems = []
    for path, doc in docs.items():
        cons = consumed.get(path, set())

        def satisfied(h):
            key = f"{path}#{h.slug}"
            if h.slug in cons or key in IGNORED_SLUGS:
                return True
            return bool(h.children) and all(satisfied(c) for c in h.children)

        for h in doc.heads:
            if h.level <= 3 and not satisfied(h):
                problems.append(f'{path}#{h.slug}  ("{h.title}")')

    if problems:
        print("DRIFT TRIPWIRE: source sections not mirrored into any wiki page "
              "and not on the ignore list:", file=sys.stderr)
        for p in sorted(set(problems)):
            print("  -", p, file=sys.stderr)
        print("\nAdd a {{repo:...}} directive to the right wiki-src/*.md page, "
              "or add the slug to IGNORED_SLUGS with a reason, then re-run.",
              file=sys.stderr)
        return 1

    pages = [f for f in src_files if not f.startswith("_")]
    print(f"OK: rendered {len(src_files)} files "
          f"({len(pages)} pages + sidebar/footer) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
