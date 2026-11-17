"""Persistent cache for player lookups, parsed matches and local experiments."""

import gzip
import json
import sqlite3
from bisect import bisect_right
from pathlib import Path

from .analysis import extract_match, select_player
from .build_review import extract_build
from .events import event_data
from .hero_review import extract_hero_samples
from .impact import match_detail
from .source import SourceError


REFERENCE_FILE = Path(__file__).resolve().parent.parent / "examples" / "hero-reference.json.gz"


def hero_reference(row):
    return {k: row.get(k) for k in ("match_id", "start_time", "duration", "patch", "patch_version",
                                    "league_id", "lobby_type", "game_mode", "hero_samples")}


class Archive:
    def __init__(self, filename):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(filename, timeout=30)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                player INTEGER NOT NULL, match_id INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (player, match_id)
            );
            CREATE TABLE IF NOT EXISTS names (
                player INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE
            );
            CREATE TABLE IF NOT EXISTS heroes (
                id INTEGER PRIMARY KEY, unit TEXT NOT NULL, title TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS valve_patches (
                released INTEGER NOT NULL, version TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS match_reviews (
                match_id INTEGER NOT NULL, slot INTEGER NOT NULL, player INTEGER, body TEXT NOT NULL,
                PRIMARY KEY (match_id, slot)
            );
            CREATE TABLE IF NOT EXISTS hero_reference (
                match_id INTEGER PRIMARY KEY, body TEXT NOT NULL
            );
        """)

    def match_player(self, query, api=None):
        if query.isascii() and query.isdecimal():
            value = int(query)
            if not 0 < value < 2**32:
                raise ValueError("Укажи 32-битный account ID игрока.")
            saved = self.db.execute("SELECT name FROM names WHERE player=?", (value,)).fetchone()
            return value, saved[0] if saved else query

        def candidates():
            return self.db.execute("SELECT player, name FROM names WHERE name=? COLLATE NOCASE", (query,)).fetchall()

        matches = candidates()
        if not matches and api is not None:
            with self.db:
                for entry in api.pro_players():
                    name, account = entry.get("name"), entry.get("account_id")
                    if name and isinstance(account, int):
                        self.db.execute("INSERT OR REPLACE INTO names VALUES (?,?)", (account, name))
            matches = candidates()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("Несколько игроков с этим ником. Укажи ID: " + ", ".join(str(row[0]) for row in matches))
        raise ValueError("Ник не найден. Укажи account ID из профиля OpenDota.")

    def hero_names(self, api):
        def read():
            return {i: {"name": unit, "localized_name": name}
                    for i, unit, name in self.db.execute("SELECT id,unit,title FROM heroes")}
        saved = read()
        if saved:
            return saved
        directory = api.hero_directory()
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO heroes VALUES (?,?,?)",
                                ((i, h["name"], h["localized_name"]) for i, h in directory.items()))
        return directory

    def save(self, row):
        with self.db:
            body = json.dumps(row, ensure_ascii=False)
            if row.get("league_id") and row.get("account_id"):
                self.db.execute("INSERT OR REPLACE INTO games VALUES (?,?,?)",
                                (row["account_id"], row["match_id"], body))
            elif row.get("player_slot") is not None:
                self.db.execute("INSERT OR REPLACE INTO match_reviews VALUES (?,?,?,?)",
                                (row["match_id"], row["player_slot"], row.get("account_id"), body))
            elif row.get("account_id"):  # Legacy player archives without league or slot metadata.
                self.db.execute("INSERT OR REPLACE INTO games VALUES (?,?,?)",
                                (row["account_id"], row["match_id"], body))
            if row.get("hero_samples"):
                self.db.execute("INSERT OR REPLACE INTO hero_reference VALUES (?,?)",
                                (row["match_id"], json.dumps(hero_reference(row))))

    def rows(self, account):
        cursor = self.db.execute("SELECT body FROM games WHERE player=? ORDER BY match_id DESC", (account,))
        return self.with_patch_versions([json.loads(body) for (body,) in cursor])

    def personal_history(self, row):
        account = row.get("account_id")
        if not account:
            return []
        if row.get("league_id"):
            return self.rows(account)
        records = self.db.execute("SELECT body FROM match_reviews WHERE player=?", (account,))
        pubs = [json.loads(body) for (body,) in records]
        return self.with_patch_versions([p for p in pubs if p.get("lobby_type") == row.get("lobby_type")
                                         and p.get("game_mode") == row.get("game_mode")])

    def with_patch_versions(self, rows):
        patches = list(self.db.execute("SELECT released, version FROM valve_patches ORDER BY released, version"))
        stamps = [stamp for stamp, _ in patches]
        for row in rows:
            i = bisect_right(stamps, row.get("start_time") or 0) - 1
            if i >= 0:
                row["patch_version"] = patches[i][1]
        return rows

    def references(self):
        records = {}
        if REFERENCE_FILE.exists():
            with gzip.open(REFERENCE_FILE, "rt", encoding="utf8") as source:
                records = {r["match_id"]: r for r in json.load(source)}
        for mid, body in self.db.execute("SELECT match_id,body FROM hero_reference"):
            row = json.loads(body)
            # Keep the release version supplied with the bundled observation when
            # a fresh local copy lacks its own Valve calendar.
            row["patch_version"] = row.get("patch_version") or records.get(mid, {}).get("patch_version")
            records[mid] = row
        return self.with_patch_versions(list(records.values()))

    def cached_match(self, match_id, account, slot):
        if slot is None:
            record = self.db.execute("SELECT body FROM games WHERE match_id=? AND player=?",
                                     (match_id, account)).fetchone()
            if not record:
                record = self.db.execute("SELECT body FROM match_reviews WHERE match_id=? AND player=?",
                                         (match_id, account)).fetchone()
        else:
            record = self.db.execute("SELECT body FROM match_reviews WHERE match_id=? AND slot=?",
                                     (match_id, slot)).fetchone()
            if not record:
                record = self.db.execute("SELECT body FROM games WHERE match_id=? AND json_extract(body,'$.player_slot')=?",
                                         (match_id, slot)).fetchone()
        return self.with_patch_versions([json.loads(record[0])])[0] if record else None

    def save_patches(self, entries):
        with self.db:
            self.db.execute("DELETE FROM valve_patches")
            self.db.executemany("INSERT INTO valve_patches VALUES (?,?)", entries)

    def contains(self, account, match_id):
        return self.db.execute("SELECT 1 FROM games WHERE player=? AND match_id=?", (account, match_id)).fetchone() is not None


def normalize(raw, account, directory, *, slot=None):
    player = select_player(raw, account, slot)
    if player is None:
        return None
    row = extract_match(raw, account, player=player)
    hero = directory.get(row["hero_id"], {})
    row["hero_name"] = hero.get("localized_name")
    league = raw.get("league") if isinstance(raw.get("league"), dict) else {}
    row["league_name"] = league.get("name") or raw.get("league_name")
    row["league"] = {key: league[key] for key in ("leagueid", "tier") if key in league}
    row.update(event_data(raw, account, hero_unit=hero.get("name"), player=player))
    row["detail"] = match_detail(raw, player, directory)
    row["detail"]["build"] = extract_build(raw, row["player_slot"], directory)
    row["hero_samples"] = extract_hero_samples(raw)
    row["review_version"] = 2
    for field in ("player_name", "team_name", "opponent"):
        row[field] = row["detail"][field]
    return row


def pages(api, account, limit):
    count = 0
    before = None
    while limit is None or count < limit:
        page = api.player_pro_ids(account, before)
        if not page:
            return
        for match_id in page:
            if limit is not None and count == limit:
                return
            yield match_id
            count += 1
        before = page[-1]


def sync(archive, api, account, ids, refresh=False, log=print):
    counters = {"seen": 0, "saved": 0, "cached": 0, "missing": 0}
    directory = None
    for match_id in ids:
        counters["seen"] += 1
        if not refresh and archive.contains(account, match_id):
            counters["cached"] += 1
            continue
        if directory is None:
            try:
                directory = archive.hero_names(api)
            except SourceError as exc:
                log(f"Справочник героев недоступен. Матчи будут сохранены с ID героев. {exc}")
                directory = {}
        try:
            raw = api.match(match_id)
        except SourceError as exc:
            if exc.status != 404:
                raise
            counters["missing"] += 1
            log(f"{match_id}: карточка пока недоступна")
            continue
        row = normalize(raw, account, directory)
        if row is None or not raw.get("leagueid"):
            counters["missing"] += 1
            log(f"{match_id}: нет игрока или турнира в карточке")
            continue
        archive.save(row)
        counters["saved"] += 1
        if counters["saved"] % 10 == 0:
            log(f'Сохранено {counters["saved"]} матчей')
    return counters
