# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import re


def strip_whitespaces(text):
    return re.sub(r"(^[\s\u200B-\u200D\ufeff]+)|([\s\u200B-\u200D\ufeff]+$)","", text)



def title_sort_name(title, regex):
    """Apply the configured library article rule to a metadata name."""
    if title is None:
        return ''
    try:
        if regex:
            match = re.compile(regex, re.IGNORECASE).search(title)
            if match:
                article = match.group(1)
                title = title[len(article):] + ', ' + article
    except Exception:
        # Preserve the library UDF fallback even for regex size/depth errors.
        pass
    return strip_whitespaces(title)
