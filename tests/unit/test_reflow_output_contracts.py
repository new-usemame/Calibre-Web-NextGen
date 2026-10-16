"""The digest helper rejects both absent and changed exact synthetic output."""
from hashlib import sha256
import pytest
from tests.fixtures.reflow_output_contracts import assert_authored_substring

pytestmark = pytest.mark.unit


def test_exact_authored_output_contract_rejects_deleted_or_changed_text():
    expected = 'Fixture-only blue bracket.'
    digest = sha256(expected.encode('utf-8')).hexdigest()
    assert_authored_substring('prefix ' + expected + ' suffix', digest, len(expected))
    for damaged in ['prefix suffix', 'Fixture-only red bracket.',
                    expected.lower(), expected.replace(' ', '  '), expected[:-1]]:
        with pytest.raises(AssertionError):
            assert_authored_substring(damaged, digest, len(expected))
