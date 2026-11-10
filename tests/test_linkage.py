import pytest

from inex.services.linkage import (
    is_prerelease,
    live_version_at,
    parse_timestamp,
    sort_versions_desc,
)


@pytest.fixture
def pick():
    def _pick(times, issue_date):
        ordered = sort_versions_desc(list(times))
        got = live_version_at(issue_date, [(v, times.get(v)) for v in ordered])
        return got[0] if got else None

    return _pick


class TestPublishOrder:
    def test_backport_published_after_a_major_wins(self, pick):
        times = {
            "1.9.0": "2019-06-01T00:00:00.000Z",
            "2.0.0": "2020-01-01T00:00:00.000Z",
            "1.9.5": "2020-06-01T00:00:00.000Z",
        }
        assert pick(times, "2020-07-01T00:00:00Z") == "1.9.5"

    def test_normal_history_is_unaffected(self, pick):
        times = {
            "1.0.0": "2019-01-01T00:00:00.000Z",
            "1.1.0": "2019-06-01T00:00:00.000Z",
            "2.0.0": "2020-01-01T00:00:00.000Z",
        }
        assert pick(times, "2019-08-01T00:00:00Z") == "1.1.0"
        assert pick(times, "2021-01-01T00:00:00Z") == "2.0.0"

    def test_issue_predating_every_release_is_unlinkable(self, pick):
        times = {"1.0.0": "2019-01-01T00:00:00.000Z"}
        assert pick(times, "2018-01-01T00:00:00Z") is None


class TestPrereleases:
    def test_a_newer_prerelease_does_not_displace_the_stable_release(self, pick):
        times = {"2.0.0": "2020-01-01T00:00:00.000Z", "2.1.0-next.999": "2020-06-01T00:00:00.000Z"}
        assert pick(times, "2020-07-01T00:00:00Z") == "2.0.0"

    def test_prerelease_used_when_no_stable_exists_yet(self, pick):
        times = {"1.0.0-beta.1": "2020-01-01T00:00:00.000Z"}
        assert pick(times, "2020-07-01T00:00:00Z") == "1.0.0-beta.1"

    def test_prerelease_detection(self):
        assert is_prerelease("2.1.0-next.999")
        assert not is_prerelease("2.1.0")


class TestTimestamps:
    def test_release_half_a_second_after_the_issue_is_excluded(self, pick):
        times = {"1.0.0": "2019-01-01T00:00:00.000Z", "1.1.0": "2020-01-01T09:00:00.500Z"}
        assert pick(times, "2020-01-01T09:00:00Z") == "1.0.0"

    def test_identical_instant_in_both_formats_is_included(self, pick):
        times = {"1.0.0": "2020-01-01T09:00:00.000Z"}
        assert pick(times, "2020-01-01T09:00:00Z") == "1.0.0"

    def test_formats_parse_to_the_same_instant(self):
        assert parse_timestamp("2020-01-01T00:00:00.000Z") == parse_timestamp(
            "2020-01-01T00:00:00Z"
        )

    def test_bare_date_is_comparable_with_an_aware_timestamp(self):
        assert parse_timestamp("2020-01-01") < parse_timestamp("2020-06-01T00:00:00.000Z")

    def test_unparseable_values_return_none(self):
        assert parse_timestamp("not-a-date") is None
        assert parse_timestamp(None) is None
        assert parse_timestamp("") is None

    def test_missing_publish_time_is_skipped_not_fatal(self, pick):
        times = {"1.0.0": None, "0.9.0": "2019-01-01T00:00:00.000Z"}
        assert pick(times, "2020-01-01T00:00:00Z") == "0.9.0"
