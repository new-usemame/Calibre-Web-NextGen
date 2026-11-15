# SPDX-License-Identifier: GPL-3.0-or-later
"""One Calibre tag field keeps its name across editing, browsing and OPDS (#1511)."""
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask, request
from flask_babel import Babel, gettext
from werkzeug.routing import Rule

pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def compiled_catalogs(tmp_path_factory):
    root = tmp_path_factory.mktemp('tag-naming-translations')
    for locale in ['nl', 'fr']:
        source = Path(__file__).resolve().parents[2] / 'cps' / 'translations' / locale / 'LC_MESSAGES' / 'messages.po'
        target = root / locale / 'LC_MESSAGES' / 'messages.mo'
        target.parent.mkdir(parents=True)
        subprocess.run(['msgfmt', '-o', str(target), str(source)], check=True)
    return root


@pytest.fixture(params=['en', 'nl', 'fr'])
def naming_context(monkeypatch, compiled_catalogs, request):
    from cps import render_template as rendering
    app = Flask(__name__)
    locale = request.param
    Babel(app, default_translation_directories=str(compiled_catalogs), locale_selector=lambda: locale)
    user = SimpleNamespace(id=1, is_anonymous=True, has_own_library=False,
                           role_admin=lambda: False, role_edit=lambda: False,
                           filter_language=lambda: 'all', view_settings={})
    query = MagicMock()
    for method in ['filter', 'order_by']:
        getattr(query, method).return_value = query
    query.all.return_value = []
    session = MagicMock()
    session.query.return_value = query
    monkeypatch.setattr(rendering, 'current_user', user)
    monkeypatch.setattr(rendering.ub, 'session', session)
    monkeypatch.setattr(rendering, 'get_custom_column_sidebar_entries', lambda: [])
    with app.test_request_context('/'):
        yield rendering


def test_classic_sidebar_uses_the_editor_tag_name(naming_context):
    sidebar, _ = naming_context.get_sidebar_config()
    entry = next(row for row in sidebar if row['id'] == 'cat')
    assert entry['text'] == gettext('Tags')
    assert entry['link'] == 'web.category_list'


def test_opds_root_and_qualified_feeds_use_the_editor_tag_name(naming_context):
    from cps import opds
    root = opds.OPDS_ROOT_ENTRY_DEFS['categories']
    assert str(root['title']) == gettext('Tags')
    assert root['endpoint'] == 'opds.feed_categoryindex'
    request.url_rule = Rule('/opds/category/F', endpoint='opds.feed_letter_category')
    assert str(opds._feed_title_with_letter('F')) == gettext('Tags') + ' (F)'
    request.url_rule = Rule('/opds/category/5', endpoint='opds.feed_category')
    assert str(opds._feed_title_with_name('Fantasy')) == gettext('Tags') + ': Fantasy'



_LOCALES = sorted(p.parent.parent.name for p in (Path(__file__).resolve().parents[2] / 'cps' / 'translations').glob('*/LC_MESSAGES/messages.po'))
_TAG_COPY = [
    'Show Tags Section', 'Enter Tags', 'Tags in this Library',
    'Tags: %(name)s', 'Books grouped by tags',
    'Multiple values are supported for Authors, Tags, Languages, and Publishers. Use comma-separated values (e.g., "Name1, Name2").',
]


@pytest.mark.parametrize('locale', _LOCALES)
def test_changed_tag_copy_survives_actual_gettext_compilation(locale):
    """A fuzzy, omitted or empty renamed entry must fail as English fallback."""
    import gettext as stdlib_gettext
    from io import BytesIO
    po = Path(__file__).resolve().parents[2] / 'cps' / 'translations' / locale / 'LC_MESSAGES' / 'messages.po'
    compiled = subprocess.run(['msgfmt', '-o', '-', str(po)], capture_output=True, check=True).stdout
    catalog = stdlib_gettext.GNUTranslations(BytesIO(compiled))
    # German and Brazilian Portuguese deliberately borrow the word Tags.
    # Presence in the compiled runtime map distinguishes that valid translation
    # from an absent/fuzzy entry falling through to the English fallback.
    missing = [message for message in _TAG_COPY if message not in catalog._catalog]
    assert not missing, (locale, missing)
    assert catalog.gettext('Tags: %(name)s') % {'name': 'Fantasy'} != catalog.gettext('Tags: %(name)s')
