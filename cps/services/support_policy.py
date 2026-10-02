"""Policy and validation for the site support destinations (issue #1402)."""

import unicodedata
from urllib.parse import urlsplit


MAX_SUPPORT_URL_LENGTH = 2048
MAX_SUPPORT_LABEL_LENGTH = 80


def validate_support_settings(url, label):
    """Validate the administrator-supplied support destination and label.

    Return normalized strings. A blank URL disables the host destination, but
    validation is independent of the hide-project-links toggle so an unsafe
    value cannot be stored and activated later.
    """
    if not isinstance(url, str) or not isinstance(label, str):
        raise ValueError("Support URL and label must be text")

    if _has_control_characters(url) or _has_control_characters(label):
        raise ValueError("Support URL and label cannot contain control characters")

    url = url.strip()
    label = label.strip()
    if len(url) > MAX_SUPPORT_URL_LENGTH:
        raise ValueError("Support URL must be at most 2048 characters")
    if len(label) > MAX_SUPPORT_LABEL_LENGTH:
        raise ValueError("Support label must be at most 80 characters")
    if url:
        try:
            parsed = urlsplit(url)
            # Accessing port validates malformed numeric ports as well.
            _ = parsed.port
        except ValueError as error:
            raise ValueError("Support URL must be an absolute HTTP or HTTPS URL") from error
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(character.isspace() for character in url)
        ):
            raise ValueError("Support URL must be an absolute HTTP or HTTPS URL without credentials")

    return url, label


def support_policy(settings, *, is_admin=False, contact_support_label="Contact support"):
    """Return the support links a user should see in classic and SPA chrome."""
    if is_admin:
        return {"show_project_links": True, "url": None, "label": None}

    configured_visibility = getattr(settings, "config_show_project_support", True)
    show_project_links = bool(configured_visibility) if configured_visibility is not None else True
    configured_url = getattr(settings, "config_support_url", "")
    configured_label = getattr(settings, "config_support_label", "")
    # A few API bootstrap paths intentionally use sparse/mock config objects.
    # Treat anything other than persisted strings as unset rather than leaking
    # a mock/proxy object's repr into the JSON contract or href.
    try:
        url, label = validate_support_settings(
            configured_url if isinstance(configured_url, str) else "",
            configured_label if isinstance(configured_label, str) else "",
        )
    except ValueError:
        # A hand-edited/old invalid value never becomes a navigable link.
        url, label = "", ""
    return {
        "show_project_links": show_project_links,
        "url": url or None,
        "label": (label or contact_support_label) if url else None,
    }


def _has_control_characters(value):
    return any(unicodedata.category(character) == "Cc" for character in value)
