import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests


class TflApiError(RuntimeError):
    pass


@dataclass
class StopPointMatch:
    id: str
    name: str
    modes: List[str]


class TflClient:
    def __init__(self, api_key: str, app_id: Optional[str] = None) -> None:
        if not api_key:
            raise TflApiError("TFL_API_KEY is not set. Run `tube-timing env` for help.")
        self.api_key = api_key
        self.app_id = app_id
        self.base_url = "https://api.tfl.gov.uk"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "tube-timing/0.1"})

    @classmethod
    def from_env(cls) -> "TflClient":
        api_key = os.getenv("TFL_API_KEY", "").strip()
        app_id = os.getenv("TFL_APP_ID", "").strip() or None
        return cls(api_key=api_key, app_id=app_id)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        merged = dict(params or {})
        merged.setdefault("app_key", self.api_key)
        if self.app_id:
            merged.setdefault("app_id", self.app_id)
        response = self.session.get(url, params=merged, timeout=15)
        if response.status_code >= 400:
            message = response.text.strip()
            raise TflApiError(
                f"TFL API error {response.status_code} for {path}: {message}"
            )
        return response.json()


def search_stop_points(
    client: TflClient, query: str, modes: Optional[Iterable[str]] = None
) -> List[StopPointMatch]:
    params: Dict[str, Any] = {}
    if modes:
        params["modes"] = ",".join(modes)
    encoded = quote(query)
    data = client.get(f"/StopPoint/Search/{encoded}", params=params)
    matches = []
    for item in data.get("matches", []) or []:
        matches.append(
            StopPointMatch(
                id=item.get("id", ""),
                name=item.get("name", ""),
                modes=item.get("modes", []) or [],
            )
        )
    return matches


def get_stop_point(client: TflClient, stop_id: str) -> Dict[str, Any]:
    return client.get(f"/StopPoint/{quote(stop_id)}")


def get_arrivals(client: TflClient, stop_id: str) -> List[Dict[str, Any]]:
    return client.get(f"/StopPoint/{quote(stop_id)}/Arrivals")


def get_stop_point_timetable(
    client: TflClient, stop_id: str, direction: Optional[str] = None
) -> Any:
    params: Dict[str, Any] = {}
    if direction:
        params["direction"] = direction
    return client.get(f"/StopPoint/{quote(stop_id)}/Timetable", params=params)


def get_line_timetable(
    client: TflClient, line_id: str, stop_id: str, direction: Optional[str] = None
) -> Any:
    params: Dict[str, Any] = {}
    if direction:
        params["direction"] = direction
    return client.get(f"/Line/{quote(line_id)}/Timetable/{quote(stop_id)}", params=params)
