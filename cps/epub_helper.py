# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import zipfile
from copy import deepcopy
from lxml import etree

from . import isoLanguages

default_ns = {
    'n': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'pkg': 'http://www.idpf.org/2007/opf',
}

OPF_NAMESPACE = "http://www.idpf.org/2007/opf"
PURL_NAMESPACE = "http://purl.org/dc/elements/1.1/"

OPF = "{%s}" % OPF_NAMESPACE
PURL = "{%s}" % PURL_NAMESPACE

etree.register_namespace("opf", OPF_NAMESPACE)
etree.register_namespace("dc", PURL_NAMESPACE)

OPF_NS = {None: OPF_NAMESPACE}  # the default namespace (no prefix)
NSMAP = {'dc': PURL_NAMESPACE, 'opf': OPF_NAMESPACE}

_KEPUB_MANAGED_DC_ELEMENTS = {
    "identifier",
    "title",
    "creator",
    "contributor",
    "date",
    "description",
    "publisher",
    "language",
    "subject",
}
_KEPUB_MANAGED_META_NAMES = {
    "calibre:author_link_map",
    "calibre:rating",
    "calibre:series",
    "calibre:series_index",
    "calibre:timestamp",
    "calibre:title_sort",
}


def updateEpub(src, dest, filename, data, ):
    # create a temp copy of the archive without filename
    with zipfile.ZipFile(src, 'r') as zin:
        with zipfile.ZipFile(dest, 'w') as zout:
            zout.comment = zin.comment  # preserve the comment
            for item in zin.infolist():
                if item.filename != filename:
                    zout.writestr(item, zin.read(item.filename))

    # now add filename with its new data
    with zipfile.ZipFile(dest, mode='a', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, data)


def _strip_xml_leading_noise(data):
    if isinstance(data, bytes):
        data = data.lstrip(b"\xef\xbb\xbf\r\n\t ")
        xml_index = data.find(b"<?xml")
        if xml_index > 0:
            data = data[xml_index:]
    else:
        data = data.lstrip("\ufeff\r\n\t ")
        xml_index = data.find("<?xml")
        if xml_index > 0:
            data = data[xml_index:]
    return data


_safe_parser = etree.XMLParser(resolve_entities=False, no_network=True)


def get_content_opf(file_path, ns=None):
    if ns is None:
        ns = default_ns
    epubZip = zipfile.ZipFile(file_path)
    txt = epubZip.read('META-INF/container.xml')
    # Some EPUBs include a BOM or stray whitespace before the XML declaration,
    # which causes lxml to error with: "XML declaration allowed only at the start".
    txt = _strip_xml_leading_noise(txt)
    tree = etree.fromstring(txt, parser=_safe_parser)
    cf_name = tree.xpath('n:rootfiles/n:rootfile/@full-path', namespaces=ns)[0]
    cf = epubZip.read(cf_name)
    cf = _strip_xml_leading_noise(cf)

    return etree.fromstring(cf, parser=_safe_parser), cf_name


