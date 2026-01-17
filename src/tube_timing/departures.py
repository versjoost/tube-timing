import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python <3.9
    ZoneInfo = None  # type: ignore


WINDOW_PATTERN = re.compile(r"(\d+)([smhd])")
VIA_STOP_IDS = {
    "via Bank": {"940GZZLUBNK"},
    "via Charing Cross": {"940GZZLUCHX"},
}


@dataclass(frozen=True)
class Departure:
    when: datetime
    destination: str
    source: str
    line: Optional[str] = None
    stops: Optional[FrozenSet[str]] = None
    direction: Optional[str] = None


def london_tz() -> timezone:
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo("Europe/London")
    except Exception:
        return timezone.utc


def parse_window(value: str) -> timedelta:
    text = value.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("Window value is empty")
    matches = WINDOW_PATTERN.findall(text)
    if not matches or "".join(f"{v}{u}" for v, u in matches) != text:
        raise ValueError("Window must look like 30m, 1h, or 1h30m")
    total = 0
    for amount, unit in matches:
        seconds = int(amount)
        if unit == "s":
            total += seconds
        elif unit == "m":
            total += seconds * 60
        elif unit == "h":
            total += seconds * 3600
        elif unit == "d":
            total += seconds * 86400
    return timedelta(seconds=total)


def parse_iso_datetime(value: str, tzinfo: timezone) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tzinfo)
    return parsed.astimezone(tzinfo)


def _combine_hour_minute(
    hour: int, minute: int, today: date, tzinfo: timezone
) -> datetime:
    day_offset = 0
    while hour >= 24:
        hour -= 24
        day_offset += 1
    return datetime.combine(
        today + timedelta(days=day_offset), time(hour, minute), tzinfo=tzinfo
    )


def parse_time_of_day(value: str, today: date, tzinfo: timezone) -> Optional[datetime]:
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{3,4}", text):
        hours = int(text[:-2])
        minutes = int(text[-2:])
        return _combine_hour_minute(hours, minutes, today, tzinfo)
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            hours = int(parts[0])
            minutes = int(parts[1])
            return _combine_hour_minute(hours, minutes, today, tzinfo)
    return None


def parse_time_value(value: Any, today: date, tzinfo: timezone) -> Optional[datetime]:
    if isinstance(value, dict):
        hour = value.get("hour")
        minute = value.get("minute")
        if hour is not None and minute is not None:
            return _combine_hour_minute(int(hour), int(minute), today, tzinfo)
    if isinstance(value, str):
        return parse_time_of_day(value, today, tzinfo)
    return None


