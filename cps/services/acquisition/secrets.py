# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit scoped encryption; importing this module never opens a key file."""
import base64
import hashlib
import hmac
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretError(ValueError):
    """Errors intentionally omit key material, ciphertext and caller values."""


@dataclass(frozen=True)
class SealedValue:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    version: int = 1


class SecretBox:
    def __init__(self, key, max_bytes=2 * 1024 * 1024):
        if not isinstance(key, bytes) or len(key) != 32:
            raise SecretError("Acquisition key must contain exactly 32 bytes")
        if type(max_bytes) is not int or max_bytes < 1:
            raise SecretError("Invalid encrypted value size limit")
        self._cipher = AESGCM(key)
        self._identity_key = hmac.digest(key, b"cwng-acquisition-display-identity-v1", "sha256")
        self.max_bytes = max_bytes

    def display_identity(self, value):
        """Stable private-data fingerprint for UI reconciliation, never a grant.

        A separate derived key prevents exposing raw hashes of private URLs.
        Callers include account/connection and a purpose in the encoded value.
        """
        if not isinstance(value, str):
            raise SecretError("Identity input must be text")
        data = value.encode("utf-8")
        if len(data) > self.max_bytes:
            raise SecretError("Identity input exceeds size limit")
        return hmac.new(self._identity_key, data, hashlib.sha256).hexdigest()

    @staticmethod
    def _aad(scope, identity, field_name):
        if any(not isinstance(value, str) or not value or len(value) > 256
               for value in (scope, identity, field_name)):
            raise SecretError("Invalid credential binding")
        return json.dumps(["cwng-acquisition", 1, scope, identity, field_name],
                          separators=(",", ":")).encode("utf-8")

    def seal(self, plaintext, *, scope, identity, field_name):
        if not isinstance(plaintext, str):
            raise SecretError("Encrypted value must be text")
        try:
            data = plaintext.encode("utf-8")
        except UnicodeError as exc:
            raise SecretError("Invalid encrypted value encoding") from None
        if len(data) > self.max_bytes:
            raise SecretError("Encrypted value exceeds size limit")
        nonce = os.urandom(12)
        return SealedValue(self._cipher.encrypt(
            nonce, data, self._aad(scope, identity, field_name)), nonce)

    def open(self, sealed, *, scope, identity, field_name):
        if sealed.version != 1:
            raise SecretError("Unsupported encrypted value version")
        if len(sealed.ciphertext) > self.max_bytes + 16:
            raise SecretError("Encrypted value exceeds size limit")
        try:
            return self._cipher.decrypt(
                sealed.nonce, sealed.ciphertext,
                self._aad(scope, identity, field_name)).decode("utf-8")
        except (InvalidTag, ValueError, UnicodeError) as exc:
            raise SecretError("Encrypted value could not be authenticated") from None


def _decode_key(encoded):
    try:
        decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise SecretError("Invalid encoded acquisition key") from None
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != encoded:
        raise SecretError("Invalid encoded acquisition key")
    return decoded


def load_or_create_key(path, *, explicit_base64=None, allow_create=True):
    """Caller supplies the configured path; never replace an existing key.

    Back up this file with app.db. An unreadable/corrupt existing key fails
    closed. Runtime must pass allow_create=False when encrypted rows already
    exist, so losing the key cannot silently create an unrelated replacement.
    Supplying explicit_base64 does not touch the filesystem.
    """
    if explicit_base64 is not None:
        try:
            return _decode_key(explicit_base64.encode("ascii"))
        except (AttributeError, UnicodeError) as exc:
            raise SecretError("Invalid encoded acquisition key") from None
    path = Path(path)
    temporary = None
    fd = None
    try:
        if allow_create:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".acquisition-key-", dir=path.parent)
            os.fchmod(fd, 0o600)
            key = AESGCM.generate_key(bit_length=256)
            encoded = base64.urlsafe_b64encode(key) + b"\n"
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SecretError("Acquisition key must be a regular file")
        os.fchmod(fd, 0o600)
        encoded = os.read(fd, 257)
        if len(encoded) > 256 or os.read(fd, 1):
            raise SecretError("Acquisition key file exceeds size limit")
        return _decode_key(encoded.strip())
    except OSError as exc:
        raise SecretError("Acquisition key file is unavailable") from None
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
