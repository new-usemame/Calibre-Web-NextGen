# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol catalog service with owner-bound opaque selections.

The caller authenticates accounts and checks acquisition permissions. Source
URLs and credentials stay server-side; this service never submits a download.
"""
import ipaddress
import re
import json
from urllib.parse import quote, urldefrag

from .contracts import Link, Search, DIRECT_FORMATS, direct_format_allowed
from .http import HTTPPolicy, TransportError, authorization, normalized_url, origin, run_transfer
from .opds import ACQUISITION, CATALOG_TYPES, PUBLICATION_TYPE, RELATIONS, parse_catalog, parse_search_description
from .storage import NotFound


class CatalogError(ValueError):
    pass


# Ranges an administrator may open one catalog onto. The allowance exists so a
# home catalog on a LAN can be reached; it is not a general hole, so it is an
# allow-list of private space rather than a deny-list of the dangerous parts.
# Loopback, link-local (which carries 169.254.169.254), the unspecified and
# catch-all networks, multicast and public space are all absent, so all of them
# are refused without needing to be enumerated.
HOME_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',   # RFC 1918
    '100.64.0.0/10',                                   # CGNAT, incl. Tailscale
    'fc00::/7',                                        # IPv6 unique local
))
# Inside that private space but never a catalog: cloud instance metadata.
# (169.254.169.254 and its kin are link-local, which is outside the list above.)
METADATA_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '100.100.100.200/32',                              # Alibaba Cloud, inside CGNAT
    'fd00:ec2::254/128',                               # AWS IMDS over IPv6
    'fd20:ce::254/128',                                # GCP metadata over IPv6
))


def _without_metadata(network):
    pieces = [network]
    for hole in METADATA_NETWORKS:
        pieces = [part for piece in pieces for part in (
            piece.address_exclude(hole) if hole.version == piece.version and hole.subnet_of(piece)
            else (piece,))]
    return sorted(pieces)


# What the administrator's one-click opt-in expands to: IPv4 private space with
# the metadata addresses carved out, because the transport honours its allow
# list before any deny rule. IPv6 unique-local is left to an explicit range --
# a site using it sets its own RFC 4193 prefix.
DEFAULT_HOME_NETWORKS = tuple(part for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10',
) for part in _without_metadata(ipaddress.ip_network(value)))


def home_networks(values):
    """Reject an allowance advocate itself would honour but must not.

    `AddrValidator` checks its whitelist *before* the loopback and link-local
    rules, so a whitelisted 127.0.0.0/8 or 169.254.0.0/16 is simply allowed.
    Nothing below this call will refuse them, so this is the only place that
    can, and it is reached by every administrator-supplied configuration.
    """
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError:
            raise CatalogError('invalid_network_policy') from None
        family = [net for net in HOME_NETWORKS if net.version == network.version]
        if not any(network.subnet_of(net) for net in family):
            raise CatalogError('private_network_not_allowed') from None
        if any(network.overlaps(net) for net in METADATA_NETWORKS
               if net.version == network.version):
            raise CatalogError('private_network_not_allowed') from None
    return tuple(values)


def connection_config(value):
    """Validate administrator-supplied OPDS configuration, without a probe."""
    if not isinstance(value, dict) or set(value) - {
            'endpoint', 'auth_kind', 'username', 'secret', 'credential_origins',
            'private_origins', 'private_networks', 'allow_private_network', 'allow_mobi'}:
        raise CatalogError('invalid_connection')
    endpoint = normalized_url(value.get('endpoint'))
    auth_kind = value.get('auth_kind', 'none')
    secret, username = value.get('secret', ''), value.get('username', '')
    if not all(isinstance(x, str) for x in (auth_kind, secret, username)):
        raise CatalogError('invalid_authentication')
    allow_private = value.get('allow_private_network', False)
    allow_mobi = value.get('allow_mobi', False)
    if not isinstance(allow_private, bool) or not isinstance(allow_mobi, bool):
        raise CatalogError('invalid_connection')
    auth = authorization(auth_kind, secret, username)
    def strings(key, default):
        values = value.get(key, default)
        # 32 leaves room for the opt-in's ranges, which the metadata carve-out
        # splits into 25 (one hole in 100.64.0.0/10 costs 22 prefixes).
        if not isinstance(values, (list, tuple)) or len(values) > 32 or any(not isinstance(x, str) or len(x) > 8192 for x in values):
            raise CatalogError('invalid_network_policy')
        return tuple(values)
    # A catalog's redirects never acquire credentials for additional origins.
    credentials = strings('credential_origins', [endpoint] if auth else [])
    private = strings('private_origins', [])
    networks = home_networks(strings('private_networks', []))
    if allow_private:
        # The administrator's opt-in, expanded here rather than in the browser
        # so the allowed ranges have one definition. It is scoped to this one
        # catalog's origin, so it widens nothing else.
        if private or networks:
            raise CatalogError('invalid_connection')
        private = (endpoint,)
        networks = tuple(str(net) for net in DEFAULT_HOME_NETWORKS)
    try:
        HTTPPolicy(credential_origins=credentials, private_origins=private,
                   private_networks=networks, authorization=auth)
    except (ValueError, TypeError):
        raise CatalogError('invalid_network_policy') from None
    if bool(private) != bool(networks):
        raise CatalogError('private_origin_and_network_required')
    return dict(endpoint=endpoint, auth_kind=auth_kind, username=username,
                secret=secret, credential_origins=list(credentials),
                private_origins=list(private), private_networks=list(networks), allow_mobi=allow_mobi)


def policy(config, *, download=False):
    return HTTPPolicy(credential_origins=tuple(config['credential_origins']),
        private_origins=tuple(config['private_origins']),
        private_networks=tuple(config['private_networks']),
        authorization=authorization(config['auth_kind'], config['secret'], config['username']),
        deadline=120 if download else 20)


def expand_search(search, query):
    """Bounded keyword search: OpenSearch terms and OPDS2 query templates.

    Unknown required OpenSearch variables or unsupported URI operators are not
    guessed. Optional unknown values are omitted. User text is always encoded,
    including punctuation which otherwise changes a template's host/query.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise CatalogError('invalid_search')
    template = search.link.href
    expressions = re.findall(r'\{([^{}]+)\}', template)
    if not expressions or len(expressions) > 32 or '{' in re.sub(r'\{[^{}]+\}', '', template) or '}' in re.sub(r'\{[^{}]+\}', '', template):
        raise CatalogError('unsupported_search')
    encoded = quote(query.strip(), safe='')
    found = False
    def replacement(match):
        nonlocal found
        expression = match.group(1)
        if search.syntax == 'opensearch':
            optional = expression.endswith('?')
            name = expression[:-1] if optional else expression
            values = {'searchTerms': encoded, 'count': '50',
                      'inputEncoding': 'UTF-8', 'outputEncoding': 'UTF-8', 'language': '*'}
            if name == 'searchTerms':
                found = True
            if name in values:
                return values[name]
            if optional:
                return ''
            raise CatalogError('unsupported_search')
        if search.syntax != 'uri-template':
            raise CatalogError('unsupported_search')
        operator = expression[0] if expression[0] in '?&' else ''
        variables = (expression[1:] if operator else expression).split(',')
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) for name in variables):
            raise CatalogError('unsupported_search')
        if 'query' not in variables:
            return ''
        found = True
        return operator + 'query=' + encoded if operator else encoded
    expanded = re.sub(r'\{([^{}]+)\}', replacement, template)
    if not found:
        raise CatalogError('unsupported_search')
    # Host templates are not supported by keyword search. The structural host
    # must be unchanged regardless of operator/value escaping.
    if origin(expanded) != origin(re.sub(r'\{[^{}]+\}', '', template)):
        raise CatalogError('unsupported_search')
    return normalized_url(expanded)