def normalize_name(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", value.lower())
    text = text.replace(" underground station ", " ")
    text = text.replace(" station ", " ")
    return " ".join(text.split())


def compact_destination(value: str) -> str:
    return " ".join(value.replace(" Underground Station", "").split())


def _extract_stop_map(timetable: Dict[str, Any]) -> Dict[str, str]:
    stops = timetable.get("stops")
    if not isinstance(stops, list):
        return {}
    stop_map: Dict[str, str] = {}
    for item in stops:
        if not isinstance(item, dict):
            continue
        stop_id = item.get("id")
        name = item.get("name")
        if stop_id and name:
            stop_map[str(stop_id)] = str(name)
    return stop_map


def timetable_destinations(timetable: Any) -> List[str]:
    destinations: set[str] = set()
    _collect_timetable_destinations(timetable, destinations, {})
    return sorted(destinations)


def arrivals_to_departures(
    arrivals: Iterable[Dict[str, Any]],
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
) -> List[Departure]:
    departures: List[Departure] = []
    for item in arrivals:
        expected = item.get("expectedArrival") or item.get("expectedDeparture")
        if expected:
            when = parse_iso_datetime(str(expected), tzinfo)
        else:
            time_to_station = item.get("timeToStation")
            when = None
            if isinstance(time_to_station, (int, float)):
                when = now + timedelta(seconds=float(time_to_station))
        if when is None:
            continue
        if when < now or when > window_end:
            continue
        destination = item.get("towards") or item.get("destinationName") or ""
        via = item.get("via")
        if via and via not in destination:
            destination = f"{destination} via {via}".strip()
        if not destination:
            destination = item.get("lineName") or item.get("lineId") or "Unknown"
        departures.append(
            Departure(
                when=when,
                destination=destination,
                source="live",
                line=item.get("lineName") or item.get("lineId"),
                direction=item.get("direction"),
            )
        )
    return sorted(departures, key=lambda dep: dep.when)


def timetable_to_departures(
    timetable: Any,
    stop_id: str,
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
) -> List[Departure]:
    departures: List[Departure] = []
    if isinstance(timetable, list):
        for item in timetable:
            departures.extend(
                timetable_to_departures(item, stop_id, now, window_end, tzinfo)
            )
        return departures
    if not isinstance(timetable, dict):
        return departures
    stop_map = _extract_stop_map(timetable)

    if "departures" in timetable:
        departures.extend(
            _parse_departure_list(
                timetable.get("departures", []), now, window_end, tzinfo
            )
        )

    if "timetables" in timetable and isinstance(timetable["timetables"], list):
        for item in timetable["timetables"]:
            departures.extend(
                timetable_to_departures(item, stop_id, now, window_end, tzinfo)
            )

    if "timetable" in timetable:
        departures.extend(
            _parse_timetable_container(
                timetable["timetable"], stop_id, now, window_end, tzinfo, stop_map
            )
        )
    elif "routes" in timetable:
        departures.extend(
            _parse_timetable_container(timetable, stop_id, now, window_end, tzinfo, stop_map)
        )

    return _dedupe_departures(sorted(departures, key=lambda dep: dep.when))


def _collect_timetable_destinations(
    timetable: Any, destinations: set[str], stop_map: Dict[str, str]
) -> None:
    if isinstance(timetable, list):
        for item in timetable:
            _collect_timetable_destinations(item, destinations, stop_map)
        return
    if not isinstance(timetable, dict):
        return
    local_stop_map = dict(stop_map)
    local_stop_map.update(_extract_stop_map(timetable))
    if "timetables" in timetable and isinstance(timetable["timetables"], list):
        for item in timetable["timetables"]:
            _collect_timetable_destinations(item, destinations, local_stop_map)
    if "timetable" in timetable:
        _collect_timetable_destinations(timetable["timetable"], destinations, local_stop_map)
    routes = timetable.get("routes")
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            interval_destinations = _build_interval_destinations(route, local_stop_map)
            destinations.update(interval_destinations.values())


def _parse_departure_list(
    departures: Iterable[Dict[str, Any]],
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
) -> List[Departure]:
    items: List[Departure] = []
    for item in departures:
        if not isinstance(item, dict):
            continue
        time_value = (
            item.get("departureTime")
            or item.get("scheduledTime")
            or item.get("time")
        )
        when = None
        if isinstance(time_value, str):
            when = parse_iso_datetime(time_value, tzinfo)
            if when is None:
                when = parse_time_of_day(time_value, now.date(), tzinfo)
        elif isinstance(time_value, dict):
            when = parse_time_value(time_value, now.date(), tzinfo)
        elif isinstance(time_value, (int, float)):
            when = now + timedelta(seconds=float(time_value))
        if when is None:
            continue
        if when < now or when > window_end:
            continue
        destination = item.get("destination") or item.get("destinationName") or "Unknown"
        items.append(Departure(when=when, destination=destination, source="timetable"))
    return items


def _parse_timetable_container(
    container: Any,
    stop_id: str,
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
    stop_map: Optional[Dict[str, str]] = None,
) -> List[Departure]:
    if isinstance(container, list):
        items: List[Departure] = []
        for item in container:
            items.extend(
                _parse_timetable_container(
                    item, stop_id, now, window_end, tzinfo, stop_map
                )
            )
        return items
    if not isinstance(container, dict):
        return []
    local_stop_map = dict(stop_map or {})
    local_stop_map.update(_extract_stop_map(container))
    routes = container.get("routes")
    if isinstance(routes, list):
        items: List[Departure] = []
        for route in routes:
            items.extend(
                _parse_route(route, stop_id, now, window_end, tzinfo, local_stop_map)
            )
        return items
    return []


def _parse_route(
    route: Dict[str, Any],
    stop_id: str,
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
    stop_map: Optional[Dict[str, str]] = None,
) -> List[Departure]:
    destination = (
        route.get("destination")
        or route.get("destinationName")
        or route.get("name")
        or route.get("lineString")
        or route.get("direction")
        or "Unknown"
    )
    interval_destinations = _build_interval_destinations(route, stop_map or {})
    interval_stops = _build_interval_stops(route, stop_map or {})
    route_stops: Optional[set[str]] = None
    if len(interval_stops) == 1:
        route_stops = next(iter(interval_stops.values()))
    default_destination = destination
    if interval_destinations:
        default_destination = next(iter(interval_destinations.values()))
    items: List[Departure] = []
    schedules = route.get("schedules", [])
    matching = [
        schedule
        for schedule in schedules
        if isinstance(schedule, dict)
        and _schedule_matches_day(schedule.get("name"), now)
    ]
    for schedule in (matching or schedules):
        known = _parse_known_journeys(
            schedule,
            interval_destinations,
            interval_stops,
            default_destination,
            now,
            window_end,
            tzinfo,
        )
        if known:
            items.extend(known)
            continue
        periods = schedule.get("periods", []) if isinstance(schedule, dict) else []
        for period in periods:
            items.extend(
                _parse_schedule_period(
                    period,
                    default_destination,
                    0,
                    now,
                    window_end,
                    tzinfo,
                    route_stops,
                )
            )
    return items


def _schedule_matches_day(name: Any, now: datetime) -> bool:
    if not isinstance(name, str):
        return True
    text = name.lower()
    day = now.strftime("%A").lower()
    if day in text:
        return True
    if "weekday" in text and day in {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
    }:
        return True
    if "monday - thursday" in text and day in {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
    }:
        return True
    if "friday" in text and day == "friday":
        return True
    if "saturday" in text and day == "saturday":
        return True
    if "sunday" in text and day == "sunday":
        return True
    return False


def _build_interval_destinations(
    route: Dict[str, Any], stop_map: Dict[str, str]
) -> Dict[str, str]:
    destinations: Dict[str, str] = {}
    intervals = route.get("stationIntervals")
    if not isinstance(intervals, list):
        return destinations
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        interval_id = interval.get("id")
        if interval_id is None:
            continue
        stops = interval.get("intervals", [])
        if not isinstance(stops, list) or not stops:
            continue
        stop_ids = [item.get("stopId") for item in stops if item.get("stopId")]
        if not stop_ids:
            continue
        stop_id = stop_ids[-1]
        destination = stop_map.get(str(stop_id), str(stop_id))
        via = _detect_via(stop_ids)
        if via:
            destination = f"{destination} {via}"
        destinations[str(interval_id)] = destination
    return destinations


def _build_interval_stops(
    route: Dict[str, Any], stop_map: Dict[str, str]
) -> Dict[str, set[str]]:
    interval_stops: Dict[str, set[str]] = {}
    intervals = route.get("stationIntervals")
    if not isinstance(intervals, list):
        return interval_stops
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        interval_id = interval.get("id")
        if interval_id is None:
            continue
        stops = interval.get("intervals", [])
        if not isinstance(stops, list) or not stops:
            continue
        stop_ids = [item.get("stopId") for item in stops if item.get("stopId")]
        if not stop_ids:
            continue
        names = {
            stop_map.get(str(stop_id), str(stop_id)) for stop_id in stop_ids
        }
        interval_stops[str(interval_id)] = names
    return interval_stops


def _detect_via(stop_ids: List[str]) -> Optional[str]:
    for label, ids in VIA_STOP_IDS.items():
        for stop_id in stop_ids:
            if stop_id in ids:
                return label
    return None


def _parse_known_journeys(
    schedule: Any,
    interval_destinations: Dict[str, str],
    interval_stops: Dict[str, set[str]],
    default_destination: str,
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
) -> List[Departure]:
    if not isinstance(schedule, dict):
        return []
    journeys = schedule.get("knownJourneys")
    if not isinstance(journeys, list):
        return []
    items: List[Departure] = []
    today = now.date()
    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        when = parse_time_value(journey, today, tzinfo)
        if when is None:
            continue
        if when < now or when > window_end:
            continue
        interval_id = journey.get("intervalId")
        destination = default_destination
        stops: Optional[FrozenSet[str]] = None
        if interval_id is not None:
            destination = interval_destinations.get(str(interval_id), destination)
            interval_stop_set = interval_stops.get(str(interval_id))
            if interval_stop_set:
                stops = frozenset(interval_stop_set)
        items.append(
            Departure(
                when=when,
                destination=destination,
                source="timetable",
                stops=stops,
            )
        )
    return items


def _parse_schedule_period(
    period: Dict[str, Any],
    destination: str,
    offset_minutes: int,
    now: datetime,
    window_end: datetime,
    tzinfo: timezone,
    stops: Optional[set[str]] = None,
) -> List[Departure]:
    if not isinstance(period, dict):
        return []
    times = period.get("times")
    if isinstance(times, list):
        items: List[Departure] = []
        for time_value in times:
            when = None
            if isinstance(time_value, (str, dict)):
                when = parse_time_value(time_value, now.date(), tzinfo)
            if when is None:
                continue
            when = when + timedelta(minutes=offset_minutes)
            if now <= when <= window_end:
                items.append(
                    Departure(
                        when=when,
                        destination=destination,
                        source="timetable",
                        stops=frozenset(stops) if stops else None,
                    )
                )
        return items

    start_value = period.get("startTime") or period.get("fromTime")
    end_value = period.get("endTime") or period.get("toTime")
    frequency = period.get("frequency")
    if not (start_value and end_value and frequency):
        return []
    start_dt = parse_time_value(start_value, now.date(), tzinfo)
    end_dt = parse_time_value(end_value, now.date(), tzinfo)
    if start_dt is None or end_dt is None:
        return []
    if end_dt < start_dt:
        end_dt = end_dt + timedelta(days=1)
    frequency_minutes = float(frequency)
    if frequency_minutes <= 0:
        return []
    offset_delta = timedelta(minutes=offset_minutes)
    current = start_dt
    if now > current:
        delta_minutes = (now - current).total_seconds() / 60.0
        steps = math.floor(delta_minutes / frequency_minutes)
        current = current + timedelta(minutes=steps * frequency_minutes)
        if current < now:
            current = current + timedelta(minutes=frequency_minutes)
    items: List[Departure] = []
    while current <= end_dt and current <= window_end:
        when = current + offset_delta
        if now <= when <= window_end:
            items.append(
                Departure(
                    when=when,
                    destination=destination,
                    source="timetable",
                    stops=frozenset(stops) if stops else None,
                )
            )
        current = current + timedelta(minutes=frequency_minutes)
    return items


def merge_departures(
    live: List[Departure], timetable: List[Departure]
) -> List[Departure]:
    merged = list(live)
    for scheduled in timetable:
        if _is_duplicate(scheduled, live):
            continue
        merged.append(scheduled)
    return sorted(merged, key=lambda dep: dep.when)


def _dedupe_departures(departures: List[Departure]) -> List[Departure]:
    seen: set[tuple[datetime, str]] = set()
    unique: List[Departure] = []
    for item in departures:
        key = (item.when, normalize_name(item.destination))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _is_duplicate(candidate: Departure, live: List[Departure]) -> bool:
    for item in live:
        if abs((item.when - candidate.when).total_seconds()) <= 90:
            if _similar_destination(item.destination, candidate.destination):
                return True
    return False


def _similar_destination(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_norm = normalize_name(left)
    right_norm = normalize_name(right)
    return left_norm in right_norm or right_norm in left_norm


def format_departure(departure: Departure, now: datetime) -> str:
    destination = compact_destination(departure.destination)
    when_label = departure.when.strftime("%H:%M")
    seconds = (departure.when - now).total_seconds()
    if seconds <= 60:
        relative = "due"
    else:
        minutes = int(math.ceil(seconds / 60.0))
        relative = f"in {minutes}m"
    if departure.source == "live":
        return f"{destination} {when_label} ({relative}) LIVE"
    return f"{destination} {when_label} ({relative}) SCHEDULED"
