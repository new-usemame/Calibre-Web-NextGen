# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import struct
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


def _without_zip64_extra(extra):
    """Central ZIP64 values describe the OLD sizes/offset; zipfile rebuilds them."""
    result = bytearray()
    position = 0
    while position + 4 <= len(extra):
        tag, size = struct.unpack_from('<HH', extra, position)
        end = position + 4 + size
        if end > len(extra):
            raise zipfile.BadZipFile('Truncated ZIP extra field')
        if tag != 1:
            result.extend(extra[position:end])
        position = end
    result.extend(extra[position:])  # zipfile also accepts trailing padding
    return bytes(result)


class _RawCopyZipInfo(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        # The standard writer converts non-ASCII CP437 names to UTF-8. A raw
        # local header still uses CP437, so keep the central name/flags in sync.
        return self.orig_filename.encode('utf-8' if self.flag_bits & 0x800 else 'cp437'), self.flag_bits


def _raw_copy_info(info):
    result = _RawCopyZipInfo(info.filename, info.date_time)
    for attribute in zipfile.ZipInfo.__slots__:
        if hasattr(info, attribute):
            setattr(result, attribute, getattr(info, attribute))
    result.extra = _without_zip64_extra(info.extra)
    return result


def _raw_member_length(source, info, limit):
    """Return a validated local-record length, or None for the legacy path.

    APPNOTE 6.3.10 §§4.3.7–4.3.9, 4.4.4, 4.5.3:
    https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT
    Local extras are independent of central extras. Descriptors immediately
    follow compressed data and have 32/64-bit sizes, with/without signature.
    Never search payload bytes for a signature or infer size from the next file.
    """
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED,
                                  zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        return None
    # Refuse encryption (0), patched data (5), strong encryption (6) and
    # masked local headers (13). Compression options/EOS (1/2/4) and encoding
    # (11) do not obstruct verbatim copying; retain opaque/reserved bits too.
    # In particular, source EPUB writers commonly leave enhanced-deflate bit 4
    # set (0x816). An allow-list based on zipfile's writer needlessly decodes it.
    # Bit 3 still requires the descriptor validation below, regardless of hints.
    if info.flag_bits & ((1 << 0) | (1 << 5) | (1 << 6) | (1 << 13)):
        return None
    handle = source.fp
    handle.seek(info.header_offset)
    header = handle.read(30)
    if len(header) != 30:
        return None
    signature, _, flags, method, _, _, crc, packed, size, names, extras = struct.unpack('<4s5H3L2H', header)
    if signature != b'PK\x03\x04' or flags != info.flag_bits or method != info.compress_type:
        return None
    name = handle.read(names)
    extra = handle.read(extras)
    encoding = 'utf-8' if flags & 0x800 else 'cp437'
    try:
        if name.decode(encoding) != info.orig_filename or len(extra) != extras:
            return None
    except UnicodeError:
        return None
    # ZIP64 local sizes live in the LOCAL extra, not ZipInfo.extra (central).
    # Validate them before retaining the header; zipfile's reader can handle
    # some damaged local metadata using the central sizes on the legacy path.
    local_sizes = None
    position = 0
    while position + 4 <= len(extra):
        tag, length = struct.unpack_from('<HH', extra, position)
        if position + 4 + length > len(extra):
            return None
        if tag == 1:
            if length < 16:
                return None
            local_sizes = struct.unpack_from('<QQ', extra, position + 4)
        position += 4 + length
    if packed == 0xFFFFFFFF or size == 0xFFFFFFFF:
        if local_sizes is None:
            return None
        if not flags & 8 and local_sizes != (info.file_size, info.compress_size):
            return None
    data_end = info.header_offset + 30 + names + extras + info.compress_size
    if data_end > limit:
        return None
    if not flags & 8:
        # zipfile tolerates some inconsistent local sizes, too. Leave such
        # files to its reader instead of certifying a bad raw record.
        if crc != info.CRC:
            return None
        if packed != 0xFFFFFFFF and packed != info.compress_size:
            return None
        if size != 0xFFFFFFFF and size != info.file_size:
            return None
        return data_end - info.header_offset

    handle.seek(data_end)
    descriptor = handle.read(min(24, limit - data_end))
    # Compare the complete tuple, including CRC: a CRC can itself equal the
    # optional signature. Try both layouts rather than consuming it blindly.
    for prefix in (0, 4):
        if prefix and descriptor[:4] != b'PK\x07\x08':
            continue
        for layout in ('<LQQ', '<LLL'):
            length = struct.calcsize(layout)
            if len(descriptor) >= prefix + length and struct.unpack_from(layout, descriptor, prefix) == (
                    info.CRC, info.compress_size, info.file_size):
                return data_end + prefix + length - info.header_offset
    return None


def rewrite_zip_members(src, dest, replacements):
    """Copy a book ZIP, replacing {member_name: bytes_or_text} in source order.

    Unchanged supported members keep their local headers, compressed bytes and
    descriptors verbatim. Only replacements are compressed; bulk work is bounded
    memory file I/O. Existing metadata/comment survives, CRC/sizes are recalculated
    for replacements. Missing names are appended (new members use deflate).
    Duplicate names use the last entry's content at the first entry's position,
    matching ZipFile.read(name), without producing another ambiguous archive.

    This deliberately uses zipfile's fp/filelist/NameToInfo/start_dir/_didModify
    writer state and ZipInfo slots/filename encoder. The wire-format tests cover
    raw→writestr→raw transitions and ZIP64 output on the running Python version.
    Unsupported layouts go through the old read/writestr path, including its
    normal errors for unknown methods or password-protected members.
    src/dest accept the paths or seekable file objects accepted by ZipFile; they
    must refer to separate files. Callers own temporary-file cleanup on failure.
    """
    with zipfile.ZipFile(src, 'r') as source, zipfile.ZipFile(dest, 'w') as target:
        target.comment = source.comment
        infos = source.infolist()
        offsets = sorted({info.header_offset for info in infos} | {source.start_dir})
        limits = dict(zip(offsets, offsets[1:]))
        members = {info.filename: info for info in infos}
        names = list(members)
        names.extend(name for name in replacements if name not in members)
        if 'mimetype' in names:
            names.remove('mimetype')
            names.insert(0, 'mimetype')
        for name in names:
            info = members.get(name)
            if info is None:
                target.writestr(name, replacements[name], compress_type=(
                    zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED))
                continue
            replacement = name in replacements
            length = None if replacement else _raw_member_length(
                source, info, limits.get(info.header_offset, source.start_dir))
            if name == 'mimetype' and info.compress_type != zipfile.ZIP_STORED:
                length = None
            if length is None:
                # Use a normal ZipInfo for rewritten headers: writestr clears
                # descriptor/encryption bits and chooses its own name encoding.
                copied = deepcopy(info)
                copied.extra = _without_zip64_extra(info.extra)
                if name == 'mimetype':
                    copied.compress_type = zipfile.ZIP_STORED
                elif replacement and copied.compress_type not in (
                        zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
                    copied.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(copied, replacements[name] if replacement else source.read(info))
                continue
            copied = _raw_copy_info(info)
            copied.header_offset = target.fp.tell()
            source.fp.seek(info.header_offset)
            remaining = length
            while remaining:
                chunk = source.fp.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise zipfile.BadZipFile('Truncated ZIP member')
                target.fp.write(chunk)
                remaining -= len(chunk)
            target.filelist.append(copied)
            target.NameToInfo[name] = copied
            target.start_dir = target.fp.tell()
            target._didModify = True


def updateEpub(src, dest, filename, data):
    rewrite_zip_members(src, dest, {filename: data})


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

    # Exact matches take their old ids first. A changed value inherits an old
    # id only when one unmatched old element and one generated element remain
    # for that field. With any other cardinality the correspondence is
    # ambiguous (for example, remove one author while renaming another), so
    # dropping the old refinements is safer than attaching them to the wrong
    # value.
    unmatched_by_name = {}
    for old_child in unmatched_referenced:
        local_name = _local_name(old_child)
        if local_name == "identifier":
            old_id = old_child.get("id")
            if old_id == unique_identifier:
                # Preserve an identity URI absent from the Calibre DB rather than
                # leaving package@unique-identifier dangling or changing meaning.
                preserved_anchors.add(old_child)
        else:
            unmatched_by_name.setdefault(local_name, []).append(old_child)

    for local_name, old_children in unmatched_by_name.items():
        candidates = [
            candidate for candidate in generated_by_name.get(local_name, [])
            if id(candidate) not in claimed_generated
        ]
        if len(old_children) == 1 and len(candidates) == 1:
            old_id = old_children[0].get("id")
            candidate = candidates[0]
            candidate.set("id", old_id)
            claimed_generated.add(id(candidate))

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

    while True:
        surviving_ids = {
            element.get("id")
            for element in tree.iter()
            if isinstance(element.tag, str) and element.get("id")
        }
        orphaned_removed_ids = removed_ids - surviving_ids
        orphaned_refinements = [
            child for child in metadata
            if (child.get("refines") or "").removeprefix("#")
            in orphaned_removed_ids
        ]
        if not orphaned_refinements:
            break
        for child in orphaned_refinements:
            child_id = child.get("id") if isinstance(child.tag, str) else None
            if child_id:
                removed_ids.add(child_id)
            metadata.remove(child)

    return etree.tostring(
        tree,
        xml_declaration=True,
        encoding='utf-8',
        pretty_print=True,
    ).decode('utf-8')
