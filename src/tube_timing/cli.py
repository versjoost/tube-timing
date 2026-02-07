import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

from .api import (
    TflApiError,
    TflClient,
    get_arrivals,
    get_line_timetable,
    get_stop_point,
    get_stop_point_timetable,
    search_stop_points,
)
from .departures import (
    arrivals_to_departures,
    compact_destination,
    Departure,
    format_departure,
    london_tz,
    merge_departures,
    normalize_name,
    parse_window,
    timetable_destinations,
    timetable_to_departures,
)


class FriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        required_prefix = "the following arguments are required:"
        if required_prefix in message:
            missing_raw = message.split(required_prefix, 1)[1].strip()
            missing = [item.strip() for item in missing_raw.split(",") if item.strip()]
            self.print_usage(sys.stderr)
            for arg in missing:
                if arg == "window":
                    print(
                        "error: Missing required argument: window (example: 10m or 1h30m).",
                        file=sys.stderr,
                    )
                elif arg == "station":
                    print(
                        "error: Missing required argument: station (example: \"Oxford Circus\").",
                        file=sys.stderr,
                    )
                elif arg == "command":
                    print(
                        "error: Missing command (try `now`, `list`, or `env`).",
                        file=sys.stderr,
                    )
                else:
                    print(f"error: Missing required argument: {arg}.", file=sys.stderr)
            if "window" in missing or "station" in missing:
                print(
                    "Example: tube-timing now \"Totteridge & Whetstone\" 10m",
                    file=sys.stderr,
                )
            self.exit(2)
        super().error(message)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = FriendlyArgumentParser(
        prog="tube-timing",
        description="Check upcoming TfL departures for a station.",
        epilog=(
            "Examples:\n"
            "  tube-timing env\n"
            "  tube-timing now \"Regent's Park\" 10m\n"
            "  tube-timing now \"Waterloo\" 30m --line jubilee --direction southbound\n"
            "  tube-timing list \"Oxford Circus\" --line victoria"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    now_parser = subparsers.add_parser(
        "now",
        help="Show expected departures",
        description="Show live and scheduled departures in a future window.",
        epilog=(
            "Examples:\n"
            "  tube-timing now \"Regent's Park\" 30m\n"
            "  tube-timing now \"Regent's Park\" 60m --direction southbound\n"
            "  tube-timing now \"Waterloo\" 60m --line jubilee --line northern\n"
            "  tube-timing now \"Bank\" 20m --towards \"Charing Cross\""
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    now_parser.add_argument("station", help="Station name, e.g. Totteridge & Whetstone")
    now_parser.add_argument("window", help="Time window, e.g. 30m or 1h30m")
    now_parser.add_argument("--mode", default="tube", help="TFL mode filter")
    now_parser.add_argument(
        "--line",
        "-l",
        action="append",
        help="Filter by line name/id (repeatable or comma-separated)",
    )
    now_parser.add_argument(
        "--full-timetable",
        action="store_true",
        help="Allow per-line timetables on stations with many lines (may be slow)",
    )
    now_parser.add_argument(
        "--direction",
        help="Filter by direction (inbound/outbound or northbound/southbound/etc)",
    )
    now_parser.add_argument(
        "--towards", help="Filter by destination text, e.g. Morden or High Barnet"
    )
    now_parser.add_argument(
        "--debug",
        nargs="?",
        const="tube-timing-debug.json",
        help="Write raw API payloads to JSON (optional path)",
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List available directions and destinations",
        description="List available directions and destinations for a station.",
        epilog=(
            "Examples:\n"
            "  tube-timing list \"Oxford Circus\"\n"
            "  tube-timing list \"Oxford Circus\" --line victoria\n"
            "  tube-timing list \"Waterloo\" --direction southbound"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    list_parser.add_argument("station", help="Station name, e.g. Totteridge & Whetstone")
    list_parser.add_argument("--mode", default="tube", help="TFL mode filter")
    list_parser.add_argument(
        "--line",
        "-l",
        action="append",
        help="Filter by line name/id (repeatable or comma-separated)",
    )
    list_parser.add_argument(
        "--full-timetable",
        action="store_true",
        help="Allow per-line timetables on stations with many lines (may be slow)",
    )
    list_parser.add_argument(
        "--direction",
        help="Filter destinations by direction (inbound/outbound or northbound)",
    )

    subparsers.add_parser("env", help="Check API environment variables")

    arg_list = sys.argv[1:] if argv is None else list(argv)
    if not arg_list:
        parser.print_help()
        return 0

    args = parser.parse_args(arg_list)

    if args.command == "env":
        return cmd_env()
    if args.command == "now":
        return cmd_now(
            args.station,
            args.window,
            args.mode,
            args.line,
            args.full_timetable,
            args.direction,
            args.towards,
            args.debug,
        )
    if args.command == "list":
        return cmd_list(
            args.station, args.mode, args.line, args.full_timetable, args.direction
        )

    parser.print_help()
    return 1


def _station_initials(name: str) -> str:
    words = normalize_name(name).split()
    if len(words) < 2:
        return ""
    return "".join(word[0] for word in words if word)


def choose_station_match(
    station_query: str, matches: List[Any]
) -> tuple[Any, bool]:
    station_norm = normalize_name(station_query)
    for candidate in matches:
        if normalize_name(candidate.name) == station_norm:
            return candidate, False

    query_token = re.sub(r"[^a-z0-9]+", "", station_query.strip().lower())
    if query_token:
        acronym_matches = [
            candidate
            for candidate in matches
            if _station_initials(candidate.name) == query_token
        ]
        if acronym_matches:
            return acronym_matches[0], True

    return matches[0], True


def cmd_env() -> int:
    api_key = os.getenv("TFL_API_KEY", "").strip()
    if api_key:
        masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "set"
        print(f"TFL_API_KEY is set ({masked}).")
        return 0
    print("TFL_API_KEY is not set.")
    print("Run: export TFL_API_KEY=\"your_key_here\"")
    return 1


def cmd_now(
    station: str,
    window: str,
    mode: str,
    lines: Optional[List[str]],
    full_timetable: bool,
    direction: Optional[str],
    towards: Optional[str],
    debug_path: Optional[str],
) -> int:
    tzinfo = london_tz()
    now = datetime.now(tzinfo)
    try:
        window_delta = parse_window(window)
    except ValueError as exc:
        print(f"Invalid window: {exc}", file=sys.stderr)
        return 2
    window_end = now + window_delta

    try:
        client = TflClient.from_env()
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    debug_data: Optional[dict[str, Any]] = {} if debug_path else None
    if debug_data is not None:
        print(
            "Warning: debug output may include sensitive data; app_key will be redacted.",
            file=sys.stderr,
        )

    station_query = resolve_station_query(station)
    if station_query != station:
        print(
            f"Note: interpreting station '{station}' as '{station_query}'.",
            file=sys.stderr,
        )
    try:
        matches = search_stop_points(client, station_query, modes=[mode])
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not matches:
        print(f"No station matches for '{station}'.", file=sys.stderr)
        return 2
    if debug_data is not None:
        debug_data["matches"] = [match.__dict__ for match in matches]
    match, matched_by_fallback = choose_station_match(station_query, matches)
    if matched_by_fallback:
        print(
            f"Note: using station match '{match.name}' for query '{station_query}'.",
            file=sys.stderr,
        )

    stop_id = match.id
    station_name = match.name
    line_details: List[dict[str, str]] = []

    try:
        stop_point = get_stop_point(client, stop_id)
        station_name = stop_point.get("commonName") or station_name
        for line in stop_point.get("lines", []) or []:
            line_id = line.get("id")
            if line_id:
                line_name = line.get("name") or line_id
                line_details.append({"id": line_id, "name": line_name})
        if debug_data is not None:
            debug_data["stop_point"] = stop_point
    except TflApiError:
        pass

    try:
        arrivals = get_arrivals(client, stop_id)
        if debug_data is not None:
            debug_data["arrivals"] = arrivals
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not line_details:
        line_details = collect_line_details(arrivals)

    selected_lines, unknown_lines = resolve_line_filters(lines, line_details)
    if unknown_lines:
        available = format_available_lines(line_details)
        print(
            f"Unknown line(s): {', '.join(unknown_lines)}.",
            file=sys.stderr,
        )
        if available:
            print(f"Available lines: {', '.join(available)}", file=sys.stderr)
        return 2

    allow_line_timetables = should_fetch_line_timetables(
        selected_lines, line_details, full_timetable, bool(towards)
    )
    if not allow_line_timetables:
        print(
            f"Warning: {station_name} has {len(line_details)} lines; "
            "skipping per-line timetables. Use --line or --full-timetable.",
            file=sys.stderr,
        )
    elif (
        towards
        and not selected_lines
        and not full_timetable
        and len(line_details) > 1
    ):
        print(
            "Note: --towards enabled per-line timetables for this multi-line station. "
            "Use --line to limit requests.",
            file=sys.stderr,
        )

    normalized_direction = normalize_direction(direction)
    if direction and normalized_direction is None:
        print(
            "Direction must be inbound/outbound or a cardinal like northbound.",
            file=sys.stderr,
        )
        return 2
    if (
        normalized_direction
        and normalized_direction not in {"inbound", "outbound"}
        and selected_lines
        and len(selected_lines) > 1
    ):
        print(
            "Cardinal directions require a single line; "
            "use inbound/outbound or select one line.",
            file=sys.stderr,
        )
        return 2
    filtered_arrivals = filter_arrivals_by_line(arrivals, selected_lines)
    filtered_arrivals = filter_arrivals_by_direction(
        filtered_arrivals, normalized_direction
    )
    live_departures = arrivals_to_departures(
        filtered_arrivals, now, window_end, tzinfo
    )

    timetable_departures = []
    timetable_errors: List[str] = []
    arrivals_for_direction = (
        filtered_arrivals if selected_lines else (filtered_arrivals or arrivals)
    )
    timetable_direction = infer_timetable_direction(
        arrivals_for_direction, normalized_direction
    )
    if (
        normalized_direction
        and normalized_direction not in {"inbound", "outbound"}
        and timetable_direction is None
    ):
        print(
            f"Could not infer inbound/outbound for '{normalized_direction}'.",
            file=sys.stderr,
        )
        return 2
    if not selected_lines:
        try:
            timetable_data = get_stop_point_timetable(
                client, stop_id, timetable_direction
            )
            if debug_data is not None:
                debug_data["stop_point_timetable"] = timetable_data
            timetable_departures = timetable_to_departures(
                timetable_data, stop_id, now, window_end, tzinfo
            )
        except TflApiError as exc:
            timetable_errors.append(str(exc))

    if not timetable_departures and allow_line_timetables:
        line_ids = [item["id"] for item in line_details]
        if selected_lines:
            line_ids = [line_id for line_id in line_ids if line_id in selected_lines]
            if not line_ids:
                line_ids = sorted(selected_lines)
        timetable_directions = (
            [timetable_direction]
            if timetable_direction
            else ["inbound", "outbound"]
        )
        for line_id in line_ids:
            for direction_value in timetable_directions:
                try:
                    timetable_data = get_line_timetable(
                        client, line_id, stop_id, direction_value
                    )
                    if debug_data is not None:
                        key = f"line_timetable_{line_id}_{direction_value}"
                        debug_data[key] = timetable_data
                except TflApiError as exc:
                    timetable_errors.append(str(exc))
                    continue
                timetable_departures.extend(
                    timetable_to_departures(
                        timetable_data, stop_id, now, window_end, tzinfo
                    )
                )

    combined = merge_departures(live_departures, timetable_departures)
    combined_before_towards = len(combined)
    if towards:
        needles = build_towards_needles(towards)
        via_sensitive = is_via_direction_sensitive(towards)
        combined = [
            item
            for item in combined
            if departure_matches_towards(
                item, needles, normalized_direction, via_sensitive
            )
        ]
        if combined_before_towards and not combined:
            sample = sorted(
                {canonicalize_display_destination(item.destination) for item in live_departures}
            )
            if sample:
                preview = ", ".join(sample[:5])
                print(
                    f"Note: --towards '{towards}' filtered out all departures. "
                    f"Live destinations include: {preview}",
                    file=sys.stderr,
                )
    combined = order_departures(combined)
    if debug_data is not None:
        debug_data["timetable_errors"] = timetable_errors
        debug_data["combined_count"] = len(combined)
        redacted = redact_debug_data(debug_data, client.api_key, client.app_id)
        Path(debug_path).write_text(json.dumps(redacted, indent=2))

    direction_label = f", direction: {normalized_direction}" if normalized_direction else ""
    print(f"Expected departures at {station_name} (next {window}{direction_label}):")
    if not combined:
        print("No departures found in this window.")
        return 0
    for departure in combined:
        print(format_departure_display(departure, now))
    return 0


def cmd_list(
    station: str,
    mode: str,
    lines: Optional[List[str]],
    full_timetable: bool,
    direction: Optional[str],
) -> int:
    try:
        client = TflClient.from_env()
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    station_query = resolve_station_query(station)
    if station_query != station:
        print(
            f"Note: interpreting station '{station}' as '{station_query}'.",
            file=sys.stderr,
        )
    try:
        matches = search_stop_points(client, station_query, modes=[mode])
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not matches:
        print(f"No station matches for '{station}'.", file=sys.stderr)
        return 2
    match, matched_by_fallback = choose_station_match(station_query, matches)
    if matched_by_fallback:
        print(
            f"Note: using station match '{match.name}' for query '{station_query}'.",
            file=sys.stderr,
        )

    stop_id = match.id
    station_name = match.name
    line_details: List[dict[str, str]] = []
    try:
        stop_point = get_stop_point(client, stop_id)
        station_name = stop_point.get("commonName") or station_name
        for line in stop_point.get("lines", []) or []:
            line_id = line.get("id")
            if line_id:
                line_name = line.get("name") or line_id
                line_details.append({"id": line_id, "name": line_name})
    except TflApiError:
        pass

    try:
        arrivals = get_arrivals(client, stop_id)
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not line_details:
        line_details = collect_line_details(arrivals)

    selected_lines, unknown_lines = resolve_line_filters(lines, line_details)
    if unknown_lines:
        available = format_available_lines(line_details)
        print(
            f"Unknown line(s): {', '.join(unknown_lines)}.",
            file=sys.stderr,
        )
        if available:
            print(f"Available lines: {', '.join(available)}", file=sys.stderr)
        return 2

    allow_line_timetables = should_fetch_line_timetables(
        selected_lines, line_details, full_timetable, False
    )
    skipped_line_timetables = False
    if not allow_line_timetables:
        skipped_line_timetables = True
        print(
            f"Warning: {station_name} has {len(line_details)} lines; "
            "skipping per-line timetables. Use --line or --full-timetable.",
            file=sys.stderr,
        )

    normalized_direction = normalize_direction(direction)
    if direction and normalized_direction is None:
        print(
            "Direction must be inbound/outbound or a cardinal like northbound.",
            file=sys.stderr,
        )
        return 2
    if (
        normalized_direction
        and normalized_direction not in {"inbound", "outbound"}
        and selected_lines
        and len(selected_lines) > 1
    ):
        print(
            "Cardinal directions require a single line; "
            "use inbound/outbound or select one line.",
            file=sys.stderr,
        )
        return 2

    print(f"Available options for {station_name}:")
    arrivals_for_display = filter_arrivals_by_line(arrivals, selected_lines)
    _print_directions(arrivals_for_display)

    filtered_arrivals = filter_arrivals_by_direction(
        arrivals_for_display, normalized_direction
    )
    live_destinations = extract_live_destinations(filtered_arrivals)
    if live_destinations:
        print("Live destinations:")
        for dest in live_destinations:
            print(dest)

    arrivals_for_direction = (
        arrivals_for_display if selected_lines else (arrivals_for_display or arrivals)
    )
    timetable_direction = infer_timetable_direction(
        arrivals_for_direction, normalized_direction
    )
    if (
        normalized_direction
        and normalized_direction not in {"inbound", "outbound"}
        and timetable_direction is None
    ):
        print(
            f"Could not infer inbound/outbound for '{normalized_direction}'.",
            file=sys.stderr,
        )
        return 2
    timetable_directions = (
        [timetable_direction]
        if timetable_direction
        else ["inbound", "outbound"]
    )
    timetable_dest_set: set[str] = set()
    if allow_line_timetables:
        line_ids = [item["id"] for item in line_details]
        if selected_lines:
            line_ids = [line_id for line_id in line_ids if line_id in selected_lines]
            if not line_ids:
                line_ids = sorted(selected_lines)
        for line_id in line_ids:
            for direction_value in timetable_directions:
                try:
                    timetable_data = get_line_timetable(
                        client, line_id, stop_id, direction_value
                    )
                except TflApiError:
                    continue
                timetable_dest_set.update(timetable_destinations(timetable_data))
    if timetable_dest_set:
        print("Timetable destinations:")
        for dest in sorted(timetable_dest_set):
            print(compact_destination(dest))

    if not live_destinations and not timetable_dest_set:
        if skipped_line_timetables:
            print(
                "No live destinations right now. "
                "Timetable destinations skipped; use --line or --full-timetable."
            )
        else:
            print("No destinations available right now.")
    return 0


LINE_TIMETABLE_LINE_THRESHOLD = 2

STATION_QUERY_ALIASES = {
    "tcr": "Tottenham Court Road",
}

LINE_ALIASES = {
    "bakerloo": "bakerloo",
    "central": "central",
    "circle": "circle",
    "district": "district",
    "dlr": "dlr",
    "elizabeth": "elizabeth",
    "elizabethline": "elizabeth",
    "hammersmithandcity": "hammersmith-city",
    "hammersmithcity": "hammersmith-city",
    "hmc": "hammersmith-city",
    "jub": "jubilee",
    "jubilee": "jubilee",
    "met": "metropolitan",
    "metropolitan": "metropolitan",
    "northern": "northern",
    "overground": "london-overground",
    "picc": "piccadilly",
    "piccadilly": "piccadilly",
    "victoria": "victoria",
    "waterlooandcity": "waterloo-city",
    "waterloocity": "waterloo-city",
    "wac": "waterloo-city",
}


def normalize_line_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def normalize_station_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def resolve_station_query(value: str) -> str:
    token = normalize_station_token(value)
    return STATION_QUERY_ALIASES.get(token, value)


def should_fetch_line_timetables(
    selected_lines: Optional[set[str]],
    line_details: List[dict[str, str]],
    full_timetable: bool,
    allow_if_towards: bool,
) -> bool:
    if selected_lines:
        return True
    if full_timetable:
        return True
    if allow_if_towards:
        return True
    return len(line_details) < LINE_TIMETABLE_LINE_THRESHOLD


def guess_line_id(value: str) -> Optional[str]:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or None


def collect_line_details(arrivals: List[dict[str, Any]]) -> List[dict[str, str]]:
    seen: set[str] = set()
    details: List[dict[str, str]] = []
    for item in arrivals:
        line_id = item.get("lineId")
        if not line_id or line_id in seen:
            continue
        line_name = item.get("lineName") or line_id
        details.append({"id": line_id, "name": line_name})
        seen.add(line_id)
    return details


def format_available_lines(line_details: List[dict[str, str]]) -> List[str]:
    formatted: List[str] = []
    seen: set[str] = set()
    for line in line_details:
        line_id = line.get("id")
        if not line_id or line_id in seen:
            continue
        name = line.get("name") or line_id
        if normalize_line_token(name) != normalize_line_token(line_id):
            formatted.append(f"{name} ({line_id})")
        else:
            formatted.append(name)
        seen.add(line_id)
    return formatted


def resolve_line_filters(
    requested: Optional[List[str]],
    available_lines: List[dict[str, str]],
) -> tuple[Optional[set[str]], List[str]]:
    if not requested:
        return None, []
    tokens: List[str] = []
    for value in requested:
        for part in value.split(","):
            text = part.strip()
            if text:
                tokens.append(text)
    if not tokens:
        return None, []
    lookup: dict[str, str] = {}
    available_ids: set[str] = set()
    for line in available_lines:
        line_id = line.get("id")
        if not line_id:
            continue
        available_ids.add(line_id)
        lookup[normalize_line_token(line_id)] = line_id
        name = line.get("name")
        if name:
            lookup[normalize_line_token(name)] = line_id
    resolved: set[str] = set()
    unknown: List[str] = []
    for token in tokens:
        norm = normalize_line_token(token)
        if not norm:
            continue
        line_id = lookup.get(norm)
        if line_id is None:
            line_id = LINE_ALIASES.get(norm)
        if line_id is None and not available_ids:
            line_id = guess_line_id(token)
        if line_id is None:
            unknown.append(token)
            continue
        if available_ids and line_id not in available_ids:
            unknown.append(token)
            continue
        resolved.add(line_id)
    if not resolved and not unknown:
        return None, []
    return resolved, unknown


def normalize_direction(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip().lower()
    aliases = {
        "in": "inbound",
        "out": "outbound",
        "nb": "northbound",
        "sb": "southbound",
        "eb": "eastbound",
        "wb": "westbound",
        "north": "northbound",
        "south": "southbound",
        "east": "eastbound",
        "west": "westbound",
    }
    text = aliases.get(text, text)
    if text in {"inbound", "outbound", "northbound", "southbound", "eastbound", "westbound"}:
        return text
    return None


TOWARDS_ENV_VAR = "TUBE_TIMING_TOWARDS_ALIASES"

BASE_TOWARDS_ALIASES = {
    "battersea power station": {"battersea"},
    "charing cross": {"cx", "chx"},
    "saint": {"st"},
    "st": {"saint"},
}

VIA_DIRECTION_SENSITIVE = {"charing cross", "bank"}

TOWARDS_DISPLAY_OVERRIDES = {
    "battersea power station": "Battersea Power Station",
    "charing cross": "Charing Cross",
}


def build_towards_needles(value: str) -> set[str]:
    needles: set[str] = set()
    aliases = get_towards_aliases()
    base = normalize_name(re.sub(r"\bvia\b", "", value, flags=re.IGNORECASE))
    if base:
        needles.add(base)
    text = re.sub(r"\bpower station\b", "", value, flags=re.IGNORECASE)
    alt = normalize_name(text)
    if alt:
        needles.add(alt)
    canonical_map = build_alias_canonical_map(aliases)
    queue = list(needles)
    while queue:
        needle = queue.pop()
        canonical = canonical_map.get(needle)
        if canonical and canonical not in needles:
            needles.add(canonical)
            queue.append(canonical)
        alias_set = aliases.get(needle, set()) or aliases.get(canonical or "", set())
        for alias in alias_set:
            if alias not in needles:
                needles.add(alias)
                queue.append(alias)
        for key, alias_values in aliases.items():
            if needle in alias_values and key not in needles:
                needles.add(key)
                queue.append(key)
    return needles


def is_via_direction_sensitive(value: str) -> bool:
    normalized = normalize_name(value)
    if not normalized:
        return False
    aliases = get_towards_aliases()
    sensitive_terms: set[str] = set()
    for key in VIA_DIRECTION_SENSITIVE:
        key_norm = normalize_name(key)
        sensitive_terms.add(key_norm)
        for alias in aliases.get(key_norm, set()):
            sensitive_terms.add(alias)
    return any(term in normalized for term in sensitive_terms)


def departure_matches_towards(
    departure: Departure,
    needles: set[str],
    direction_filter: Optional[str],
    via_sensitive: bool,
) -> bool:
    if not needles:
        return True
    destination, via = split_destination_via(departure.destination)
    dest_norm = normalize_name(destination)
    if any(needle in dest_norm for needle in needles):
        return True
    if departure.stops:
        for stop_name in departure.stops:
            stop_norm = normalize_name(stop_name)
            if any(needle in stop_norm for needle in needles):
                return True
    if via:
        via_norm = normalize_name(via)
        if any(needle in via_norm for needle in needles):
            if direction_filter:
                return True
            if (
                via_sensitive
                and departure.source == "live"
                and departure.direction == "outbound"
            ):
                return False
            return True
    return False


def order_departures(departures: List[Departure]) -> List[Departure]:
    live = [item for item in departures if item.source == "live"]
    scheduled = [item for item in departures if item.source != "live"]
    if live:
        latest_by_key: dict[str, datetime] = {}
        live_times_by_key: dict[str, List[datetime]] = {}
        for item in live:
            key = dedupe_key_for_departure(item)
            latest_by_key[key] = max(latest_by_key.get(key, item.when), item.when)
            live_times_by_key.setdefault(key, []).append(item.when)
        filtered = []
        for item in scheduled:
            key = dedupe_key_for_departure(item)
            live_times = live_times_by_key.get(key, [])
            if any(abs((item.when - live_time).total_seconds()) <= 90 for live_time in live_times):
                continue
            latest = latest_by_key.get(key)
            if latest and item.when < latest:
                continue
            filtered.append(item)
        scheduled = filtered
    return sorted(live, key=lambda item: item.when) + sorted(
        scheduled, key=lambda item: item.when
    )


def dedupe_key_for_departure(departure: Departure) -> str:
    display_value = canonicalize_display_destination(departure.destination)
    return normalize_destination_key(display_value)


def format_departure_display(departure: Departure, now: datetime) -> str:
    display_destination = canonicalize_display_destination(departure.destination)
    if display_destination == departure.destination:
        return format_departure(departure, now)
    display_dep = Departure(
        when=departure.when,
        destination=display_destination,
        source=departure.source,
        line=departure.line,
        stops=departure.stops,
        direction=departure.direction,
    )
    return format_departure(display_dep, now)


def canonicalize_display_destination(value: str) -> str:
    destination, via = split_destination_via(value)
    aliases = get_towards_aliases()
    canonical_map = build_alias_canonical_map(aliases)
    display_overrides = get_towards_display_overrides()
    dest_norm = normalize_name(destination)
    dest_key = canonical_map.get(dest_norm, dest_norm)
    display_dest = display_overrides.get(dest_key, destination.strip())
    via_display = ""
    if via:
        via_norm = normalize_name(via)
        via_key = canonical_map.get(via_norm, via_norm)
        via_display = display_overrides.get(via_key, via.strip())
    if via_display:
        return f"{display_dest} via {via_display}"
    return display_dest


def normalize_destination_key(value: str) -> str:
    destination, via = split_destination_via(value)
    aliases = get_towards_aliases()
    canonical_map = build_alias_canonical_map(aliases)
    dest_norm = normalize_name(destination)
    dest_norm = normalize_name(
        re.sub(r"\bpower station\b", "", dest_norm, flags=re.IGNORECASE)
    )
    dest_norm = canonical_map.get(dest_norm, dest_norm)
    if via:
        via_norm = normalize_name(via)
        via_norm = canonical_map.get(via_norm, via_norm)
        return f"{dest_norm} via {via_norm}".strip()
    return dest_norm


def split_destination_via(value: str) -> tuple[str, str]:
    parts = re.split(r"\s+via\s+", value, flags=re.IGNORECASE, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return value.strip(), ""


def build_alias_canonical_map(aliases: dict[str, set[str]]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for key, alias_set in aliases.items():
        canonical[key] = key
        for alias in alias_set:
            canonical[alias] = key
    return canonical


def get_towards_aliases() -> dict[str, set[str]]:
    aliases = {key: set(values) for key, values in BASE_TOWARDS_ALIASES.items()}
    env_value = os.getenv(TOWARDS_ENV_VAR, "").strip()
    if not env_value:
        return aliases
    for entry in env_value.split(";"):
        if not entry.strip() or "=" not in entry:
            continue
        key_raw, alias_raw = entry.split("=", 1)
        key = normalize_name(key_raw)
        if not key:
            continue
        alias_set = {
            normalize_name(item)
            for item in alias_raw.split(",")
            if normalize_name(item)
        }
        if not alias_set:
            continue
        if key not in aliases:
            aliases[key] = set()
        aliases[key].update(alias_set)
    return aliases


def get_towards_display_overrides() -> dict[str, str]:
    return dict(TOWARDS_DISPLAY_OVERRIDES)


def filter_arrivals_by_line(
    arrivals: List[dict[str, Any]], line_ids: Optional[set[str]]
) -> List[dict[str, Any]]:
    if not line_ids:
        return arrivals
    allowed = set(line_ids)
    allowed_tokens = {normalize_line_token(line_id) for line_id in allowed}
    filtered = []
    for item in arrivals:
        line_id = item.get("lineId")
        if line_id in allowed:
            filtered.append(item)
            continue
        line_name = item.get("lineName") or ""
        if normalize_line_token(line_name) in allowed_tokens:
            filtered.append(item)
    return filtered


def filter_arrivals_by_direction(
    arrivals: List[dict[str, Any]], direction: Optional[str]
) -> List[dict[str, Any]]:
    if not direction:
        return arrivals
    if direction in {"inbound", "outbound"}:
        return [item for item in arrivals if item.get("direction") == direction]
    needle = direction.lower()
    filtered = []
    for item in arrivals:
        platform = (item.get("platformName") or "").lower()
        if needle in platform:
            filtered.append(item)
    return filtered


def infer_timetable_direction(
    arrivals: List[dict[str, Any]], direction: Optional[str]
) -> Optional[str]:
    if not direction:
        return None
    if direction in {"inbound", "outbound"}:
        return direction
    needle = direction.lower()
    counts: dict[str, int] = {}
    for item in arrivals:
        platform = (item.get("platformName") or "").lower()
        if needle not in platform:
            continue
        arrival_direction = item.get("direction")
        if arrival_direction:
            counts[arrival_direction] = counts.get(arrival_direction, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def extract_live_destinations(arrivals: List[dict[str, Any]]) -> List[str]:
    destinations: set[str] = set()
    for item in arrivals:
        destination = item.get("towards") or item.get("destinationName") or ""
        via = item.get("via")
        if via and via not in destination:
            destination = f"{destination} via {via}".strip()
        if destination:
            destinations.add(compact_destination(destination))
    return sorted(destinations)


def _print_directions(arrivals: List[dict[str, Any]]) -> None:
    direction_counts: dict[str, int] = {}
    cardinal_map: dict[str, dict[str, int]] = {}
    for item in arrivals:
        inbound_outbound = item.get("direction")
        if inbound_outbound:
            direction_counts[inbound_outbound] = direction_counts.get(inbound_outbound, 0) + 1
        platform = (item.get("platformName") or "").lower()
        for cardinal in ["northbound", "southbound", "eastbound", "westbound"]:
            if cardinal in platform:
                if cardinal not in cardinal_map:
                    cardinal_map[cardinal] = {}
                if inbound_outbound:
                    cardinal_map[cardinal][inbound_outbound] = (
                        cardinal_map[cardinal].get(inbound_outbound, 0) + 1
                    )
    if not direction_counts and not cardinal_map:
        print("Directions: no live arrivals to infer.")
        return
    print("Directions:")
    for cardinal, counts in sorted(cardinal_map.items()):
        if counts:
            preferred = max(counts, key=counts.get)
            print(f"{cardinal} ({preferred})")
        else:
            print(cardinal)
    for direction in sorted(direction_counts):
        if direction not in {"inbound", "outbound"}:
            continue
        print(direction)


def redact_debug_data(value: Any, api_key: str, app_id: Optional[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: redact_debug_data(val, api_key, app_id) for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact_debug_data(item, api_key, app_id) for item in value]
    if isinstance(value, str):
        redacted = value
        if api_key:
            redacted = redacted.replace(api_key, "REDACTED")
        if app_id:
            redacted = redacted.replace(app_id, "REDACTED")
        redacted = re.sub(r"(app_key=)([^&\s]+)", r"\1REDACTED", redacted)
        redacted = re.sub(r"(app_id=)([^&\s]+)", r"\1REDACTED", redacted)
        return redacted
    return value


if __name__ == "__main__":
    raise SystemExit(main())
