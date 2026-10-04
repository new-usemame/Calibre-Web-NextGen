# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure bounded OPDS parsers. No network, persistence, template expansion or HTML.

Parsing a URL is not SSRF validation. Consumers must enforce connection origins,
resolve DNS safely, and check redirects before fetching any returned resource.
"""
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .contracts import (Capabilities, Catalog, Contributor, Link, Offer,
                        Publication, Search, Section)

ATOM = "http://www.w3.org/2005/Atom"
DC = ("http://purl.org/dc/terms/", "http://purl.org/dc/elements/1.1/")
OPDS = "http://opds-spec.org/2010/catalog"
OS = "http://a9.com/-/spec/opensearch/1.1/"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
ACQUISITION = "http://opds-spec.org/acquisition"
RELATIONS = {ACQUISITION: "acquisition", ACQUISITION + "/open-access": "download",
             ACQUISITION + "/buy": "buy", ACQUISITION + "/borrow": "borrow",
             ACQUISITION + "/sample": "preview", ACQUISITION + "/subscribe": "subscribe"}
RELATIONS.update({value: value for value in tuple(RELATIONS.values())})
CATALOG_TYPES = frozenset(("application/atom+xml", "application/opds+json"))
PUBLICATION_TYPE = "application/opds-publication+json"
# Readium language-map schema BCP47 syntax (no IANA registry/network lookup).
_LANGUAGE_TAG = re.compile('^((?:(en-GB-oed|i-ami|i-bnn|i-default|i-enochian|i-hak|i-klingon|i-lux|i-mingo|i-navajo|i-pwn|i-tao|i-tay|i-tsu|sgn-BE-FR|sgn-BE-NL|sgn-CH-DE)|(art-lojban|cel-gaulish|no-bok|no-nyn|zh-guoyu|zh-hakka|zh-min|zh-min-nan|zh-xiang))|((?:([A-Za-z]{2,3}(-(?:[A-Za-z]{3}(-[A-Za-z]{3}){0,2}))?)|[A-Za-z]{4}|[A-Za-z]{5,8})(-(?:[A-Za-z]{4}))?(-(?:[A-Za-z]{2}|[0-9]{3}))?(-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*(-(?:[0-9A-WY-Za-wy-z](-[A-Za-z0-9]{2,8})+))*(-(?:x(-[A-Za-z0-9]{1,8})+))?)|(?:x(-[A-Za-z0-9]{1,8})+))$', re.IGNORECASE)


class CatalogParseError(ValueError):
    """Malformed/unsupported catalog; messages never contain remote content."""


@dataclass(frozen=True)
class ParseLimits:
    max_bytes: int = 2 * 1024 * 1024
    max_depth: int = 32
    max_nodes: int = 20000
    max_publications: int = 1000
    max_links: int = 5000
    max_text: int = 32768

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("Parse limits must be positive integers")


class _Budget:
    def __init__(self, limits, preferred_language=None):
        self.limits = limits
        self.links = self.publications = 0
        if preferred_language is not None and not isinstance(preferred_language, str):
            raise CatalogParseError("Invalid display language")
        self.language = (preferred_language or "en").replace("_", "-").lower()

    def count(self, kind):
        value = getattr(self, kind) + 1
        if value > getattr(self.limits, "max_" + kind):
            raise CatalogParseError("Catalog exceeds " + kind + " limit")
        setattr(self, kind, value)

    def text(self, value, required=False):
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise CatalogParseError("Expected catalog text")
        if len(value) > self.limits.max_text:
            raise CatalogParseError("Catalog text exceeds limit")
        value = value.strip()
        if required and not value:
            raise CatalogParseError("Required catalog text is empty")
        return value or None


    def localized_text(self, value, required=False):
        if not isinstance(value, dict):
            return self.text(value, required=required)
        if not value:
            raise CatalogParseError("Language map is empty")
        variants = {}
        # Validate all values before selecting; an unselected variant is still
        # remote input and must not bypass text/type/structure limits.
        for tag, text in value.items():
            if len(tag) > 128 or not _LANGUAGE_TAG.fullmatch(tag):
                raise CatalogParseError("Invalid language map tag")
            key = tag.lower()
            if key in variants:
                raise CatalogParseError("Duplicate language map tag")
            variants[key] = self.text(text, required=True)
        keys = sorted(variants)
        language = self.language
        while language:
            if language in variants:
                return variants[language]
            matches = [key for key in keys if key.startswith(language + "-")]
            if matches:
                return variants[matches[0]]
            language = language.rsplit("-", 1)[0] if "-" in language else ""
        return variants.get("en", variants[keys[0]])


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.rsplit(":", 1)[-1]
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif tag in ("p", "div", "li"):
            self.parts.append(" ")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def _plain(value):
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split()) or None


def _url(value, base, budget):
    value = budget.text(value, required=True)
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise CatalogParseError("Invalid catalog URL")
    try:
        resolved = urljoin(base, value)
        parsed = urlsplit(resolved)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError()
        parsed.port  # invalid numeric port is malformed too
    except ValueError as exc:
        raise CatalogParseError("Unsupported catalog URL") from None
    return resolved


def _type(value, budget):
    value = budget.text(value)
    return value.split(";", 1)[0].strip().lower() if value else None


def _payload(payload, limits):
    if isinstance(payload, str):
        try:
            payload = payload.encode("utf-8")
        except UnicodeError as exc:
            raise CatalogParseError("Invalid catalog encoding") from None
    if not isinstance(payload, bytes):
        raise CatalogParseError("Catalog payload must be bytes or text")
    if len(payload) > limits.max_bytes:
        raise CatalogParseError("Catalog payload exceeds byte limit")
    return payload


class _BoundedTree(ET.TreeBuilder):
    def __init__(self, limits):
        super().__init__()
        self.limits = limits
        self.depth = self.nodes = 0

    def doctype(self, name, pubid, system):
        raise CatalogParseError("DTD and entity declarations are not supported")

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > self.limits.max_depth or self.nodes > self.limits.max_nodes:
            raise CatalogParseError("XML structure exceeds limit")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def _xml(payload, limits):
    try:
        return ET.fromstring(payload, parser=ET.XMLParser(target=_BoundedTree(limits)))
    except (ET.ParseError, ValueError) as exc:
        if isinstance(exc, CatalogParseError):
            raise
        raise CatalogParseError("Invalid XML catalog") from None


def _bases(root, document_url, budget):
    result = {}
    stack = [(root, document_url)]
    while stack:
        node, base = stack.pop()
        base = _url(node.attrib[XML_BASE], base, budget) if node.attrib.get(XML_BASE) else base
        result[node] = base
        stack.extend((child, base) for child in node)
    return result


def _children(node, name, namespace=ATOM):
    return node.findall("{" + namespace + "}" + name)


def _node_text(node, budget):
    return budget.text("".join(node.itertext())) if node is not None else None


def _child_text(node, name, budget, namespace=ATOM):
    return _node_text(node.find("{" + namespace + "}" + name), budget)


def _xml_links(node, bases, budget):
    result = []
    for child in _children(node, "link"):
        budget.count("links")
        relation = budget.text(child.get("rel")) or "alternate"
        href = budget.text(child.get("href"), required=True)
        if relation in ("http://opds-spec.org/image", "http://opds-spec.org/image/thumbnail") and href.lower().startswith("data:"):
            # Inline artwork is optional and never a transport target. Some
            # public catalogs use it for every thumbnail; do not discard books.
            continue
        link = Link(_url(href, bases[child], budget), (relation,),
                    _type(child.get("type"), budget), budget.text(child.get("title")))
        indirect = tuple(_type(item.get("type"), budget) for item in child.iter()
                         if item.tag == "{" + OPDS + "}indirectAcquisition")
        if any(not item for item in indirect):
            raise CatalogParseError("Indirect acquisition needs a media type")
        result.append((link, indirect))
    return tuple(result)


def _offers(links):
    return tuple(Offer(link, RELATIONS[relation], indirect)
                 for link, indirect in links for relation in link.relations
                 if relation in RELATIONS)


def _searches(links, protocol):
    return tuple(Search(link, "description" if link.media_type == "application/opensearchdescription+xml"
                        else "uri-template" if protocol == "opds2" and link.templated
                        else "unsupported")
                 for link in links if "search" in link.relations)


def _publication_xml(node, bases, budget):
    budget.count("publications")
    title = budget.text(_child_text(node, "title", budget), required=True)
    identifier = _child_text(node, "id", budget)
    contributors = tuple(Contributor(budget.text(_child_text(author, "name", budget), required=True))
                         for author in _children(node, "author"))
    languages = tuple(filter(None, (_child_text(node, "language", budget, ns) for ns in DC)))
    identifiers = tuple(filter(None, (_node_text(item, budget) for ns in DC
                                      for item in _children(node, "identifier", ns))))
    description_node = node.find("{" + ATOM + "}summary")
    if description_node is None:
        description_node = node.find("{" + ATOM + "}content")
    description = _node_text(description_node, budget)
    if description and description_node.get("type") in ("html", "xhtml"):
        if description_node.get("type") == "xhtml":
            description = ET.tostring(description_node, encoding="unicode")
        description = _plain(description)
    links = _xml_links(node, bases, budget)
    images = tuple(link for link, _ in links if any(rel.startswith("http://opds-spec.org/image")
                                                    for rel in link.relations))
    return Publication(identifier, title, contributors, languages, identifiers,
                       description, tuple(link for link, _ in links), images, _offers(links))


def _catalog_xml(payload, source_url, budget):
    root = _xml(payload, budget.limits)
    standalone = root.tag == "{" + ATOM + "}entry"
    if not standalone and root.tag != "{" + ATOM + "}feed":
        raise CatalogParseError("Expected Atom catalog feed or entry")
    bases = _bases(root, source_url, budget)
    if standalone:
        for field in ("id", "updated"):
            values = _children(root, field)
            if len(values) != 1 or not _node_text(values[0], budget):
                raise CatalogParseError("Publication needs Atom identity and update")
        publication = _publication_xml(root, bases, budget)
        if not publication.offers:
            raise CatalogParseError("Publication needs an acquisition link")
        return Catalog(publication.title, source_url, "opds1", (publication,), (),
                       publication.links, (), (), (), Capabilities(True, False, any(
                           offer.is_direct_download for offer in publication.offers)),
                       is_publication_document=True)
    links = tuple(link for link, _ in _xml_links(root, bases, budget))
    publications, navigation = [], []
    for entry in _children(root, "entry"):
        publication = _publication_xml(entry, bases, budget)
        catalog_links = tuple(link for link in publication.links if link.media_type in CATALOG_TYPES)
        if publication.offers or not catalog_links:
            publications.append(publication)
        else:
            navigation.extend(Link(link.href, link.relations, link.media_type,
                                   link.title or publication.title, link.templated)
                              for link in catalog_links)
    searches = _searches(links, "opds1")
    return Catalog(budget.text(_child_text(root, "title", budget), required=True), source_url,
                   "opds1", tuple(publications), tuple(navigation), links, searches, (), (),
                   Capabilities(True, bool(searches), any(
                       offer.is_direct_download for pub in publications for offer in pub.offers)))


def _json(payload, limits):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CatalogParseError("Duplicate JSON member")
            result[key] = value
        return result

    try:
        document = json.loads(payload, object_pairs_hook=unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(
                                  CatalogParseError("Non-finite JSON value")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CatalogParseError):
            raise
        raise CatalogParseError("Invalid JSON catalog") from None
    nodes = 0
    stack = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise CatalogParseError("JSON structure exceeds limit")
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    return _object(document)


def _object(value):
    if not isinstance(value, dict):
        raise CatalogParseError("Expected JSON object")
    return value


def _array(value):
    if not isinstance(value, list):
        raise CatalogParseError("Expected JSON array")
    return value


def _json_links(values, base, budget, *, images=False):
    result = []
    for value in _array(values):
        value = _object(value)
        budget.count("links")
        relations = value.get("rel", [])
        if isinstance(relations, str):
            relations = [relations]
        relations = tuple(budget.text(item, required=True) for item in _array(relations))
        templated = value.get("templated", False)
        if not isinstance(templated, bool):
            raise CatalogParseError("Invalid template flag")
        href = budget.text(value.get("href"), required=True)
        if images and href.lower().startswith("data:"):
            continue
        link = Link(_url(href, base, budget), relations,
                    _type(value.get("type"), budget), budget.text(value.get("title")), templated)
        properties = _object(value.get("properties", {}))
        indirect = []
        stack = list(reversed(_array(properties.get("indirectAcquisition", []))))
        while stack:
            item = _object(stack.pop())
            indirect.append(budget.text(item.get("type"), required=True).lower())
            stack.extend(reversed(_array(item.get("child", []))))
        result.append((link, tuple(indirect)))
    return tuple(result)


def _strings(value, budget):
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(budget.text(item, required=True) for item in _array(value))


def _publication_json(value, base, budget):
    value = _object(value)
    budget.count("publications")
    metadata = _object(value.get("metadata"))
    contributors = []
    for role in ("author", "translator", "editor", "artist", "illustrator", "narrator"):
        people = metadata.get(role, [])
        if isinstance(people, (str, dict)):
            people = [people]
        for person in _array(people):
            if isinstance(person, str):
                contributors.append(Contributor(budget.text(person, required=True), role))
            else:
                person = _object(person)
                contributors.append(Contributor(budget.localized_text(person.get("name"), required=True), role,
                                                budget.text(person.get("identifier"))))
    links = _json_links(value.get("links", []), base, budget)
    images = tuple(link for link, _ in _json_links(value.get("images", []), base, budget, images=True))
    description = budget.text(metadata.get("description"))
    identifiers = _strings(metadata.get("identifier"), budget)
    return Publication(identifiers[0] if identifiers else None,
                       budget.localized_text(metadata.get("title"), required=True), tuple(contributors),
                       _strings(metadata.get("language"), budget), identifiers,
                       _plain(description) if description else None,
                       tuple(link for link, _ in links), images, _offers(links))


def _section(value, base, budget):
    value = _object(value)
    metadata = _object(value.get("metadata"))
    return Section(budget.localized_text(metadata.get("title"), required=True),
                   tuple(link for link, _ in _json_links(value.get("navigation", []), base, budget)),
                   tuple(_publication_json(item, base, budget)
                         for item in _array(value.get("publications", []))),
                   tuple(link for link, _ in _json_links(value.get("links", []), base, budget)))


def _catalog_json(payload, source_url, budget):
    document = _json(payload, budget.limits)
    metadata = _object(document.get("metadata"))
    if not any(key in document for key in ("navigation", "publications", "groups")):
        raise CatalogParseError("OPDS2 catalog has no collection")
    links = tuple(link for link, _ in _json_links(document.get("links", []), source_url, budget))
    publications = tuple(_publication_json(item, source_url, budget)
                         for item in _array(document.get("publications", [])))
    navigation = tuple(link for link, _ in _json_links(document.get("navigation", []), source_url, budget))
    groups = tuple(_section(item, source_url, budget) for item in _array(document.get("groups", [])))
    facets = tuple(_section(item, source_url, budget) for item in _array(document.get("facets", [])))
    searches = _searches(links, "opds2")
    all_publications = publications + tuple(pub for group in groups for pub in group.publications)
    return Catalog(budget.localized_text(metadata.get("title"), required=True), source_url, "opds2",
                   publications, navigation, links, searches, groups, facets,
                   Capabilities(True, bool(searches), any(
                       offer.is_direct_download for pub in all_publications for offer in pub.offers)))


def _publication_catalog_json(payload, source_url, budget):
    document = _json(payload, budget.limits)
    if any(key in document for key in ("navigation", "publications", "groups")):
        raise CatalogParseError("Expected one OPDS publication")
    publication = _publication_json(document, source_url, budget)
    if not publication.offers:
        raise CatalogParseError("OPDS publication has no acquisition link")
    return Catalog(publication.title, source_url, "opds2", (publication,), (),
                   publication.links, (), (), (),
                   Capabilities(True, False, any(offer.is_direct_download
                                                for offer in publication.offers)),
                   is_publication_document=True)


def parse_catalog(payload, document_url, *, media_type=None, limits=ParseLimits(), preferred_language=None):
    """Parse a fetched catalog. ``document_url`` is the final response URL.

    Templates are preserved, never expanded. A later transport must implement
    the advertised syntax explicitly; ``Search.syntax == 'unsupported'`` must
    not be silently replaced with a guessed search query.
    """
    budget = _Budget(limits, preferred_language)
    source_url = _url(document_url, "", budget)
    payload = _payload(payload, limits)
    kind = _type(media_type, budget)
    if kind == PUBLICATION_TYPE:
        return _publication_catalog_json(payload, source_url, budget)
    if kind == "application/opds+json" or (kind is None and payload.lstrip().startswith((b"{", b"\xef\xbb\xbf{"))):
        return _catalog_json(payload, source_url, budget)
    if kind not in (None, "application/atom+xml", "application/xml", "text/xml"):
        raise CatalogParseError("Unsupported catalog media type")
    return _catalog_xml(payload, source_url, budget)


def parse_search_description(payload, document_url, *, limits=ParseLimits()):
    """Read OpenSearch URL templates without fetching or expanding them."""
    budget = _Budget(limits)
    source_url = _url(document_url, "", budget)
    root = _xml(_payload(payload, limits), limits)
    if root.tag != "{" + OS + "}OpenSearchDescription":
        raise CatalogParseError("Expected OpenSearch 1.1 description")
    bases = _bases(root, source_url, budget)
    searches = []
    for node in _children(root, "Url", OS):
        budget.count("links")
        media_type = _type(node.get("type"), budget)
        if media_type in CATALOG_TYPES:
            searches.append(Search(Link(_url(node.get("template"), bases[node], budget),
                                        ("search",), media_type, templated=True), "opensearch"))
    return tuple(searches)
