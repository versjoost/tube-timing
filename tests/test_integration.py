import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tube_timing import cli


def _run_silenced(callable_obj, *args, **kwargs) -> int:
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        return callable_obj(*args, **kwargs)


@unittest.skipUnless(os.getenv("TFL_API_KEY"), "TFL_API_KEY not set")
class TubeTimingIntegrationTests(unittest.TestCase):
    def test_now_with_line_filters(self) -> None:
        cases = [
            ("Waterloo", ["jubilee", "northern"]),
            ("Totteridge & Whetstone", ["northern"]),
        ]
        for station, lines in cases:
            with self.subTest(station=station):
                code = _run_silenced(
                    cli.cmd_now,
                    station,
                    "10m",
                    "tube",
                    lines,
                    False,
                    None,
                    None,
                    None,
                )
                self.assertEqual(code, 0)

    def test_list_with_line_filter(self) -> None:
        code = _run_silenced(
            cli.cmd_list, "Oxford Circus", "tube", ["victoria"], False, None
        )
        self.assertEqual(code, 0)