def create_new_metadata_backup(book,  custom_columns, export_language, translated_cover_name, lang_type=3):
    # generate root package element
    package = etree.Element(OPF + "package", nsmap=OPF_NS)
    package.set("unique-identifier", "uuid_id")
    package.set("version", "2.0")

    # generate metadata element and all sub elements of it
    metadata = etree.SubElement(package, "metadata", nsmap=NSMAP)
    identifier = etree.SubElement(metadata, PURL + "identifier", id="calibre_id", nsmap=NSMAP)
    identifier.set(OPF + "scheme", "calibre")
    identifier.text = str(book.id)
    identifier2 = etree.SubElement(metadata, PURL + "identifier", id="uuid_id", nsmap=NSMAP)
    identifier2.set(OPF + "scheme", "uuid")
    identifier2.text = book.uuid
    for i in book.identifiers:
        identifier = etree.SubElement(metadata, PURL + "identifier", nsmap=NSMAP)
        identifier.set(OPF + "scheme", i.format_type())
        identifier.text = str(i.val)
    title = etree.SubElement(metadata, PURL + "title", nsmap=NSMAP)
    title.text = book.title
    for author in book.authors:
        creator = etree.SubElement(metadata, PURL + "creator", nsmap=NSMAP)
        creator.text = str(author.name)
        creator.set(OPF + "file-as", book.author_sort)     # ToDo Check
        creator.set(OPF + "role", "aut")
    contributor = etree.SubElement(metadata, PURL + "contributor", nsmap=NSMAP)
    contributor.text = "calibre (5.7.2) [https://calibre-ebook.com]"
    contributor.set(OPF + "file-as", "calibre")     # ToDo Check
    contributor.set(OPF + "role", "bkp")

    date = etree.SubElement(metadata, PURL + "date", nsmap=NSMAP)
    date.text = '{d.year:04}-{d.month:02}-{d.day:02}T{d.hour:02}:{d.minute:02}:{d.second:02}'.format(d=book.pubdate)
    if book.comments and book.comments[0].text:
        for b in book.comments:
            description = etree.SubElement(metadata, PURL + "description", nsmap=NSMAP)
            description.text = b.text
    for b in book.publishers:
        publisher = etree.SubElement(metadata, PURL + "publisher", nsmap=NSMAP)
        publisher.text = str(b.name)
    if not book.languages:
        language = etree.SubElement(metadata, PURL + "language", nsmap=NSMAP)
        language.text = export_language
    else:
        for b in book.languages:
            language = etree.SubElement(metadata, PURL + "language", nsmap=NSMAP)
            language.text = str(b.lang_code) if lang_type == 3 else isoLanguages.get(part3=b.lang_code).part1
    for b in book.tags:
        subject = etree.SubElement(metadata, PURL + "subject", nsmap=NSMAP)
        subject.text = str(b.name)
    etree.SubElement(metadata, "meta", name="calibre:author_link_map",
                     content="{" + ", ".join(['"' + str(a.name) + '": ""' for a in book.authors]) + "}",
                     nsmap=NSMAP)
    for b in book.series:
        etree.SubElement(metadata, "meta", name="calibre:series",
                         content=str(str(b.name)),
                         nsmap=NSMAP)
    if book.series:
        etree.SubElement(metadata, "meta", name="calibre:series_index",
                         content=str(book.series_index),
                         nsmap=NSMAP)
    if len(book.ratings) and book.ratings[0].rating > 0:
        etree.SubElement(metadata, "meta", name="calibre:rating",
                         content=str(book.ratings[0].rating),
                         nsmap=NSMAP)
    etree.SubElement(metadata, "meta", name="calibre:timestamp",
                     content='{d.year:04}-{d.month:02}-{d.day:02}T{d.hour:02}:{d.minute:02}:{d.second:02}'.format(
                         d=book.timestamp),
                     nsmap=NSMAP)
    etree.SubElement(metadata, "meta", name="calibre:title_sort",
                     content=book.sort,
                     nsmap=NSMAP)
    sequence = 0
    for cc in custom_columns:
        value = None
        extra = None
        cc_entry = getattr(book, "custom_column_" + str(cc.id))
        if cc_entry.__len__():
            value = [c.value for c in cc_entry] if cc.is_multiple else cc_entry[0].value
            extra = cc_entry[0].extra if hasattr(cc_entry[0], "extra") else None
        etree.SubElement(metadata, "meta", name="calibre:user_metadata:#{}".format(cc.label),
                         content=cc.to_json(value, extra, sequence),
                         nsmap=NSMAP)
        sequence += 1

    # generate guide element and all sub elements of it
    # Title is translated from default export language
    guide = etree.SubElement(package, "guide")
    etree.SubElement(guide, "reference", type="cover", title=translated_cover_name, href="cover.jpg")

    return package


def replace_metadata(tree, package):
    rep_element = tree.xpath('/pkg:package/pkg:metadata', namespaces=default_ns)[0]
    new_element = package.xpath('//metadata', namespaces=default_ns)[0]
    tree.replace(rep_element, new_element)
    return etree.tostring(tree,
                          xml_declaration=True,
                          encoding='utf-8',
                          pretty_print=True).decode('utf-8')


def _local_name(element):
    tag = element.tag
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1]


def _meta_name(element):
    if _local_name(element) != "meta":
        return None
    return element.get("name")


def _is_managed_meta(element):
    name = _meta_name(element)
    property_name = (
        element.get("property")
        if _local_name(element) == "meta"
        else None
    )
    return bool(
        name in _KEPUB_MANAGED_META_NAMES
        or (name and name.startswith("calibre:user_metadata:"))
        or property_name in _KEPUB_MANAGED_META_NAMES
        or (property_name and property_name.startswith("calibre:user_metadata"))
    )


def _series_collection_ids(metadata):
    metas = [
        element for element in metadata
        if _local_name(element) == "meta"
    ]
    collection_ids = {
        element.get("id")
        for element in metas
        if element.get("property") == "belongs-to-collection"
        and element.get("id")
    }
    return {
        element.get("refines", "").removeprefix("#")
        for element in metas
        if element.get("property") == "collection-type"
        and "".join(element.itertext()).strip().lower() == "series"
        and element.get("refines", "").startswith("#")
    } & collection_ids


