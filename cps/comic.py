# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import tarfile
import zipfile

from . import logger, isoLanguages, cover
from .constants import BookMeta

try:
    from wand.image import Image
    use_IM = True
except (ImportError, RuntimeError) as e:
    use_IM = False

log = logger.create()

# Backport of janeczku/calibre-web PR #3504 (@lb803): natural-sort the
# archive entry list before scanning for a cover image. Picking by
# unsorted order (the prior behavior) or even bare lexicographic sort
# put `page10` before `page2` and produced a wrong cover on most
# multi-digit comic series. `natsort` is already a hard dependency for
# the book listing in cps/web.py; the fallback to stdlib `sorted`
# keeps deterministic order in stripped-down deployments where natsort
# might be unavailable.
try:
    from natsort import natsorted as sort
except ImportError:
    log.debug("Natural sorting unavailable: using standard sorted() method instead.")
    sort = sorted

try:
    from comicapi.comicarchive import ComicArchive, MetaDataStyle
    use_comic_meta = True
    try:
        from comicapi import __version__ as comic_version
    except ImportError:
        comic_version = ''
    try:
        from comicapi.comicarchive import load_archive_plugins
        import comicapi.utils
        comicapi.utils.add_rar_paths()
    except ImportError:
        load_archive_plugins = None
except (ImportError, LookupError) as e:
    log.debug('Cannot import comicapi, extracting comic metadata will not work: %s', e)
    try:
        import rarfile
        use_rarfile = True
    except (ImportError, SyntaxError) as e:
        log.debug('Cannot import rarfile, extracting cover files from rar files will not work: %s', e)
        use_rarfile = False
    try:
        import py7zr
        use_7zip = True
    except (ImportError, SyntaxError) as e:
        log.debug('Cannot import py7zr, extracting cover files from CB7 files will not work: %s', e)
        use_7zip = False
    use_comic_meta = False
else:
    # comicapi imported successfully — rarfile and py7zr aren't strictly
    # needed for the comicapi path, but `_extract_cover_from_archive`
    # is still called as a fallback when comicapi can't open a CBR/CB7,
    # so probe them here too.
    try:
        import rarfile
        use_rarfile = True
    except (ImportError, SyntaxError):
        use_rarfile = False
    try:
        import py7zr
        use_7zip = True
    except (ImportError, SyntaxError):
        use_7zip = False


def _extract_cover_from_archive(original_file_extension, tmp_file_name, rar_executable):
    cover_data = extension = None
    if original_file_extension.upper() == '.CBZ':
        cf = zipfile.ZipFile(tmp_file_name)
        for name in sort(cf.namelist()):
            ext = os.path.splitext(name)
            if len(ext) > 1:
                extension = ext[1].lower()
                if extension in cover.COVER_EXTENSIONS:
                    cover_data = cf.read(name)
                    break
    elif original_file_extension.upper() == '.CBT':
        cf = tarfile.TarFile(tmp_file_name)
        for name in sort(cf.getnames()):
            ext = os.path.splitext(name)
            if len(ext) > 1:
                extension = ext[1].lower()
                if extension in cover.COVER_EXTENSIONS:
                    cover_data = cf.extractfile(name).read()
                    break
    elif original_file_extension.upper() == '.CBR' and use_rarfile:
        try:
            rarfile.UNRAR_TOOL = rar_executable
            cf = rarfile.RarFile(tmp_file_name)
            for name in sort(cf.namelist()):
                ext = os.path.splitext(name)
                if len(ext) > 1:
                    extension = ext[1].lower()
                    if extension in cover.COVER_EXTENSIONS:
                        cover_data = cf.read([name])
                        break
        except Exception as ex:
            log.error('Rarfile failed with error: {}'.format(ex))
    elif original_file_extension.upper() == '.CB7' and use_7zip:
        cf = py7zr.SevenZipFile(tmp_file_name)
        for name in sort(cf.getnames()):
            ext = os.path.splitext(name)
            if len(ext) > 1:
                extension = ext[1].lower()
                if extension in cover.COVER_EXTENSIONS:
                    try:
                        cover_data = cf.read([name])[name].read()
                    except (py7zr.Bad7zFile, OSError) as ex:
                        log.error('7Zip file failed with error: {}'.format(ex))
                    break
    return cover_data, extension


