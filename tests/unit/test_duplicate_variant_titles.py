"""Variant-title duplicates: one book catalogued under two different titles.

The normalizer keeps every word and number, so a book held twice under two
different titles never grouped: sources disagree about whether to append the
series, the volume or the imprint. Measured on a 202-book library, the scan
returned 54 groups, every one an exact title+author collision and not a single
variant pair.

These pin both directions, because the widened key is only safe with the volume
filter that accompanies it — duplicate resolution DELETES books:

  - RECALL: "Golden Son" == "Golden Son (Red Rising Series Book 2)",
    "The Road" == "The Road (Vintage International)", and "Liu, Cixin" ==
    "Cixin Liu" so the author order two sources disagree on still groups;
  - PRECISION (data-safety core): sibling volumes that share a stem must NEVER
    group — including the pre-existing false positive where Calibre keeps the
    volume only in series_index and both siblings carry one identical title;
  - the ambiguity rule: inside a stem spanning several volumes, a copy that
    declares no volume is excluded from every group rather than risked;
  - group-hash stability: a single-group title keeps the hash it had, so
    existing dismissals survive, while a split pair hashes apart.

RED on main (no stem, no author reordering, no volume filter); GREEN on branch.
"""
from cps.duplicates import (
    canonical_author_key,
    canonical_title_stem,
    declared_volume,
    generate_group_hash,
    normalize_title_for_duplicates,
    split_on_volume_conflict,
)
from cps import duplicate_index


def _groups(books):
    """Group (title, author, series, series_index) tuples the way the scan does."""
    buckets = {}
    for index, (title, author, series, series_index) in enumerate(books):
        key = (normalize_title_for_duplicates(title, author), canonical_author_key(author))
        buckets.setdefault(key, []).append(
            (index, declared_volume(title, series_index, bool(series)))
        )
    out = []
    for members in buckets.values():
        out.extend([g for g in split_on_volume_conflict(members) if len(g) > 1])
    return out


# --- RECALL: the same book written two ways collapses -----------------------

def test_series_annotation_is_not_part_of_the_title():
    assert canonical_title_stem("Golden Son (Red Rising Series Book 2)") == \
           canonical_title_stem("Golden Son")


def test_imprint_annotation_is_not_part_of_the_title():
    # No marker word here, so this relies on the end-anchored trailing strip.
    assert canonical_title_stem("The Road (Vintage International)") == \
           canonical_title_stem("The Road")


def test_bracketed_series_tag_is_not_part_of_the_title():
    assert canonical_title_stem("[Wayward Pines 01] Pines") == canonical_title_stem("Pines")


def test_author_order_does_not_matter():
    assert canonical_author_key("Liu, Cixin") == canonical_author_key("Cixin Liu")


def test_author_watermarks_are_ignored():
    # A real record: a bracketed repeat of the name plus a scraper's domain.
    assert canonical_author_key("Liu, Cixin [Liu, Cixin] || chenjin5.com") == \
           canonical_author_key("Cixin Liu")


def test_same_book_two_titles_groups():
    assert len(_groups([
        ("Golden Son", "Pierce Brown", "Red Rising", 2),
        ("Golden Son (Red Rising Series Book 2)", "Pierce Brown", "Red Rising", 2),
    ])) == 1


def test_thinner_metadata_still_groups():
    # One copy imported without series metadata: "declares nothing" is not
    # "declares volume 1", so it must still group with its twin.
    assert len(_groups([
        ("Summer Frost", "Blake Crouch", "Forward", 1),
        ("Summer Frost", "Blake Crouch", None, None),
    ])) == 1


# --- PRECISION: distinct books must never group -----------------------------

def test_sibling_volumes_sharing_a_stem_never_group():
    assert _groups([
        ("Wayward Pines (Book 1)", "Blake Crouch", "Wayward Pines", 1),
        ("Wayward Pines (Book 2)", "Blake Crouch", "Wayward Pines", 2),
    ]) == []


def test_identically_titled_siblings_never_group():
    # Calibre keeps the volume ONLY in series_index here, so these two share an
    # identical title and grouped together before the volume filter existed —
    # auto-resolve would have deleted one.
    assert _groups([
        ("Wayward Pines", "Blake Crouch", "Wayward Pines", 1),
        ("Wayward Pines", "Blake Crouch", "Wayward Pines", 2),
    ]) == []


def test_subtitled_siblings_never_group():
    assert _groups([
        ("Mistborn: The Final Empire", "Brandon Sanderson", "Mistborn", 1),
        ("Mistborn: The Well of Ascension", "Brandon Sanderson", "Mistborn", 2),
    ]) == []


def test_numbered_titles_keep_their_number():
    # The volume LABEL is dropped but the NUMBER survives into the stem.
    assert canonical_title_stem("Wool Book 12") != canonical_title_stem("Wool Book 13")


def test_roman_numbered_siblings_never_group():
    assert _groups([
        ("The Dark Tower I: The Gunslinger", "Stephen King", "The Dark Tower", 1),
        ("The Dark Tower II: The Drawing of the Three", "Stephen King", "The Dark Tower", 2),
    ]) == []


def test_same_title_different_authors_never_group():
    assert _groups([
        ("Dark Matter", "Blake Crouch", None, None),
        ("Dark Matter", "Michelle Paver", None, None),
    ]) == []


def test_four_digit_numbers_are_not_volumes():
    # "1984" is a title and "2001" a year; neither may be read as a volume.
    assert declared_volume("1984") is None
    assert declared_volume("2001: A Space Odyssey") is None


def test_annotation_only_title_does_not_collapse_to_empty():
    # Otherwise every such book would share one empty stem and group together.
    assert canonical_title_stem("(Boxed Set)") != ""


# --- the ambiguity rule -----------------------------------------------------

def test_volume_ambiguous_copy_is_excluded_from_every_group():
    # Stem spans volumes 1 and 2; the third copy declares nothing, so nothing
    # says which volume it is and it must not be offered for deletion.
    partitions = split_on_volume_conflict([("a", 1), ("b", 2), ("c", None)])
    assert all("c" not in partition for partition in partitions)


def test_single_declared_volume_absorbs_undeclared_copies():
    partitions = split_on_volume_conflict([("a", 2), ("b", None)])
    assert partitions == [["a", "b"]]


# --- group-hash stability ---------------------------------------------------

def test_group_hash_unchanged_without_a_discriminator():
    # Existing dismissals must survive: an unsplit group hashes as before.
    assert generate_group_hash("Dune", "Frank Herbert") == \
           generate_group_hash("Dune", "Frank Herbert", None)


def test_split_groups_hash_apart():
    # Two groups sharing one display title would otherwise dismiss together.
    assert generate_group_hash("Wayward Pines", "Blake Crouch", 1) != \
           generate_group_hash("Wayward Pines", "Blake Crouch", 2)


# --- index surface ----------------------------------------------------------

def test_index_normalization_version_rotated():
    assert duplicate_index.NORMALIZATION_VERSION == "duplicate-index-v4"
