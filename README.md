# tube-timing

A small CLI for expected Tube departures using the TFL Unified API.

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
```

Explore options:

```sh
tube-timing list "Totteridge & Whetstone"
```

Debug payloads:

```sh
tube-timing now "Totteridge & Whetstone" 60m --debug
```

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

Quick sanity checks:

```sh
python3 -m py_compile src/tube_timing/*.py
```

## License

MIT. See `LICENSE`.
