# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable source-neutral descriptions, not authorization to fetch a URL.

URLs may contain provider tokens: these internal objects are not API serializers.
A connection's transport must validate destinations and supply scoped credentials.
"""
from dataclasses import dataclass, field
from types import MappingProxyType

MOBI_MEDIA_TYPE = 'application/x-mobipocket-ebook'
DIRECT_FORMATS = MappingProxyType({
    'application/epub+zip': ('EPUB', 'epub'),
    'application/pdf': ('PDF', 'pdf'),
    MOBI_MEDIA_TYPE: ('MOBI', 'mobi'),
})


def direct_format_allowed(media_type, config):
    """Source capability plus the administrator's explicit catalog opt-in."""
    return (isinstance(media_type, str) and media_type in DIRECT_FORMATS
            and (media_type != MOBI_MEDIA_TYPE or config.get('allow_mobi') is True))


@dataclass(frozen=True)
class Link:
    href: str = field(repr=False)
    relations: tuple[str, ...] = ()
    media_type: str | None = None
    title: str | None = None
    templated: bool = False


@dataclass(frozen=True)
class Search:
    link: Link
    syntax: str  # description, opensearch, or uri-template; no expansion implied


@dataclass(frozen=True)
class Contributor:
    name: str
    role: str = "author"
    identifier: str | None = None


@dataclass(frozen=True)
class Offer:
    link: Link
    relation: str  # download, acquisition, buy, borrow, preview, subscribe
    indirect_types: tuple[str, ...] = ()

    @property
    def is_direct_download(self) -> bool:
        """Complete file candidate; may still require configured authentication.

        Generic acquisition does not promise the absence of authentication or
        payment requirements that open-access/download explicitly promises.
        Only the authorized transport can establish actual availability.
        """
        return (self.relation in ("download", "acquisition") and not self.link.templated
                and not self.indirect_types
                and isinstance(self.link.media_type, str) and self.link.media_type in DIRECT_FORMATS)


@dataclass(frozen=True)
class Publication:
    identifier: str | None
    title: str
    contributors: tuple[Contributor, ...] = ()
    languages: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    description: str | None = None  # plain text only
    links: tuple[Link, ...] = ()
    images: tuple[Link, ...] = ()
    offers: tuple[Offer, ...] = ()


@dataclass(frozen=True)
class Section:
    title: str
    navigation: tuple[Link, ...] = ()
    publications: tuple[Publication, ...] = ()
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class Capabilities:
    browse: bool  # valid catalog can be presented, including an empty page
    search: bool  # advertised; a description/template may still need resolving
    direct_download: bool  # at least one explicit direct offer on this page


@dataclass(frozen=True)
class Catalog:
    title: str
    source_url: str = field(repr=False)
    protocol: str
    publications: tuple[Publication, ...]
    navigation: tuple[Link, ...]
    links: tuple[Link, ...]
    searches: tuple[Search, ...]
    groups: tuple[Section, ...]
    facets: tuple[Section, ...]
    capabilities: Capabilities
    is_publication_document: bool = False
