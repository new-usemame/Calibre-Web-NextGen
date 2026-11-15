"""SQL predicates and token normalization for custom-column restrictions.

Text and enumeration restrictions retain their persisted literal values.
Boolean restrictions use explicit three-state tokens so an absent or NULL
Calibre value can be restricted separately from ``False``.
"""

from sqlalchemy.sql.expression import and_, false, or_, true


RESTRICTION_DATATYPES = ("text", "enumeration", "bool")
BOOL_CHOICES = (
    ("true", "Yes"),
    ("false", "No"),
    ("undefined", "Undefined"),
)

_BOOL_TOKEN_ALIASES = {
    "true": "true",
    "yes": "true",
    "1": "true",
    "false": "false",
    "no": "false",
    "0": "false",
    "undefined": "undefined",
}


def normalize_bool_token(value):
    """Return a canonical Boolean state token, or ``None`` if invalid.

    ``None`` is not itself the Undefined token: callers must persist the
    explicit ``undefined`` state. Python bools and legacy ``True``/``False``
    strings are accepted for compatibility with existing bulk-save behavior.
    """
    if isinstance(value, bool):
        value = "true" if value else "false"
    if value is None:
        return None
    return _BOOL_TOKEN_ALIASES.get(str(value).strip().casefold())


def _tokens(values):
    if values is None or values == "":
        return []
    if isinstance(values, str):
        return values.split(",")
    return list(values)


def bool_tokens_valid(csv_or_list):
    """Whether a persisted Boolean restriction value contains only states.

    An empty value (including the legacy single empty-list item) means no
    restriction. Embedded empty tokens in a nonempty CSV are invalid.
    """
    values = _tokens(csv_or_list)
    if values == [""]:
        return True
    return all(normalize_bool_token(value) is not None for value in values)


def _bool_state_predicate(relationship, value_model, token):
    value = value_model.value
    if token == "true":
        return relationship.any(value.is_(True))
    if token == "false":
        return relationship.any(value.is_(False))
    # Undefined includes both a missing relationship row and a nullable row.
    return ~relationship.any(value.isnot(None))


def restriction_predicate(relationship, value_model, datatype, allowed, denied):
    """Build the common allowed/denied policy for one restricted column.

    Allowed states are ORed, denied states are ORed and excluded. Invalid
    Boolean tokens fail closed for the whole predicate; a malformed saved
    restriction must never broaden the visible library.
    """
    allowed_values = _tokens(allowed)
    denied_values = _tokens(denied)

    if datatype == "bool":
        if (not bool_tokens_valid(allowed_values)
                or not bool_tokens_valid(denied_values)):
            return false()
        allowed_tokens = [
            normalize_bool_token(value)
            for value in allowed_values if value != ""
        ]
        denied_tokens = [
            normalize_bool_token(value)
            for value in denied_values if value != ""
        ]
        allowed_filter = (
            true() if not allowed_tokens else or_(
                *(_bool_state_predicate(relationship, value_model, token)
                  for token in allowed_tokens)
            )
        )
        denied_filter = (
            false() if not denied_tokens else or_(
                *(_bool_state_predicate(relationship, value_model, token)
                  for token in denied_tokens)
            )
        )
    else:
        allowed_values = [str(value) for value in allowed_values]
        denied_values = [str(value) for value in denied_values]
        allowed_filter = (
            true() if allowed_values == [""] or not allowed_values
            else relationship.any(value_model.value.in_(allowed_values))
        )
        denied_filter = (
            false() if denied_values == [""] or not denied_values
            else relationship.any(value_model.value.in_(denied_values))
        )

    return and_(allowed_filter, ~denied_filter)
