"""Regression coverage for #1372's download-time KEPUB metadata rewrite."""

from datetime import datetime, timezone
from types import SimpleNamespace
from zipfile import ZipFile

from lxml import etree

from cps import helper
from cps.services import parallel
from tests.fixtures.kepub_fixture import build_calibre_epub3_series_kepub


def _book(series_name="Verify Series", authors=None):
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    if authors is None:
        authors = ["Alexandre Dumas", "Auguste Maquet"]
    return SimpleNamespace(
        id=1372,
        uuid="issue-1372-real-shape",
        identifiers=[],
        title="Fixture Title",
        authors=[SimpleNamespace(name=name) for name in authors],
        author_sort="Dumas, Alexandre & Maquet, Auguste",
        pubdate=now,
        comments=[SimpleNamespace(text="Library description")],
        publishers=[SimpleNamespace(name="Library Publisher")],
        languages=[SimpleNamespace(lang_code="eng")],
        tags=[SimpleNamespace(name="regression")],
        series=[] if series_name is None else [SimpleNamespace(name=series_name)],
        series_index=3,
        ratings=[],
        timestamp=now,
        sort="Fixture Title",
    )


def _run_download_rewrite(monkeypatch, tmp_path, book, source_transform=None):
    source = build_calibre_epub3_series_kepub(tmp_path / "source.kepub")
    if source_transform is not None:
        package, package_name = helper.get_content_opf(source)
        source_transform(package)
        rewritten = tmp_path / "source-rewritten.kepub"
        helper.updateEpub(
            source,
            rewritten,
            package_name,
            etree.tostring(
                package,
                xml_declaration=True,
                encoding="utf-8",
                pretty_print=True,
            ),
        )
        source = rewritten

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return []

    monkeypatch.setattr(
        helper.calibre_db,
        "session",
        SimpleNamespace(query=lambda *_args: Query()),
    )
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(locale="en"))
    monkeypatch.setattr(helper, "_", lambda value: value)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(helper, "uuid4", lambda: "served-copy")
    monkeypatch.setattr(parallel, "run_blocking", lambda job: job())

    output_dir, output_name = helper.do_kepubify_metadata_replace(book, str(source))
    return source, tmp_path / f"{output_name}.kepub"


def _package(archive):
    container = etree.fromstring(archive.read("META-INF/container.xml"))
    package_name = container.xpath(
        'string(//*[local-name()="rootfile"]/@full-path)'
    )
    return package_name, etree.fromstring(archive.read(package_name))


def _series_collections(package):
    metas = package.xpath('//*[local-name()="meta"]')
    series_ids = {
        element.get("refines", "").removeprefix("#")
        for element in metas
        if element.get("property") == "collection-type"
        and "".join(element.itertext()).strip().lower() == "series"
    }
    return [
        element
        for element in metas
        if element.get("property") == "belongs-to-collection"
        and element.get("id") in series_ids
    ]


def test_kepub_download_keeps_library_series_in_epub3_collection_metadata(
    monkeypatch, tmp_path
):
    source, served = _run_download_rewrite(monkeypatch, tmp_path, _book())

    with ZipFile(source) as source_zip, ZipFile(served) as served_zip:
        package_name, package = _package(served_zip)
        collections = _series_collections(package)
        assert ["".join(element.itertext()).strip() for element in collections] == [
            "Verify Series"
        ]
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#%s"]'
            '[@property="group-position"])' % collections[0].get("id")
        ) == "3"

        assert package.xpath(
            'string(//*[local-name()="meta"][@property="dcterms:modified"])'
        ) == "2026-08-23T12:00:00Z"
        assert package.xpath(
            'string(//*[local-name()="meta"][@name="cover"]/@content)'
        ) == "cover-image"
        assert package.xpath(
            'string(//*[local-name()="meta"][@property="belongs-to-collection"]'
            '[.="Fixture Set"])'
        ) == "Fixture Set"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#creator"]'
            '[@property="file-as"])'
        ) == "Dumas, Alexandre"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#creator"]'
            '[@property="role"])'
        ) == "aut"
        unique_identifier = package.get("unique-identifier")
        assert unique_identifier == "bookid"
        assert len(
            package.xpath(
                '//*[local-name()="identifier"][@id=$identifier]',
                identifier=unique_identifier,
            )
        ) == 1

        assert set(source_zip.namelist()) == set(served_zip.namelist())
        assert served_zip.comment == source_zip.comment
        for member in source_zip.namelist():
            if member != package_name:
                assert served_zip.read(member) == source_zip.read(member), member


