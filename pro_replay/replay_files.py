"""Read Valve demos once and keep an indexed, match-specific cache on disk."""

from contextlib import closing, contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from urllib.request import Request, urlopen
import zlib

from .replay_download import ReplayDownloadError, ensure_replay
from .source import SourceError, valve_patches


DECODER = Path(__file__).resolve().parent.parent / "replay-decoder"
FORMAT = "pro-replay-truth-v1"
_locks = [threading.Lock() for _ in range(16)]
_runtime_lock = threading.Lock()


class EvidenceError(RuntimeError):
    pass


@lru_cache(maxsize=64)
def _newer_patch(start):
    # A previously fetched calendar only establishes release order up to the
    # time it was fetched. Query once per new match beyond the bundled snapshot.
    versions = [entry for entry in valve_patches() if entry[0] <= start]
    return max(versions)[1] if versions else None


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _classpath(cache, bundle):
    app = DECODER / "coach-reader.jar"
    if not app.is_file() or _sha(app) != bundle["reader_sha256"]:
        raise EvidenceError("Файлы модуля чтения повторов неполны. Обнови копию проекта с GitHub.")
    files = [str(app)]
    folder = cache / "libraries"
    folder.mkdir(parents=True, exist_ok=True)
    with _runtime_lock:
        for dep in bundle["libraries"]:
            remote_path, digest = dep["path"], dep["sha256"]
            jar = folder / Path(remote_path).name
            if not jar.exists() or _sha(jar) != digest:
                url = "https://repo.maven.apache.org/maven2/" + remote_path
                with tempfile.TemporaryDirectory(dir=folder) as temp:
                    staged = Path(temp) / jar.name
                    try:
                        with urlopen(Request(url, headers={"User-Agent": "ProReplayLab/1"}), timeout=45) as source:
                            with staged.open("wb") as target:
                                shutil.copyfileobj(source, target)
                    except OSError as exc:
                        raise EvidenceError("Не удалось загрузить модуль чтения повторов из Maven Central. Повтори попытку.") from exc
                    if _sha(staged) != digest:
                        raise EvidenceError("Загруженный модуль чтения не прошёл проверку целостности.")
                    staged.replace(jar)
            files.append(str(jar))
    return os.pathsep.join(files)


def _decode(demo, output, cache, bundle):
    java = shutil.which("java")
    if not java:
        raise EvidenceError("Для чтения файлов Valve требуется Java 21 или новее.")
    arguments = [java, "-Xmx1g", "-cp", _classpath(cache, bundle), "pro.replay.Decoder", str(demo), str(output)]
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, timeout=900, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise EvidenceError("Чтение повтора заняло больше 15 минут.") from exc
    if result.returncode:
        if b"UnsupportedClassVersionError" in result.stderr:
            raise EvidenceError("Для чтения файлов Valve требуется Java 21 или новее.")
        raise EvidenceError("Не удалось прочитать этот формат повтора Valve.")


def _index_frames(source, destination, match_id):
    """The decoder emits a JSON object with one complete frame or event per line."""
    with source.open("rb") as stream, closing(sqlite3.connect(destination)) as db:
        header = json.loads(stream.readline().strip().removesuffix(b"[") + b"[]}")
        if header.get("format") != FORMAT or header.get("match_id") != match_id:
            raise EvidenceError("ID матча в файле повтора отличается от запрошенного.")
        db.execute("CREATE TABLE frames(time INTEGER PRIMARY KEY, data BLOB NOT NULL)")
        db.execute("CREATE TABLE info(match_id INTEGER PRIMARY KEY, events BLOB NOT NULL)")
        events, in_frames, complete, count = [], True, False, 0
        for line in stream:
            line = line.strip()
            if not line:
                continue
            if line == b'],"events":[' and in_frames:
                in_frames = False
            elif line == b"]}" and not in_frames:
                complete = True
                break
            else:
                body = line.removesuffix(b",")
                record = json.loads(body)
                if not isinstance(record, dict) or type(record.get("time")) is not int:
                    raise EvidenceError("В повторе нарушена разметка игрового времени.")
                if in_frames:
                    if type(record.get("tick")) is not int or not isinstance(record.get("heroes"), dict):
                        raise EvidenceError("В повторе отсутствует состояние героев.")
                    db.execute("INSERT INTO frames VALUES (?,?)", (record["time"], zlib.compress(body, 1)))
                    count += 1
                else:
                    events.append(record)
        if not complete or not count or stream.read().strip():
            raise EvidenceError("Файл повтора прочитан не полностью.")
        db.execute("INSERT INTO info VALUES (?,?)", (match_id, json.dumps(events, separators=(",", ":"))))
        db.commit()


def _valid_index(path, mid):
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(path)) as db:
            return db.execute("SELECT match_id FROM info").fetchone() == (mid,)
    except sqlite3.Error:
        return False


def _prepare(raw, cache):
    bundle = json.loads((DECODER / "runtime.json").read_text(encoding="utf8"))
    mid = raw["match_id"]
    if type(mid) is not int or mid <= 0:
        raise EvidenceError("Некорректный ID повтора.")
    stamp = bundle["reader_sha256"][:16]
    path = cache / f"{mid}-{stamp}-v1.sqlite"
    with _locks[mid % len(_locks)]:
        if _valid_index(path, mid):
            return path
        cache.mkdir(parents=True, exist_ok=True)
        demo = ensure_replay(raw, cache)
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            output, index = Path(temp) / "frames.json", Path(temp) / "frames.sqlite"
            _decode(demo, output, cache, bundle)
            _index_frames(output, index, mid)
            if not _valid_index(path, mid):
                index.replace(path)
    return path


@contextmanager
def replay_evidence(raw, cache):
    """Return a lazy time window reader or a human-readable reason for journal-only review."""
    try:
        path = _prepare(raw, Path(cache))
        db = sqlite3.connect(path)
    except (ReplayDownloadError, EvidenceError) as exc:
        yield None, str(exc)
        return
    except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
        yield None, f"Не удалось открыть данные повтора ({type(exc).__name__})."
        return
    with closing(db):
        @lru_cache(maxsize=8)
        def frame_window(first, last):
            rows = db.execute("SELECT data FROM frames WHERE time BETWEEN ? AND ? ORDER BY time", (first, last))
            return [json.loads(zlib.decompress(blob)) for (blob,) in rows]

        events = json.loads(db.execute("SELECT events FROM info WHERE match_id=?", (raw["match_id"],)).fetchone()[0])
        timeline = {"format": FORMAT, "match_id": raw["match_id"], "frame_window": frame_window, "events": events}
        from .replay_verification import CASTS
        start = raw.get("start_time")
        if type(start) is int and start > CASTS["through"]:
            try:
                timeline["patch_version"] = _newer_patch(start)
            except SourceError:
                pass
        yield timeline, ""
