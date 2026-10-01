# SPDX-License-Identifier: GPL-3.0-or-later
"""Newznab/Torznab discovery, with private release tokens and scoped transport."""
from dataclasses import replace
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from .catalog import connection_config as transport_config, policy
from .http import TransportError, normalized_url, origin, run_transfer
from .storage import NotFound


class IndexerError(TransportError):
    pass


def connection_config(value):
    if not isinstance(value, dict):
        raise IndexerError('invalid_connection')
    extra = {'category', 'client_id', 'preset', 'download_origins', 'tracker_origins'}
    if not {'category', 'client_id'} <= set(value):
        raise IndexerError('invalid_connection')
    config = transport_config({key: val for key, val in value.items() if key not in extra})
    if config['auth_kind'] != 'none' or not config['secret'] or len(config['secret']) > 512:
        raise IndexerError('invalid_authentication')
    # Keys belong in the encrypted credential field, never a setup URL.
    if urlsplit(config['endpoint']).query:
        raise IndexerError('invalid_connection')
    category, client = value['category'], value['client_id']
    if not isinstance(category, str) or not re.fullmatch(r'7[0-9]{3}', category):
        raise IndexerError('invalid_connection')
    if not isinstance(client, str) or not re.fullmatch(r'[0-9a-f-]{36}', client):
        raise IndexerError('invalid_connection')
    downloads = value.get('download_origins', [])
    if not isinstance(downloads, list) or len(downloads) > 16 or any(not isinstance(item, str) for item in downloads):
        raise IndexerError('invalid_network_policy')
    # Only exact, explicit redirect origins inherit the home-network allowance.
    for item in downloads:
        parts = urlsplit(normalized_url(item))
        if parts.path not in ('', '/') or parts.query or parts.fragment:
            raise IndexerError('invalid_network_policy')
    if downloads and config['private_networks']:
        config['private_origins'] = list(dict.fromkeys(config['private_origins'] + downloads))
    from .torrent import tracker_origin
    trackers = value.get('tracker_origins', [])
    if not isinstance(trackers, list) or len(trackers) > 16: raise IndexerError('invalid_network_policy')
    for item in trackers:
        tracker_origin(item)
        parts = urlsplit(item)
        if parts.path not in ('', '/') or parts.query or parts.fragment: raise IndexerError('invalid_network_policy')
    preset = value.get('preset', 'newznab')
    if preset not in ('newznab', 'torznab', 'prowlarr', 'jackett'):
        raise IndexerError('invalid_connection')
    return dict(config, category=category, client_id=client, preset=preset, download_origins=downloads, tracker_origins=trackers)


def xml_document(raw):
    if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
        raise IndexerError('invalid_indexer_response')
    check = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in check or b'<!ENTITY' in check:
        raise IndexerError('invalid_indexer_response')
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        raise IndexerError('invalid_indexer_response') from None
    if root.tag == 'error':
        code = root.get('code')
        raise IndexerError('needs_auth' if code in ('100', '101', '102') else
            'source_busy' if code in ('500', '910') else 'indexer_error')
    return root


def parse_caps(raw, category):
    root = xml_document(raw)
    if root.tag != 'caps':
        raise IndexerError('invalid_indexer_response')
    advertised = {node.get('id') for node in root.findall('.//categories/category') + root.findall('.//categories/category/subcat')}
    if category not in advertised:
        raise IndexerError('book_category_unavailable')
    for name, mode in (('book-search', 'book'), ('search', 'search')):
        node = root.find('searching/' + name)
        if node is not None and node.get('available', '').lower() == 'yes' and 'q' in [value.strip() for value in node.get('supportedParams', '').split(',')]:
            server = root.find('server')
            return {'title': server.get('title', '') if server is not None else '', 'mode': mode, 'category': category}
    raise IndexerError('search_unavailable')


def api_url(config, **params):
    parts = urlsplit(config['endpoint'])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(dict(params, apikey=config['secret'])), ''))


def descriptor_url(config, href):
    url = normalized_url(urljoin(config['endpoint'], href))
    # Indexer keys must never be attached to a catalog-supplied foreign origin.
    if origin(url) != origin(config['endpoint']):
        raise IndexerError('untrusted_release_origin')
    parts = urlsplit(url)
    params = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != 'apikey']
    params.append(('apikey', config['secret']))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ''))