def test_kepub_download_clears_only_series_collection_metadata(monkeypatch, tmp_path):
    _source, served = _run_download_rewrite(
        monkeypatch, tmp_path, _book(series_name=None)
    )

    with ZipFile(served) as served_zip:
        _package_name, package = _package(served_zip)
        assert _series_collections(package) == []
        assert package.xpath(
            'string(//*[local-name()="meta"][@property="belongs-to-collection"]'
            '[.="Fixture Set"])'
        ) == "Fixture Set"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#id-6"]'
            '[@property="collection-type"])'
        ) == "set"
        assert package.xpath(
            'string(//*[local-name()="meta"][@refines="#title"]'
            '[@property="title-type"])'
        ) == "main"


def test_kepub_download_removes_dropped_creator_and_its_refinements(
    monkeypatch, tmp_path
):
    _source, served = _run_download_rewrite(
        monkeypatch,
        tmp_path,
        _book(authors=["Alexandre Dumas"]),
    )

    with ZipFile(served) as served_zip:
        _package_name, package = _package(served_zip)
        assert package.xpath(
            '//*[local-name()="creator"]/text()'
        ) == ["Alexandre Dumas"]
        assert package.xpath(
            '//*[local-name()="meta"][@refines="#creator-2"]'
        ) == []


def test_kepub_download_removes_transitive_refinements_of_dropped_creator(
    monkeypatch, tmp_path
):
    def add_two_level_refinement(package):
        dropped_role = package.xpath(
            '//*[local-name()="meta"][@refines="#creator-2"]'
            '[@property="role"]'
        )[0]
        dropped_role.set("id", "drop-role")
        metadata = package.xpath('//*[local-name()="metadata"]')[0]
        display_sequence = etree.Element(
            "{http://www.idpf.org/2007/opf}meta",
            refines="#drop-role",
            property="display-seq",
        )
        display_sequence.text = "1"
        metadata.append(display_sequence)

    _source, served = _run_download_rewrite(
        monkeypatch,
        tmp_path,
        _book(authors=["Alexandre Dumas"]),
        source_transform=add_two_level_refinement,
    )

    with ZipFile(served) as served_zip:
        _package_name, package = _package(served_zip)
        assert package.xpath('//*[@id="drop-role"]') == []
        assert package.xpath('//*[@refines="#drop-role"]') == []


def test_kepub_download_does_not_reassign_removed_author_refinements_on_rename(
    monkeypatch, tmp_path
):
    def make_ambiguous_author_change(package):
        creators = package.xpath('//*[local-name()="creator"]')
        creators[0].set("id", "first")
        creators[0].text = "Removed Author"
        creators[1].set("id", "second")
        creators[1].text = "Old Surviving Name"
        for refinement in package.xpath('//*[@refines]'):
            if refinement.get("refines") == "#creator":
                refinement.set("refines", "#first")
            elif refinement.get("refines") == "#creator-2":
                refinement.set("refines", "#second")

    _source, served = _run_download_rewrite(
        monkeypatch,
        tmp_path,
        _book(authors=["New Surviving Name"]),
        source_transform=make_ambiguous_author_change,
    )

    with ZipFile(served) as served_zip:
        _package_name, package = _package(served_zip)
        creators = package.xpath('//*[local-name()="creator"]')
        assert ["".join(element.itertext()) for element in creators] == [
            "New Surviving Name"
        ]
        assert creators[0].get("id") not in {"first", "second"}
        assert package.xpath('//*[@refines="#first" or @refines="#second"]') == []


def test_kepub_download_renames_generated_id_that_collides_with_identity_anchor(
    monkeypatch, tmp_path
):
    def use_urn_uuid_identity(package):
        identifier = package.xpath(
            '//*[local-name()="identifier"][@id="bookid"]'
        )[0]
        package.set("unique-identifier", "uuid_id")
        identifier.set("id", "uuid_id")
        identifier.text = "urn:uuid:issue-1372-real-shape"

    _source, served = _run_download_rewrite(
        monkeypatch,
        tmp_path,
        _book(),
        source_transform=use_urn_uuid_identity,
    )

    with ZipFile(served) as served_zip:
        _package_name, package = _package(served_zip)
        ids = package.xpath('//*[@id]/@id')
        assert len(ids) == len(set(ids)), ids
        unique_identifier = package.get("unique-identifier")
        identity_targets = package.xpath(
            '//*[local-name()="identifier"][@id=$identifier]',
            identifier=unique_identifier,
        )
        assert len(identity_targets) == 1
        assert "".join(identity_targets[0].itertext()) == (
            "urn:uuid:issue-1372-real-shape"
        )