class CatalogService:
    def __init__(self, repository, *, transfer=run_transfer, preferred_language=None):
        self.repository, self.transfer = repository, transfer
        self.preferred_language = preferred_language

    def _fetch(self, config, url):
        document = self.transfer(url, policy(config), max_bytes=2 * 1024 * 1024)
        return parse_catalog(document.body, document.url, media_type=document.content_type,
                             preferred_language=self.preferred_language)

    def probe(self, config):
        config = connection_config(config)
        catalog = self._fetch(config, config['endpoint'])
        return {'title': catalog.title, 'protocol': catalog.protocol,
                'browse': True, 'search_advertised': bool(catalog.searches),
                'direct_download_advertised': catalog.capabilities.direct_download}

    def browse(self, owner_id, connection_id, *, selection=None, query=None):
        snapshot = self.repository.connection_config(connection_id)
        config = snapshot.config
        url = config['endpoint']
        if selection is not None:
            payload = self.repository.offer_payload(owner_id, selection, connection_id).offer
            if payload.get('kind') == 'search':
                search = Search(Link(payload['href'], media_type=payload['media_type'], templated=True), payload['syntax'])
                if search.syntax == 'description':
                    document = self.transfer(search.link.href, policy(config), max_bytes=256 * 1024)
                    candidates = parse_search_description(document.body, document.url)
                    urls = []
                    for candidate in candidates:
                        try:
                            urls.append(expand_search(candidate, query))
                        except (CatalogError, TransportError):
                            continue
                    if not urls:
                        raise CatalogError('unsupported_search')
                    url = urls[0]
                else:
                    url = expand_search(search, query)
            elif payload.get('kind') == 'navigation' and query is None:
                url = payload['href']
            else:
                raise NotFound('Catalog selection is unavailable')
        elif query is not None:
            raise CatalogError('search_selection_required')
        catalog = self._fetch(config, url)
        return self._present(owner_id, connection_id, catalog, expected_revision=snapshot.revision,
                             format_config=config)

    def _present(self, owner_id, connection_id, catalog, *, expected_revision=None, format_config=None):
        def identity(kind, value):
            return self.repository.box.display_identity(json.dumps(
                [kind, owner_id, connection_id, value], separators=(',', ':')))
        def selection(payload):
            return self.repository.create_offer(owner_id, connection_id, payload, expected_revision=expected_revision)
        def readable(link):
            relations = set(link.relations)
            if (link.templated or any(relation in RELATIONS or relation == 'sample'
                    or relation.startswith(ACQUISITION) for relation in relations)):
                return False
            if catalog.is_publication_document and ('self' in relations
                    or urldefrag(link.href)[0] == urldefrag(catalog.source_url)[0]):
                return False
            if link.media_type in CATALOG_TYPES:
                return True
            # Publication acquisition links can initiate loans/purchases. Only
            # explicit metadata self/alternate links become detail reads.
            return (link.media_type == PUBLICATION_TYPE and bool(relations)
                    and relations <= {'self', 'alternate'}
                    and urldefrag(link.href)[0] != urldefrag(catalog.source_url)[0])
        def navigation(links, fallback_title=''):
            return [{'title': link.title or fallback_title, 'relations': list(link.relations),
                     'selection': selection({'kind': 'navigation', 'href': link.href})}
                    for link in links if readable(link)]
        def publications(values):
            result = []
            for publication in values:
                offers = []
                seen = set()
                for offer in publication.offers:
                    key = (offer.link.href, offer.link.media_type)
                    if (not offer.is_direct_download or key in seen
                            or not direct_format_allowed(offer.link.media_type, format_config or {})):
                        continue
                    seen.add(key)
                    offers.append({'format': DIRECT_FORMATS[offer.link.media_type][0],
                        'label': offer.link.title or None,
                        'identity': identity('file', [offer.link.href, offer.link.media_type]),
                        'relation': offer.relation, 'offer_id': selection({
                            'kind': 'acquisition', 'href': offer.link.href,
                            'media_type': offer.link.media_type, 'title': publication.title})})
                result.append({'title': publication.title,
                    'identity': identity('publication', publication.identifier or
                        [publication.title, sorted(item['identity'] for item in offers)]),
                    'authors': [person.name for person in publication.contributors if person.role == 'author'],
                    'languages': list(publication.languages), 'description': publication.description,
                    'offers': offers, 'navigation': navigation(publication.links, publication.title)})
            return result
        searches = []
        for search in catalog.searches:
            if search.syntax not in ('description', 'uri-template', 'opensearch'):
                continue
            if search.syntax != 'description':
                try:
                    expand_search(search, 'probe')
                except (CatalogError, TransportError):
                    continue
            searches.append({'title': search.link.title or '', 'selection': selection({
                'kind': 'search', 'href': search.link.href,
                'media_type': search.link.media_type, 'syntax': search.syntax})})
        return {'title': catalog.title, 'protocol': catalog.protocol,
            'publications': publications(catalog.publications),
            'navigation': navigation(catalog.navigation),
            'pagination': navigation(link for link in catalog.links if set(link.relations) & {'next', 'previous', 'prev', 'first', 'last'}),
            'searches': searches,
            'groups': [{'title': group.title, 'publications': publications(group.publications),
                        'navigation': navigation(group.navigation + group.links)} for group in catalog.groups],
            'facets': [{'title': facet.title, 'navigation': navigation(facet.navigation + facet.links)} for facet in catalog.facets]}

    def request(self, owner_id, connection_id, offer_id, idempotency_key, *, requires_approval=True, add_to_my_library=True):
        config = self.repository.connection_config(connection_id).config
        def validate(payload):
            if (payload.get('kind') != 'acquisition'
                    or not direct_format_allowed(payload.get('media_type'), config)):
                raise NotFound('Download selection is unavailable')
        return self.repository.create_job(owner_id, offer_id, idempotency_key,
            requires_approval=requires_approval, add_to_my_library=add_to_my_library,
            connection_id=connection_id, validate_offer=validate)