def _extract_cover(tmp_file_name, original_file_extension, rar_executable):
    cover_data = extension = None
    if use_comic_meta:
        try:
            archive = ComicArchive(tmp_file_name, rar_exe_path=rar_executable)
        except TypeError:
            archive = ComicArchive(tmp_file_name)
        name_list = archive.getPageNameList if hasattr(archive, "getPageNameList") else archive.get_page_name_list
        for index, name in enumerate(name_list()):
            ext = os.path.splitext(name)
            if len(ext) > 1:
                extension = ext[1].lower()
                if extension in cover.COVER_EXTENSIONS:
                    get_page = archive.getPage if hasattr(archive, "getPageNameList") else archive.get_page
                    cover_data = get_page(index)
                    break
    else:
        cover_data, extension = _extract_cover_from_archive(original_file_extension, tmp_file_name, rar_executable)
    return cover.cover_processing(tmp_file_name, cover_data, extension)


def flatten_comicinfo_to_root(archive_path):
    """Move a misplaced ComicInfo.xml to the archive root, in place.

    The ComicInfo.xml standard requires the file at the archive root;
    comicapi's has_metadata/read_metadata do an exact-name lookup against
    the archive's file list and only ever match a root-level entry, so a
    real-world .cbz that packages it one folder down (a common scan-group
    convention) never gets its metadata read at all, root or not. This is
    consistent with every other reader we checked (ComicTagger, Komga) - it's
    not a comicapi bug, it's the file that's out of spec. Rewriting the copy
    fixes it for every future reader, not just this one import.

    Only rewrites zip-based archives (.cbz) - .cbr/.cb7/.cbt aren't zip
    containers and there's no free Python writer for RAR, so those are left
    untouched. No-ops (returns False) when ComicInfo.xml is already at root,
    absent entirely, or the archive isn't a zip - callers don't need to
    check any of that first.

    :returns: True if the archive was rewritten, False if left untouched.
    """
    if not zipfile.is_zipfile(archive_path):
        return False

    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if "ComicInfo.xml" in names:
            return False  # already at root

        nested_name = next(
            (name for name in names if os.path.basename(name).lower() == "comicinfo.xml"),
            None,
        )
        if nested_name is None:
            return False  # no ComicInfo.xml anywhere in the archive

        entries = archive.infolist()
        contents = {info.filename: archive.read(info) for info in entries}

    tmp_path = archive_path + ".comicinfo-flatten.tmp"
    with zipfile.ZipFile(tmp_path, "w") as out:
        for info in entries:
            data = contents[info.filename]
            name = "ComicInfo.xml" if info.filename == nested_name else info.filename
            new_info = zipfile.ZipInfo(name, date_time=info.date_time)
            new_info.compress_type = info.compress_type
            new_info.external_attr = info.external_attr
            out.writestr(new_info, data)

    os.replace(tmp_path, archive_path)
    return True


def get_comic_info(tmp_file_path, original_file_name, original_file_extension, rar_executable, no_cover_processing):
    if use_comic_meta:
        try:
            archive = ComicArchive(tmp_file_path, rar_exe_path=rar_executable)
        except TypeError:
            load_archive_plugins(force=True, rar=rar_executable)
            archive = ComicArchive(tmp_file_path)
        if hasattr(archive, "seemsToBeAComicArchive"):
            seems_archive = archive.seemsToBeAComicArchive
        else:
            seems_archive = archive.seems_to_be_a_comic_archive
        if seems_archive():
            has_metadata = archive.hasMetadata if hasattr(archive, "hasMetadata") else archive.has_metadata
            if has_metadata(MetaDataStyle.CIX):
                style = MetaDataStyle.CIX
            elif has_metadata(MetaDataStyle.CBI):
                style = MetaDataStyle.CBI
            else:
                style = None

            read_metadata = archive.readMetadata if hasattr(archive, "readMetadata") else archive.read_metadata
            loaded_metadata = read_metadata(style)

            lang = loaded_metadata.language or ""
            loaded_metadata.language = isoLanguages.get_lang3(lang)
            if not no_cover_processing:
                cover_file = _extract_cover(tmp_file_path, original_file_extension, rar_executable)
            else:
                cover_file = None
            return BookMeta(
                file_path=tmp_file_path,
                extension=original_file_extension,
                title=loaded_metadata.title or original_file_name,
                author=" & ".join([credit["person"]
                                   for credit in loaded_metadata.credits if credit["role"] == "Writer"]) or 'Unknown',
                cover=cover_file,
                description=loaded_metadata.comments or "",
                tags="",
                series=loaded_metadata.series or "",
                series_id=loaded_metadata.issue or "",
                languages=loaded_metadata.language,
                publisher="",
                pubdate="",
                identifiers=[])
    if not no_cover_processing:
        cover_file = _extract_cover(tmp_file_path, original_file_extension, rar_executable)
    else:
        cover_file = None

    return BookMeta(
        file_path=tmp_file_path,
        extension=original_file_extension,
        title=original_file_name,
        author='Unknown',
        cover=cover_file,
        description="",
        tags="",
        series="",
        series_id="",
        languages="",
        publisher="",
        pubdate="",
        identifiers=[])
