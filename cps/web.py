# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import json
import mimetypes
import chardet  # dependency of requests
import copy
import importlib
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path as _Path
from urllib.parse import urlencode

from flask import Blueprint, jsonify
from flask import request, redirect, send_from_directory, send_file, make_response, flash, abort, url_for, Response, g
from flask import session as flask_session
from flask_babel import gettext as _
from flask_babel import get_locale
from markupsafe import escape
from .cw_login import login_user, logout_user, current_user
from flask_limiter import RateLimitExceeded
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.sql.expression import text, func, false, not_, and_, or_, case
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.functions import coalesce
from werkzeug.datastructures import Headers
from werkzeug.security import generate_password_hash, check_password_hash

from . import constants, logger, isoLanguages, services, helper, spa, oauth_auto_redirect
from . import db, ub, config, app, user_library
from . import calibre_db, kobo_sync_status, hierarchy
from .services.ereader_send import (
    ereader_addresses, other_users_with_ereader, record_email_activity,
    send_includes_own_address,
)
from .services import app_passwords, ereader_scope, reading_position
from .services.read_status import stop_reading as stop_reading_status
from .search import render_search_results, render_adv_search_results
from .gdriveutils import getFileFromEbooksFolder, do_gdrive_download
from .helper import check_valid_domain, check_email, check_username, \
    get_book_cover, get_series_cover_thumbnail, get_download_link, send_mail, generate_random_password, \
    send_registration_mail, check_send_to_ereader, check_read_formats, tags_filters, reset_password, valid_email, \
    edit_book_read_status, valid_password, get_kosync_progress_display, get_sendable_book
from .pagination import Pagination
from .sort_orders import BOOK_SORT_ORDERS, book_sort_order, viewer_id
from .custom_column_sort import (
    load_configured_columns,
    resolve_magic_shelf_sort,
)
from .redirect import get_redirect_location
from .cw_babel import get_available_locale, get_available_translations, sanitize_locale_for_write
from .usermanagement import login_required_if_no_ano
from .ui_themes import config_theme_code
from .ui_font_preferences import seed_new_user_ui_font_defaults
from .kobo_sync_status import remove_synced_book
from . import magic_shelf
from .render_template import render_title_template, get_custom_column_visibility_options
from .kobo_sync_status import change_archived_books
from . import limiter, rate_limits
from .services.worker import WorkerThread
from .services.parallel import run_blocking as _run_blocking
from .tasks_status import render_task_status
from .usermanagement import user_login_required
from .string_helper import strip_whitespaces
from .logout import cleanup_local_logout
from .reader_settings import (
    reader_setting_int as _reader_setting_int,
    sanitize_reader_settings,
)
from .user_preferences import set_checkbox_preference_from_form
from .services import reader_fonts