class IndexerService:
    def __init__(self, repository, *, transfer=run_transfer):
        self.repository, self.transfer = repository, transfer

    def _document(self, config, **params):
        # Query API keys cannot follow redirects. The endpoint must be canonical.
        try:
            return self.transfer(api_url(config, **params), replace(policy(config), max_redirects=0), max_bytes=2 * 1024 * 1024)
        except TransportError as error:
            raise IndexerError(error.code, retry_after=error.retry_after) from None

    def probe(self, config):
        config = connection_config(config)
        caps = parse_caps(self._document(config, t='caps').body, config['category'])
        # Search is a needed authenticated capability, not merely a caps claim.
        document = self._document(config, t=caps['mode'], cat=config['category'], q='CWNG connection test', limit='1', offset='0')
        if xml_document(document.body).tag != 'rss':
            raise IndexerError('invalid_indexer_response')
        return {'title': caps['title'], 'protocol': config['preset'], 'browse': True,
            'search_advertised': True, 'direct_download_advertised': False, 'category': caps['category']}

    def browse(self, owner_id, connection_id, *, selection=None, query=None):
        snapshot = self.repository.connection_config(connection_id)
        config, revision = snapshot.config, snapshot.revision
        offset = 0
        if selection is not None:
            payload = self.repository.offer_payload(owner_id, selection, connection_id).offer
            if payload.get('kind') == 'indexer_page' and query is None:
                query, offset = payload['query'], payload['offset']
            elif payload.get('kind') != 'indexer_search':
                raise NotFound('Search selection is unavailable')
        if query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 500):
            raise IndexerError('invalid_search')
        caps = parse_caps(self._document(config, t='caps').body, config['category'])
        search = self.repository.create_offer(owner_id, connection_id, {'kind': 'indexer_search'}, expected_revision=revision)
        result = {'title': caps['title'], 'protocol': config['preset'], 'publications': [],
            'navigation': [], 'pagination': [], 'searches': [{'title': '', 'selection': search}], 'groups': [], 'facets': []}
        if query is None:
            return result
        document = self._document(config, t=caps['mode'], q=query.strip(), cat=config['category'], limit='50', offset=str(offset))
        root = xml_document(document.body)
        if root.tag != 'rss':
            raise IndexerError('invalid_indexer_response')
        clients = [row for row in self.repository.list_connections() if row.id == config['client_id'] and row.adapter in ('sabnzbd', 'nzbget', 'qbittorrent', 'transmission')]
        client = clients[0] if clients else None
        for item in root.findall('./channel/item')[:50]:
            title = (item.findtext('title') or 'Untitled release')[:512]
            enclosure = item.find('enclosure')
            href = enclosure.get('url') if enclosure is not None else None
            mime = enclosure.get('type', '').lower() if enclosure is not None else ''
            identity = item.findtext('guid') or href or title
            release_key = self.repository.box.display_identity(json.dumps([connection_id, revision, identity, client.id if client else None, client.revision if client else None]))
            publication = {'title': title, 'identity': release_key, 'authors': [], 'languages': [],
                'description': None, 'offers': [], 'navigation': []}
            transport = 'nzb' if mime in ('application/x-nzb', 'application/nzb') else 'torrent' if mime in ('application/x-bittorrent', 'application/x-torrent') or href and href.startswith('magnet:') else None
            compatible = client and client.adapter in (('sabnzbd', 'nzbget') if transport == 'nzb' else ('qbittorrent', 'transmission'))
            if transport and href and compatible:
                try:
                    if transport == 'torrent' and href.startswith('magnet:'):
                        from .torrent import validate_magnet
                        validate_magnet(href, tracker_origins=config.get('tracker_origins', []), secret=config['secret'])
                        descriptor = href
                    else:
                        descriptor = descriptor_url(config, href)
                except TransportError:
                    publication['unavailable_reason'] = 'untrusted_release_origin'
                    result['publications'].append(publication)
                    continue
                selection = self.repository.create_offer(owner_id, connection_id, {
                    'kind': 'acquisition', 'transport': transport, 'href': descriptor,
                    'media_type': 'application/x-nzb' if transport == 'nzb' else 'application/x-bittorrent',
                    'title': title, 'release_key': release_key,
                    'client_id': client.id, 'client_revision': client.revision}, expected_revision=revision)
                publication['offers'] = [{'format': 'NZB' if transport == 'nzb' else 'Torrent', 'label': None, 'identity': release_key,
                    'relation': 'download', 'offer_id': selection}]
            else:
                publication['unavailable_reason'] = 'torrent_client_required' if transport == 'torrent' else 'download_client_unavailable' if transport == 'nzb' else 'unsupported_release'
            result['publications'].append(publication)
        response = next((node for node in root.findall('./channel/*') if node.tag.split('}')[-1] == 'response'), None)
        try:
            total = int(response.get('total')) if response is not None else None
        except (ValueError, TypeError):
            raise IndexerError('invalid_indexer_response') from None
        count = min(50, len(root.findall('./channel/item')))
        has_next = count > 0 and (total is not None and offset + count < total or total is None and count == 50)
        if has_next and offset + count <= 1000000:
            page = self.repository.create_offer(owner_id, connection_id,
                {'kind': 'indexer_page', 'query': query.strip(), 'offset': offset + count}, expected_revision=revision)
            result['pagination'].append({'title': 'Next', 'relations': ['next'], 'selection': page})
        return result
