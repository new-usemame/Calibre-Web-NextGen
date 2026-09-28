"""#2343: the Calibre Database location is only locked where something else owns it.

A native Windows install opened /admin/dbconfig on first run and found the
metadata.db field read-only with its folder picker disabled, so the only way
to point the app at ``D:\\Books`` was editing app.db by hand. The lock exists
for the container, where the cwa-auto-library unit chooses the library at
every boot and would replace anything typed there. Without that unit (bare
metal, Windows) or with it switched off (DISABLE_LIBRARY_AUTOMOUNT), the field
is the only way to set the location.
"""
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest
from lxml import html

from cps import constants

pytestmark = pytest.mark.unit

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "cps" / "templates"


def _render_dbconfig(**context):
    environment = jinja2.Environment(
        loader=jinja2.ChoiceLoader([
            jinja2.DictLoader({
                "layout.html": "{% block flash %}{% endblock %}{% block body %}{% endblock %}",
            }),
            jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        ]),
        autoescape=True,
    )
    return environment.get_template("config_db.html").render(
        _=lambda message, **values: message % values if values else message,
        config=SimpleNamespace(config_calibre_dir=None, config_calibre_split=False,
                               config_calibre_split_dir=None, config_use_google_drive=False),
        csrf_token=lambda: "token",
        feature_support={"gdrive": False},
        backup_root="/config/backup",
        title="Database Configuration",
        url_for=lambda endpoint, **values: "/%s" % endpoint,
        **context,
    )


def _library_controls(**context):
    document = html.fromstring(_render_dbconfig(**context))
    [field] = document.xpath('//input[@id="config_calibre_dir"]')
    [picker] = document.xpath('//button[@data-link="config_calibre_dir"]')
    [split_picker] = document.xpath('//button[@data-link="config_calibre_split_dir"]')
    return document, field, picker, split_picker


def test_an_install_without_automount_can_type_and_browse_to_its_library():
    document, field, picker, split_picker = _library_controls(library_location_locked=False)

    assert field.get("readonly") is None
    assert picker.get("disabled") is None
    assert split_picker.get("disabled") is None
    # The docker-compose instructions describe a mechanism this install lacks.
    assert "docker-compose" not in document.text_content()


def test_the_container_keeps_the_location_owned_by_auto_library():
    document, field, picker, split_picker = _library_controls(library_location_locked=True)

    assert field.get("readonly") is not None
    assert picker.get("disabled") is not None
    assert split_picker.get("disabled") is None
    assert "docker-compose" in document.text_content()


@pytest.mark.parametrize("unit_installed, flag, locked", [
    (False, None, False),     # bare metal / Windows: nothing picks the library
    (False, "false", False),
    (True, None, True),       # the image: auto-library runs at every boot
    (True, "", True),
    (True, "false", True),
    (True, "0", True),
    (True, "true", False),    # the unit's own run script skips on these
    (True, "TRUE", False),
    (True, "Yes", False),
    (True, "1", False),
])
def test_the_lock_follows_whether_auto_library_will_run(tmp_path, monkeypatch,
                                                        unit_installed, flag, locked):
    unit = tmp_path / "cwa-auto-library"
    if unit_installed:
        unit.mkdir()
    monkeypatch.setattr(constants, "AUTO_LIBRARY_UNIT_DIR", str(unit))
    if flag is None:
        monkeypatch.delenv("DISABLE_LIBRARY_AUTOMOUNT", raising=False)
    else:
        monkeypatch.setenv("DISABLE_LIBRARY_AUTOMOUNT", flag)

    assert constants.library_location_is_automounted() is locked
