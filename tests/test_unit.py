import sys
import unittest
from datetime import date, timezone
from pathlib import Path
from unittest.mock import Mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tube_timing.api import StopPointMatch, TflApiError, TflClient
from tube_timing.cli import (
    build_towards_needles,
    choose_station_match,
    resolve_station_query,
)
from tube_timing.departures import normalize_name, parse_time_of_day


class DeparturesUnitTests(unittest.TestCase):
    def test_parse_time_of_day_invalid_minutes_returns_none(self) -> None:
        when = parse_time_of_day("12:99", date(2026, 2, 7), timezone.utc)
        self.assertIsNone(when)

    def test_parse_time_of_day_invalid_token_returns_none(self) -> None:
        when = parse_time_of_day("12:xx", date(2026, 2, 7), timezone.utc)
        self.assertIsNone(when)


class TowardsAliasUnitTests(unittest.TestCase):
    def test_build_towards_needles_expands_alias_to_canonical(self) -> None:
        needles = build_towards_needles("cx")
        self.assertIn("cx", needles)
        self.assertIn("charing cross", needles)


class StationMatchUnitTests(unittest.TestCase):
    def test_resolve_station_query_alias(self) -> None:
        self.assertEqual(resolve_station_query("TCR"), "Tottenham Court Road")

    def test_normalize_name_removes_station_suffixes(self) -> None:
        self.assertEqual(
            normalize_name("Tottenham Court Road Underground Station"),
            "tottenham court road",
        )

    def test_choose_station_match_supports_acronyms(self) -> None:
        matches = [
            StopPointMatch(
                id="940GZZLUBTX",
                name="Brent Cross Underground Station",
                modes=["tube"],
            ),
            StopPointMatch(
                id="940GZZLUTCR",
                name="Tottenham Court Road Underground Station",
                modes=["tube"],
            ),
        ]
        match, used_fallback = choose_station_match("TCR", matches)
        self.assertEqual(match.name, "Tottenham Court Road Underground Station")
        self.assertTrue(used_fallback)


class ApiClientUnitTests(unittest.TestCase):
    def test_get_wraps_request_exception(self) -> None:
        client = TflClient(api_key="key")
        client.session.get = Mock(side_effect=requests.Timeout("boom"))
        with self.assertRaises(TflApiError):
            client.get("/StopPoint/Search/test")

    def test_get_wraps_invalid_json(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json = Mock(side_effect=ValueError("bad json"))
        client = TflClient(api_key="key")
        client.session.get = Mock(return_value=response)
        with self.assertRaises(TflApiError):
            client.get("/StopPoint/Search/test")

    def test_get_keeps_http_error_message(self) -> None:
        response = Mock()
        response.status_code = 502
        response.text = "upstream unavailable"
        client = TflClient(api_key="key")
        client.session.get = Mock(return_value=response)
        with self.assertRaises(TflApiError) as exc:
            client.get("/StopPoint/Search/test")
        self.assertIn("502", str(exc.exception))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
