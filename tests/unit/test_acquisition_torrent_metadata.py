# SPDX-License-Identifier: GPL-3.0-or-later
"""Reviewed v1 metadata fields preserve identity without admitting new semantics."""
import base64
import hashlib
import importlib
import json

import pytest

from tests.unit.test_acquisition_clients import clients, config, document
from tests.unit.test_acquisition_usenet import spec

pytestmark = pytest.mark.unit
PAYLOAD = b"Original legal fixture book bytes."


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b"e"


def metadata(*, multi=False):
    info = {"name": "Owned bundle" if multi else "Owned book.epub",
            "piece length": 16384, "pieces": hashlib.sha1(PAYLOAD).digest(), "private": 1}
    row = {"length": len(PAYLOAD), "sha1": hashlib.sha1(PAYLOAD).digest(), "attr": b"hx"}
    if multi:
        info["files"] = [dict(row, path=["Owned book.epub"]), {"length": 0, "path": ["empty.txt"]}]
    else:
        info.update(row)
    return {"announce": "http://tracker.example/announce", "info": info}


def validator():
    return importlib.import_module(spec.name + ".torrent")


@pytest.mark.parametrize("multi", [False, True])
def test_reviewed_checksum_and_attributes_preserve_exact_raw_v1_identity(multi):
    data = metadata(multi=multi)
    expected = hashlib.sha1(bencode(data["info"])).hexdigest()
    assert validator().validate_torrent(bencode(data), tracker_origins=["http://tracker.example"]) == expected
    # The optional hint is not canonical integrity or CWNG artifact identity.
    target = data["info"]["files"][0] if multi else data["info"]
    target["sha1"] = b"x" * 20
    assert validator().validate_torrent(bencode(data), tracker_origins=["http://tracker.example"]) == hashlib.sha1(bencode(data["info"])).hexdigest()


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("field,value", [("sha1", b"x" * 19), ("sha1", b"x" * 21), ("sha1", 20), ("attr", 1), ("attr", b"l"), ("symlink path", ["target.epub"])])
def test_optional_file_metadata_cannot_admit_malformed_or_symlink_semantics(multi, field, value):
    data = metadata(multi=multi)
    target = data["info"]["files"][0] if multi else data["info"]
    target[field] = value
    with pytest.raises(validator().TransportError):
        validator().validate_torrent(bencode(data), tracker_origins=["http://tracker.example"])


@pytest.mark.parametrize("mutate", [
    lambda i: i.update(pieces=b"x" * 40),
    lambda i: i.update(length=16385),
    lambda i: i.update(private=2),
    lambda i: i.update(private=b"1"),
    lambda i: i.update(attr=b"p"),
])
def test_single_file_shape_must_match_the_supported_v1_contract(mutate):
    data = metadata()
    # Strip the new fields so an old validator cannot pass this negative gate
    # merely by rejecting an as-yet unsupported checksum/attribute extension.
    data["info"].pop("sha1"); data["info"].pop("attr")
    mutate(data["info"])
    with pytest.raises(validator().TransportError):
        validator().validate_torrent(bencode(data), tracker_origins=["http://tracker.example"])


@pytest.mark.parametrize("regular_bytes", [0, 1])
def test_multifile_padding_is_piece_space_but_not_the_only_payload(regular_bytes):
    data = metadata(multi=True)
    data["info"]["files"] = [{"length": 16384, "path": [".pad", "16384"], "attr": b"p"},
                             {"length": regular_bytes, "path": ["book.epub"]}]
    data["info"]["pieces"] = b"x" * (40 if regular_bytes else 20)
    if regular_bytes:
        assert validator().validate_torrent(bencode(data)) == hashlib.sha1(bencode(data["info"])).hexdigest()
    else:
        with pytest.raises(validator().TransportError):
            validator().validate_torrent(bencode(data))


@pytest.mark.parametrize("adapter", ["qbittorrent", "transmission"])
def test_client_submission_keeps_reviewed_metainfo_bytes_and_hash(tmp_path, adapter):
    data = metadata(multi=True)
    raw = bencode(data); expected = hashlib.sha1(bencode(data["info"])).hexdigest(); submitted = []
    def transfer(url, policy, **kw):
        if adapter == "qbittorrent":
            if url.endswith("/auth/login"):
                return document(b"Ok.", headers={"set-cookie": "SID=owned; HttpOnly"})
            if "/torrents/info?" in url:
                return document([])
            if url.endswith("/torrents/add"):
                submitted.append(kw["upload"][1]); return document(b"Ok.")
        args = json.loads(kw["body"])
        if args["method"] == "torrent-get":
            assert args["arguments"]["ids"] == [expected]
            return document({"result": "success", "arguments": {"torrents": []}})
        assert args["method"] == "torrent-add"
        submitted.append(base64.b64decode(args["arguments"]["metainfo"]))
        return document({"result": "success", "arguments": {"torrent-added": {"hashString": expected}}})
    client = clients().CLIENTS[adapter](config(tmp_path, adapter), transfer=transfer)
    assert client.submit("cwng-owned", raw) == expected
    assert submitted == [raw]
