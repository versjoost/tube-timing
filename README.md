# tube-timing

A small CLI for expected Tube departures using the TFL Unified API. The API provides
live arrivals (typically up to ~30 minutes ahead) and longer-range timetables; this
CLI combines both into a single view.

## Setup

Set the API key in your shell:

```sh
export TFL_API_KEY="your_key_here"
```

Optional: if you have an app id, you can also set `TFL_APP_ID`.

## Install

Recommended (virtualenv):

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip setuptools
python -m pip install -e .
```

## Usage

Basic:

```sh
tube-timing now "Totteridge & Whetstone" 30m
```

Filters:

```sh
tube-timing now "Totteridge & Whetstone" 60m --direction southbound
tube-timing now "Totteridge & Whetstone" 60m --towards "Charing Cross"
tube-timing now "Waterloo" 60m --line jubilee --line northern
```

When using a cardinal direction (northbound/southbound/etc), select a single line.
The `--towards` filter matches both final destinations and intermediate stops when
available from timetables. Common abbreviations are expanded (for example, "Charing
Cross" also matches "CX"). To customize, set
`TUBE_TIMING_TOWARDS_ALIASES="charing cross=cx,chx;saint=st"`.

Output marks LIVE vs SCHEDULED in a consistent format:

```
High Barnet via CX 19:12 (in 3m) LIVE
Battersea Power Station via Charing Cross 19:18 (in 9m) SCHEDULED
```

Live entries may use short forms; the CLI expands common ones (for example, "CX" ->
"Charing Cross") so output stays consistent.

Multi-line stations:

By default, per-line timetable calls are skipped for stations with more than one line
to keep response times down. Live arrivals still show, and the station-level timetable
(single API call) is used when available. Use `--line` to scope the timetables or
`--full-timetable` to force them. Using `--towards` also enables per-line timetables.

```sh
tube-timing now "Waterloo" 10m --line jubilee --line northern
tube-timing now "Waterloo" 10m --full-timetable
```

Explore options:

```sh
tube-timing list "Totteridge & Whetstone"
```

Debug payloads:

```sh
tube-timing now "Totteridge & Whetstone" 60m --debug
```

The debug file redacts `app_key` and `app_id`, but treat it as sensitive data.

## Commands

- `tube-timing now <station> <window>`
- `tube-timing list <station>` (shows directions and destinations)
- `tube-timing env` (shows whether the API key is set)

## Window format

Accepts one or more segments like `30m`, `1h`, `15m`, `1h30m`.

## Development

Run without installing:

```sh
PYTHONPATH=src python3 -m tube_timing.cli now "Totteridge & Whetstone" 30m
```

If you use the PYTHONPATH method, install dependencies first:

```sh
python3 -m pip install -e .
```

Quick sanity checks:

```sh
python3 -m py_compile src/tube_timing/*.py
```

## License

MIT. See `LICENSE`.
