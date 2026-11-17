"""Minimal client for OpenDota's public tournament data."""

import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


VALVE_PATCHES = "https://www.dota2.com/datafeed/patchnoteslist?language=english"


def valve_patches():
    request = Request(VALVE_PATCHES, headers={"Accept": "application/json", "User-Agent": "pro-replay-lab/1"})
    try:
        with urlopen(request, timeout=20) as response:
            body = json.load(response)
    except (OSError, ValueError, HTTPException) as exc:
        raise SourceError(f"Календарь патчей Valve недоступен ({exc})") from exc
    patches = body.get("patches") if isinstance(body, dict) and body.get("success") else None
    if not isinstance(patches, list) or not patches:
        raise SourceError("Valve вернул пустой календарь патчей.")
    entries = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise SourceError("Некорректная запись патча Valve.")
        version, stamp = patch.get("patch_number"), patch.get("patch_timestamp")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+[a-z]?", version) or type(stamp) is not int:
            raise SourceError("Не удалось прочитать версию и дату патча Valve.")
        entries.append((stamp, version))
    return entries


class SourceError(RuntimeError):
    """An endpoint failed or returned an unexpected response."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            return max(0.0, float(int(value)))
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return 2.0 ** attempt


class OpenDota:
    def __init__(self, base_url: str = "https://api.opendota.com/api",
                 api_key: str | None = None, *, match_metadata: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENDOTA_API_KEY")
        self.match_metadata = match_metadata
        self._next_request = 0.0

    def _get(self, path: str, params: dict | None = None):
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "pro-replay-lab/1"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(url, headers=headers)
        for attempt in range(3):
            time.sleep(max(0.0, self._next_request - time.monotonic()))
            self._next_request = time.monotonic() + 1.1
            try:
                with urlopen(request, timeout=20) as response:
                    return json.load(response)
            except HTTPError as exc:
                with exc:
                    delay = _retry_delay(exc.headers.get("Retry-After"), attempt)
                    if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2 or delay > 30:
                        hint = f"; retry in {delay:.0f}s" if exc.headers.get("Retry-After") else ""
                        raise SourceError(f"OpenDota {path}: HTTP {exc.code}{hint}", exc.code) from exc
            except (URLError, OSError, HTTPException) as exc:
                if attempt == 2:
                    raise SourceError(f"OpenDota {path}: network request failed ({exc})") from exc
                delay = _retry_delay(None, attempt)
            except ValueError as exc:
                raise SourceError(f"OpenDota {path}: invalid JSON response") from exc
            time.sleep(delay)

    def _list(self, path: str, params: dict | None = None) -> list[dict]:
        rows = self._get(path, params)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SourceError(f"OpenDota {path}: expected a list of objects")
        return rows

    def pro_players(self) -> list[dict]:
        return self._list("/proPlayers")

    def hero_directory(self) -> dict[int, dict[str, str]]:
        heroes = {}
        for row in self._list("/heroes"):
            hero_id = row.get("id")
            names = {field: row.get(field) for field in ("name", "localized_name")}
            valid_names = all(isinstance(name, str) and name.strip() for name in names.values())
            if type(hero_id) is not int or hero_id < 1 or not valid_names:
                raise SourceError("OpenDota /heroes: invalid hero record")
            heroes[hero_id] = names
        return heroes

    def heroes(self) -> dict[int, str]:
        return {hero_id: row["localized_name"] for hero_id, row in self.hero_directory().items()}

    def player_pro_ids(self, account_id: int, before: int | None = None) -> list[int]:
        """Return one descending page from the professional Data Explorer corpus."""
        if type(account_id) is not int or not 0 < account_id < 2**32:
            raise ValueError("account_id must be a positive 32-bit Steam account ID")
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError("before must be a positive match ID")
        cursor = "" if before is None else f" AND pm.match_id < {before}"
        sql = (
            "SELECT DISTINCT pm.match_id FROM player_matches pm "
            "JOIN matches m USING (match_id) "
            f"WHERE pm.account_id = {account_id} AND m.leagueid > 0{cursor} "
            "ORDER BY pm.match_id DESC LIMIT 100"
        )
        result = self._get("/explorer", {"sql": sql})
        if not isinstance(result, dict) or result.get("err") or result.get("error"):
            raise SourceError("OpenDota /explorer: query failed")
        rows = result.get("rows")
        if not isinstance(rows, list):
            raise SourceError("OpenDota /explorer: expected rows")
        ids = []
        for row in rows:
            value = row.get("match_id") if isinstance(row, dict) else None
            if isinstance(value, str) and value.isascii() and value.isdecimal():
                value = int(value)
            if type(value) is not int or value < 1:
                raise SourceError("OpenDota /explorer: invalid match ID")
            upper_bound = ids[-1] if ids else before
            if upper_bound is not None and value >= upper_bound:
                raise SourceError("OpenDota /explorer: match IDs do not follow descending cursor")
            ids.append(value)
        return ids

    def pro_matches(self, before: int | None = None) -> list[dict]:
        """The oldest ID from one page is the exclusive cursor for the next."""
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError("before must be a positive match ID")
        params = {"less_than_match_id": before} if before is not None else None
        return self._list("/proMatches", params)

    def match(self, match_id: int) -> dict:
        if type(match_id) is not int or match_id < 1:
            raise ValueError("match_id must be a positive integer")
        path = f"/matches/{match_id}"
        try:
            result = self._get(path, {"meta": 1} if self.match_metadata else None)
        except SourceError as exc:
            if not self.match_metadata or exc.status not in (400, 403, 502, 504):
                raise
            result = self._get(path)
        if not isinstance(result, dict) or result.get("match_id") != match_id:
            raise SourceError(f"OpenDota /matches/{match_id}: expected matching match_id")
        return result
