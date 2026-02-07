# tube-timing

A CLI to quickly see the next departures from your Tube station, combining TfL live arrivals with longer-range timetables.

Requirements: Python 3.9+

## Quickstart

- TfL API key required (API ID optional)
- Get an API key from the TfL API portal: https://api.tfl.gov.uk

Install with `pipx` (recommended, when published on PyPI):

```sh
brew install pipx
pipx ensurepath
pipx install tube-timing
```

If this repo is not published to PyPI yet, install from source:

```sh
# from a local clone
pipx install .

# or directly from GitHub
pipx install "git+https://github.com/<owner>/<repo>.git"
```

Then run:

```sh
tube-timing env
tube-timing now "Regent's Park" 10m
```

Alternative (without pipx):

```sh
export TFL_API_KEY=your_key_here
# export TFL_APP_ID=your_app_id_here

python3 -m pip install .

tube-timing now "Regent's Park" 10m
```

## Usage

Basic:

```sh
tube-timing now "Regent's Park" 30m
```

Filter examples:

```sh
tube-timing now "Regent's Park" 60m --direction southbound
tube-timing now "Regent's Park" 60m --towards "Charing Cross"
tube-timing now "Waterloo" 60m --line jubilee --line northern
```

Station matching:
- Uses the TfL StopPoint name search (case-insensitive, partial matches allowed).
- If multiple matches are returned, an exact normalised name wins.
- Common shortcuts are supported (for example, `TCR` -> `Tottenham Court Road`).
- If no exact/shortcut match is found, the first TfL result is used and a note is printed.

## Window format

- Use segments like `30m`, `1h`, `15m`, `1h30m`.
- The window is how far into the future (from now) to include departures.

## Filtering

### Filtering behaviour

- `--direction` accepts inbound/outbound or compass directions like northbound.
- Compass directions require a single `--line` so direction can be inferred for that line.
- `--towards` matches the final destination in live arrivals and intermediate stops in timetables when available.

### Towards aliases

- Common abbreviations are expanded in matching and output.
- Customise via `TUBE_TIMING_TOWARDS_ALIASES`:

```sh
export TUBE_TIMING_TOWARDS_ALIASES="charing cross=cx,chx;saint=st"
```

### Output format

- Every line ends with `LIVE` or `SCHEDULED`.

```
High Barnet via Charing Cross 19:12 (in 3m) LIVE
Battersea Power Station via Charing Cross 19:18 (in 9m) SCHEDULED
```

## Timetables & performance

Default behaviour for multi-line stations:
- Per-line timetable calls are skipped.
- Live arrivals still show.
- Station-level timetables (single API call) are used when available.

Overrides:
- `--line` scopes per-line timetables to those lines.
- `--full-timetable` forces per-line timetables.
- `--towards` enables per-line timetables.

Examples:

```sh
tube-timing now "Waterloo" 10m --line jubilee --line northern
tube-timing now "Waterloo" 10m --full-timetable
```

### Known limitations

- Live arrivals can be empty during service disruptions.
- Timetables may be missing or incomplete for some stations.

## Commands

- `tube-timing now <station> <window>`: show expected departures.
- `tube-timing list <station>`: list directions and destinations.
- `tube-timing env`: show whether `TFL_API_KEY` is set.

## Debugging

- `--debug` writes JSON payloads to `./tube-timing-debug.json` by default.
- Provide a path to write elsewhere: `--debug /path/to/file.json`.
- `app_key` and `app_id` are redacted, but treat output as sensitive.

## Development

Dev setup (editable install):

```sh
python3 -m pip install -e .
```

Optional (no install):

```sh
PYTHONPATH=src python3 -m tube_timing.cli now "Regent's Park" 30m
```

Quick sanity checks:

```sh
python3 -m py_compile src/tube_timing/*.py
```

## Publishing

- PyPI release checklist: `RELEASE.md`
- After publishing, users can install with:

```sh
pipx install tube-timing
```

## License

MIT. See `LICENSE`.
