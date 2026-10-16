"""Exact authored-output checks without copying generated EPUB prose into fixtures."""
from hashlib import sha256


def has_authored_substring(text, expected_sha256, expected_length):
    """Match exact UTF-8 bytes of a substring of expected_length codepoints.

    Callers record the converter author location alongside the expected digest.
    No whitespace, case, punctuation or HTML normalization is performed.
    """
    return any(sha256(text[start:start + expected_length].encode('utf-8')).hexdigest()
               == expected_sha256
               for start in range(len(text) - expected_length + 1))


def assert_authored_substring(text, expected_sha256, expected_length):
    assert has_authored_substring(text, expected_sha256, expected_length), (
        'Exact authored output is missing or changed', expected_sha256, expected_length)
