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
    format_departure,
    london_tz,
    merge_departures,
    normalize_name,
    parse_window,
    timetable_destinations,
    timetable_to_departures,
)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="tube-timing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    now_parser = subparsers.add_parser("now", help="Show expected departures")
    now_parser.add_argument("station", help="Station name, e.g. Totteridge & Whetstone")
    now_parser.add_argument("window", help="Time window, e.g. 30m or 1h30m")
    now_parser.add_argument("--mode", default="tube", help="TFL mode filter")
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
        "list", help="List available directions and destinations"
    )
    list_parser.add_argument("station", help="Station name, e.g. Totteridge & Whetstone")
    list_parser.add_argument("--mode", default="tube", help="TFL mode filter")
    list_parser.add_argument(
        "--direction",
        help="Filter destinations by direction (inbound/outbound or northbound)",
    )

    subparsers.add_parser("env", help="Check API environment variables")

    args = parser.parse_args(argv)

    if args.command == "env":
        return cmd_env()
    if args.command == "now":
        return cmd_now(
            args.station,
            args.window,
            args.mode,
            args.direction,
            args.towards,
            args.debug,
        )
    if args.command == "list":
        return cmd_list(args.station, args.mode, args.direction)

    parser.print_help()
    return 1


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

    matches = search_stop_points(client, station, modes=[mode])
    if not matches:
        print(f"No station matches for '{station}'.", file=sys.stderr)
        return 2
    if debug_data is not None:
        debug_data["matches"] = [match.__dict__ for match in matches]

    station_norm = normalize_name(station)
    match = None
    for candidate in matches:
        if normalize_name(candidate.name) == station_norm:
            match = candidate
            break
    if match is None:
        match = matches[0]

    stop_id = match.id
    station_name = match.name
    line_ids: List[str] = []

    try:
        stop_point = get_stop_point(client, stop_id)
        station_name = stop_point.get("commonName") or station_name
        for line in stop_point.get("lines", []) or []:
            line_id = line.get("id")
            if line_id:
                line_ids.append(line_id)
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
    normalized_direction = normalize_direction(direction)
    if direction and normalized_direction is None:
        print(
            "Direction must be inbound/outbound or a cardinal like northbound.",
            file=sys.stderr,
        )
        return 2
    filtered_arrivals = filter_arrivals_by_direction(arrivals, normalized_direction)
    live_departures = arrivals_to_departures(
        filtered_arrivals, now, window_end, tzinfo
    )

    timetable_departures = []
    timetable_errors: List[str] = []
    timetable_direction = infer_timetable_direction(arrivals, normalized_direction)
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
    try:
        timetable_data = get_stop_point_timetable(client, stop_id, timetable_direction)
        if debug_data is not None:
            debug_data["stop_point_timetable"] = timetable_data
        timetable_departures = timetable_to_departures(
            timetable_data, stop_id, now, window_end, tzinfo
        )
    except TflApiError as exc:
        timetable_errors.append(str(exc))

    if not timetable_departures:
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
    if towards:
        needle = normalize_name(towards)
        combined = [
            item for item in combined if needle in normalize_name(item.destination)
        ]
    if debug_data is not None:
        debug_data["timetable_errors"] = timetable_errors
        debug_data["combined_count"] = len(combined)
        redacted = redact_debug_data(debug_data, client.api_key)
        Path(debug_path).write_text(json.dumps(redacted, indent=2))

    direction_label = f", direction: {normalized_direction}" if normalized_direction else ""
    print(f"Expected departures at {station_name} (next {window}{direction_label}):")
    if not combined:
        print("No departures found in this window.")
        return 0
    for departure in combined:
        print(format_departure(departure, now))
    return 0


def cmd_list(station: str, mode: str, direction: Optional[str]) -> int:
    try:
        client = TflClient.from_env()
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    matches = search_stop_points(client, station, modes=[mode])
    if not matches:
        print(f"No station matches for '{station}'.", file=sys.stderr)
        return 2
    station_norm = normalize_name(station)
    match = None
    for candidate in matches:
        if normalize_name(candidate.name) == station_norm:
            match = candidate
            break
    if match is None:
        match = matches[0]

    stop_id = match.id
    station_name = match.name
    line_ids: List[str] = []
    try:
        stop_point = get_stop_point(client, stop_id)
        station_name = stop_point.get("commonName") or station_name
        for line in stop_point.get("lines", []) or []:
            line_id = line.get("id")
            if line_id:
                line_ids.append(line_id)
    except TflApiError:
        pass

    try:
        arrivals = get_arrivals(client, stop_id)
    except TflApiError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    normalized_direction = normalize_direction(direction)
    if direction and normalized_direction is None:
        print(
            "Direction must be inbound/outbound or a cardinal like northbound.",
            file=sys.stderr,
        )
        return 2

    print(f"Available options for {station_name}:")
    _print_directions(arrivals)

    filtered_arrivals = filter_arrivals_by_direction(arrivals, normalized_direction)
    live_destinations = extract_live_destinations(filtered_arrivals)
    if live_destinations:
        print("Live destinations:")
        for dest in live_destinations:
            print(dest)

    timetable_direction = infer_timetable_direction(arrivals, normalized_direction)
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
        print("No destinations available right now.")
    return 0


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


def redact_debug_data(value: Any, api_key: str) -> Any:
    if isinstance(value, dict):
        return {key: redact_debug_data(val, api_key) for key, val in value.items()}
    if isinstance(value, list):
        return [redact_debug_data(item, api_key) for item in value]
    if isinstance(value, str):
        redacted = value
        if api_key:
            redacted = redacted.replace(api_key, "REDACTED")
        redacted = re.sub(r"(app_key=)([^&\s]+)", r"\1REDACTED", redacted)
        return redacted
    return value


if __name__ == "__main__":
    raise SystemExit(main())
