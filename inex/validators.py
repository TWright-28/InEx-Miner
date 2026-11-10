import os
from datetime import date, datetime

from inex.errors import InexError

END_OF_DAY = "T23:59:59Z"
ISO_DATE_LENGTH = 10
ORDERS = ("asc", "desc")
FORMATS = ("csv", "json")


def _reject(message):
    raise InexError("invalid_input", message)


def parse_repo(text):
    parts = (text or "").strip().strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        _reject(f"Repository must look like owner/repo, got {text!r}.")
    return parts[0], parts[1]


def _parse_when(text, field):
    if text is None:
        return None
    value = str(text).strip()
    try:
        if len(value) == ISO_DATE_LENGTH:
            date.fromisoformat(value)
            return value
        if len(value) > ISO_DATE_LENGTH and value[ISO_DATE_LENGTH] == "T":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
    except ValueError:
        pass
    _reject(f"{field} must be YYYY-MM-DD or an ISO 8601 timestamp, got {text!r}.")


def parse_start(text):
    return _parse_when(text, "Start date")


def parse_end(text):
    value = _parse_when(text, "End date")
    if value is not None and len(value) == ISO_DATE_LENGTH:
        return value + END_OF_DAY
    return value


def parse_order(text):
    if text is None:
        return "desc"
    value = str(text).strip().lower()
    if value not in ORDERS:
        _reject(f"Order must be 'asc' or 'desc', got {text!r}.")
    return value


def parse_count(value):
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        _reject(f"Count must be a positive whole number, got {value!r}.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        _reject(f"Count must be a positive whole number, got {value!r}.")
    if number < 1:
        _reject(f"Count must be a positive whole number, got {value!r}.")
    return number


def parse_filename(text):
    if text is None:
        return None
    if os.path.basename(text) != text or "/" in text or "\\" in text or not text.strip():
        _reject(
            f"File name must be a plain name with no folders, got {text!r}; "
            "exports are always written to the exports folder."
        )
    return text


def parse_format(text):
    if text is None:
        return "csv"
    value = str(text).strip().lower()
    if value not in FORMATS:
        _reject(f"Format must be 'csv' or 'json', got {text!r}.")
    return value
