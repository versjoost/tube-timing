# Release Guide

This guide publishes `tube-timing` to PyPI so users can install with:

```sh
pipx install tube-timing
```

## One-time setup

1. Create an account at [https://pypi.org](https://pypi.org).
2. Enable 2FA on your PyPI account.
3. Add this repo to GitHub and push your default branch.
4. In GitHub repo settings, create an environment named `pypi`.
5. In PyPI, add a trusted publisher for this GitHub repo/workflow:
   - Owner/repo: your repo
   - Workflow: `publish-pypi.yml`
   - Environment: `pypi`

## Local release checks

Use a virtual environment with dev tools:

```sh
python3 -m pip install -U pip
python3 -m pip install -e ".[dev]"
python3 -m unittest discover -s tests -v
python3 -m build
python3 -m twine check dist/*
```

Optional integration run (requires live API/network):

```sh
export TFL_API_KEY=...
export TUBE_TIMING_RUN_INTEGRATION=1
python3 -m unittest discover -s tests -v
```

## Publish steps

1. Bump version in `pyproject.toml` and `src/tube_timing/__init__.py`.
2. Commit changes and tag a release:

```sh
git add -A
git commit -m "Release v0.1.1"
git tag v0.1.1
git push origin main --tags
```

3. Create a GitHub Release for that tag (`v0.1.1`).
4. The publish workflow will build and upload to PyPI.

## Verify pipx install

After publish completes:

```sh
pipx install tube-timing
tube-timing --help
```
