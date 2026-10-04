# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe attachment names; library paths and OPDS preferences remain separate."""
from flask_babel import gettext as _
from .opds_filename import validate_template


def validate_mail_filename_template(template):
    try:
        validate_template(template)
    except ValueError:
        raise ValueError(_("Use a valid attachment filename template with supported metadata fields.")) from None