def _next_metadata_id(tree, base):
    used = {
        element.get("id")
        for element in tree.iter()
        if isinstance(element.tag, str) and element.get("id")
    }
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def merge_kepub_metadata(tree, package):
    """Merge library-authored metadata into an EPUB3 KEPUB package.

    ``create_new_metadata_backup`` builds an OPF2 metadata block for the
    separately stored ``metadata.opf`` backup. Replacing a KEPUB's complete
    EPUB3 block with it discards collection metadata and other refinements the
    backup builder does not own. Replace only library-managed fields, preserve
    the remaining EPUB3 children, and emit series in the collection form Kobo
    firmware consumes.
    """
    metadata = tree.xpath('/pkg:package/pkg:metadata', namespaces=default_ns)[0]
    generated = package.xpath('//metadata', namespaces=default_ns)[0]
    series_ids = _series_collection_ids(metadata)

    series_name = None
    series_index = None
    generated_children = []
    for child in generated:
        name = _meta_name(child)
        if name == "calibre:series":
            series_name = child.get("content")
            continue
        if name == "calibre:series_index":
            series_index = child.get("content")
            continue
        copied = deepcopy(child)
        if _local_name(copied) == "meta" and not copied.tag.startswith("{"):
            copied.tag = OPF + "meta"
        generated_children.append(copied)

    def is_series_child(element):
        if _meta_name(element) in {"calibre:series", "calibre:series_index"}:
            return True
        if _local_name(element) != "meta":
            return False
        return (
            element.get("id") in series_ids
            and element.get("property") == "belongs-to-collection"
        ) or element.get("refines", "").removeprefix("#") in series_ids

    referenced_ids = {
        (element.get("refines") or "").removeprefix("#")
        for element in metadata
        if not is_series_child(element)
        and (element.get("refines") or "").startswith("#")
    }
    unique_identifier = tree.get("unique-identifier")
    if unique_identifier:
        referenced_ids.add(unique_identifier)

    generated_by_name = {}
    for child in generated_children:
        generated_by_name.setdefault(_local_name(child), []).append(child)
    claimed_generated = set()
    preserved_anchors = set()
    unmatched_referenced = []
    for old_child in metadata:
        local_name = _local_name(old_child)
        old_id = old_child.get("id") if isinstance(old_child.tag, str) else None
        if local_name not in _KEPUB_MANAGED_DC_ELEMENTS or old_id not in referenced_ids:
            continue
        candidates = [
            candidate for candidate in generated_by_name.get(local_name, [])
            if id(candidate) not in claimed_generated
        ]
        old_text = "".join(old_child.itertext()).strip()
        candidate = next(
            (
                item for item in candidates
                if "".join(item.itertext()).strip() == old_text
            ),
            None,
        )
        if candidate is None:
            unmatched_referenced.append(old_child)
            continue
        candidate.set("id", old_id)
        claimed_generated.add(id(candidate))

    # Exact matches take their old ids first. Positional fallback then covers a
    # changed value without letting a removed first author steal the surviving
    # author's role/file-as refinements.
    for old_child in unmatched_referenced:
        local_name = _local_name(old_child)
        old_id = old_child.get("id")
        candidates = [
            candidate for candidate in generated_by_name.get(local_name, [])
            if id(candidate) not in claimed_generated
        ]
        if local_name != "identifier" and candidates:
            candidate = candidates[0]
            candidate.set("id", old_id)
            claimed_generated.add(id(candidate))
        elif local_name == "identifier" and old_id == unique_identifier:
            # Preserve an identity URI absent from the Calibre DB rather than
            # leaving package@unique-identifier dangling or changing meaning.
            preserved_anchors.add(old_child)

    removable = []
    for child in metadata:
        if child in preserved_anchors:
            continue
        if (
            _local_name(child) in _KEPUB_MANAGED_DC_ELEMENTS
            or _is_managed_meta(child)
            or is_series_child(child)
        ):
            removable.append(child)

    insertion_index = min(
        (metadata.index(child) for child in removable),
        default=len(metadata),
    )
    removed_ids = {
        child.get("id")
        for child in removable
        if isinstance(child.tag, str) and child.get("id")
    }
    for child in removable:
        metadata.remove(child)
    for offset, child in enumerate(generated_children):
        child_id = child.get("id") if isinstance(child.tag, str) else None
        if child_id and tree.xpath('//*[@id=$identifier]', identifier=child_id):
            replacement_id = _next_metadata_id(tree, child_id)
            child.set("id", replacement_id)
            for generated_child in generated_children:
                for element in generated_child.iter():
                    if element.get("refines") == f"#{child_id}":
                        element.set("refines", f"#{replacement_id}")
        metadata.insert(insertion_index + offset, child)

    if series_name:
        series_id = _next_metadata_id(tree, "calibre-web-series")
        collection = etree.Element(
            OPF + "meta",
            property="belongs-to-collection",
            id=series_id,
        )
        collection.text = series_name
        collection_type = etree.Element(
            OPF + "meta",
            refines=f"#{series_id}",
            property="collection-type",
        )
        collection_type.text = "series"
        series_children = [collection, collection_type]
        if series_index not in (None, ""):
            group_position = etree.Element(
                OPF + "meta",
                refines=f"#{series_id}",
                property="group-position",
            )
            group_position.text = series_index
            series_children.append(group_position)
        for offset, child in enumerate(series_children, start=len(generated_children)):
            metadata.insert(insertion_index + offset, child)

    surviving_ids = {
        element.get("id")
        for element in tree.iter()
        if isinstance(element.tag, str) and element.get("id")
    }
    orphaned_removed_ids = removed_ids - surviving_ids
    for child in list(metadata):
        refined_id = (child.get("refines") or "").removeprefix("#")
        if refined_id in orphaned_removed_ids:
            metadata.remove(child)

    return etree.tostring(
        tree,
        xml_declaration=True,
        encoding='utf-8',
        pretty_print=True,
    ).decode('utf-8')

