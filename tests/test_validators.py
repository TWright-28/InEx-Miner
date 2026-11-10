import pytest

from inex.errors import InexError
from inex.validators import (
    parse_count,
    parse_end,
    parse_filename,
    parse_format,
    parse_order,
    parse_repo,
    parse_start,
)


def rejects(fn, value):
    with pytest.raises(InexError) as e:
        fn(value)
    assert e.value.code == "invalid_input"


@pytest.mark.parametrize(
    "text, expected",
    [("facebook/react", ("facebook", "react")), (" facebook/react/ ", ("facebook", "react"))],
)
def test_parse_repo(text, expected):
    assert parse_repo(text) == expected


@pytest.mark.parametrize("text", ["react", "a/b/c", "/react", "facebook/", "", None])
def test_parse_repo_rejects(text):
    rejects(parse_repo, text)


def test_dates():
    assert parse_start("2020-01-05") == "2020-01-05"
    assert parse_end("2020-01-31") == "2020-01-31T23:59:59Z"
    assert parse_end("2020-01-31T10:00:00Z") == "2020-01-31T10:00:00Z"
    assert parse_start("2020-01-31T10:00:00+00:00") == "2020-01-31T10:00:00+00:00"
    assert parse_start(None) is None
    assert parse_end(None) is None


@pytest.mark.parametrize("text", ["2020-1-5", "31/01/2020", "yesterday"])
def test_bad_dates_rejected(text):
    rejects(parse_start, text)
    rejects(parse_end, text)


def test_parse_order():
    assert parse_order(None) == "desc"
    assert parse_order("ASC") == "asc"
    assert parse_order("desc") == "desc"
    rejects(parse_order, "up")


def test_parse_count():
    assert parse_count("5") == 5
    assert parse_count(5) == 5
    assert parse_count(None) is None
    for bad in (0, -1, "x", 2.5):
        rejects(parse_count, bad)


def test_parse_filename(tmp_path):
    assert parse_filename("results") == "results"
    assert parse_filename(None) is None
    for bad in ("../x", "a/b", "a" + chr(92) + "b", str(tmp_path / "abs")):
        rejects(parse_filename, bad)


def test_parse_format():
    assert parse_format(None) == "csv"
    assert parse_format("JSON") == "json"
    rejects(parse_format, "xml")
