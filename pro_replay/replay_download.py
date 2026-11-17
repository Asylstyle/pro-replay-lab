"""Cache complete Valve Source 2 demos for a match, independent of filename compression."""

import bz2
from contextlib import contextmanager
from http.client import HTTPException
import os
from pathlib import Path
import re
import tempfile
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAGIC = b"PBDEMS2\x00"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
BLOCK = 1 << 20
MAX_DOWNLOAD = 512 << 20
MAX_DEMO = 1 << 30
TIME_LIMIT = 360

# The coach HTTP server can receive a second request for the same match.
_locks = [Lock() for _ in range(32)]
_url = re.compile(r"(?P<scheme>https?)://replay(?P<cluster>[1-9][0-9]{0,3})"
                  r"\.(?P<domain>valve\.net|dota2\.com\.cn)/570/(?P<mid>[1-9][0-9]{0,18})"
                  r"_(?P<salt>[0-9]{1,10})\.dem\.bz2")


class ReplayDownloadError(RuntimeError):
    """A precise replay cannot be obtained; ordinary fight analysis can continue."""


def _valid_int(value, upper):
    return type(value) is int and 0 < value <= upper


def replay_url(raw):
    """Accept OpenDota's Valve URL or construct it from the coordinator fields."""
    mid = raw.get("match_id")
    if not _valid_int(mid, 2**63 - 1):
        raise ReplayDownloadError("У матча нет корректного ID.")
    cluster, salt = raw.get("cluster"), raw.get("replay_salt")
    value = raw.get("replay_url")
    match = _url.fullmatch(value) if isinstance(value, str) else None
    if (match and int(match["mid"]) == mid and _valid_int(int(match["salt"]), 2**32 - 1) and
            (cluster is None or cluster == int(match["cluster"])) and
            (salt is None or salt == int(match["salt"]))):
        return value
    if _valid_int(cluster, 9999) and _valid_int(salt, 2**32 - 1):
        domain = "dota2.com.cn" if cluster in (413, 415, 417) else "valve.net"
        return f"http://replay{cluster}.{domain}/570/{mid}_{salt}.dem.bz2"
    raise ReplayDownloadError("OpenDota пока не указала ссылку Valve на полный повтор этого матча.")


class _ReplayRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = _url.fullmatch(req.full_url), _url.fullmatch(newurl)
        if not old or not new or (old["mid"], old["salt"]) != (new["mid"], new["salt"]):
            raise ReplayDownloadError("Valve вернула неожиданную ссылку вместо файла повтора.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, packed):
    request = Request(url, headers={"User-Agent": "pro-replay-lab/1", "Accept-Encoding": "identity"})
    opener = build_opener(_ReplayRedirect())
    deadline = time.monotonic() + TIME_LIMIT
    for attempt in range(2):
        packed.seek(0)
        packed.truncate()
        try:
            with opener.open(request, timeout=30) as response:
                value = response.headers.get("Content-Length", "")
                length = int(value) if value.isdecimal() and len(value) < 18 else None
                if length is not None and length > MAX_DOWNLOAD:
                    raise ReplayDownloadError("Файл повтора превышает лимит загрузки 512 МиБ.")
                # Check wall time between socket reads even if a slow server
                # keeps the connection alive without filling an entire block.
                while chunk := response.read1(BLOCK):
                    if packed.tell() + len(chunk) > MAX_DOWNLOAD:
                        raise ReplayDownloadError("Файл повтора превышает лимит загрузки 512 МиБ.")
                    if time.monotonic() > deadline:
                        raise ReplayDownloadError("Истекло время загрузки повтора с сервера Valve.")
                    packed.write(chunk)
                if length is not None and length != packed.tell():
                    raise HTTPException("incomplete replay download")
            packed.seek(0)
            return
        except HTTPError as exc:
            with exc:
                if exc.code in (404, 410):
                    raise ReplayDownloadError("Полный повтор этого матча уже недоступен на сервере Valve.") from exc
                if exc.code not in (408, 500, 502, 503, 504) or attempt:
                    raise ReplayDownloadError(f"Сервер повторов Valve ответил HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, ConnectionError, HTTPException) as exc:
            if attempt:
                raise ReplayDownloadError("Не удалось загрузить полный повтор с сервера Valve.") from exc
        time.sleep(1)


@contextmanager
def _decoded(packed):
    prefix = packed.read(8)
    packed.seek(0)
    if prefix == MAGIC:
        yield packed
        return
    if prefix.startswith(b"BZh"):
        with bz2.BZ2File(packed) as source:
            yield source
        return
    if prefix.startswith(ZSTD_MAGIC):
        try:
            from compression import zstd
        except ImportError:
            try:
                from backports import zstd
            except ImportError as exc:
                raise ReplayDownloadError("Для этого повтора нужен модуль Zstandard. В Python 3.14 он встроен. "
                                          "Для Python 3.10–3.13 выполни py -m pip install backports.zstd.") from exc
        try:
            with zstd.ZstdFile(packed) as source:
                yield source
        except zstd.ZstdError as exc:
            raise ReplayDownloadError("Повреждён сжатый поток повтора Valve.") from exc
        return
    raise ReplayDownloadError("Вместо файла повтора сервер вернул данные неизвестного формата.")


def _varint(stream):
    value = 0
    for offset in range(0, 35, 7):
        byte = stream.read(1)
        if not byte:
            break
        value |= (byte[0] & 127) << offset
        if byte[0] < 128:
            return value
    return None


def _complete(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
            size = os.fstat(stream.fileno()).st_size
            offset = int.from_bytes(header[8:12], "little")
            if header[:8] != MAGIC or not 16 <= offset < size <= MAX_DEMO:
                return False
            # Source 2 points to DEM_FileInfo (2), optionally compressed (flag 64).
            stream.seek(offset)
            command, tick, length = (_varint(stream) for _ in range(3))
            return command in (2, 66) and tick is not None and length is not None and 0 < length <= size - stream.tell()
    except FileNotFoundError:
        return False


def ensure_replay(raw, cache_dir):
    """Return cache_dir/<match_id>.dem, publishing only fully downloaded/unpacked files."""
    mid = raw.get("match_id")
    if not _valid_int(mid, 2**63 - 1):
        raise ReplayDownloadError("У матча нет корректного ID.")
    folder = Path(cache_dir)
    path = folder / f"{mid}.dem"
    with _locks[mid % len(_locks)]:
        staging = None
        try:
            if _complete(path):
                return path
            url = replay_url(raw)
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder) as packed:
                _download(url, packed)
                with _decoded(packed) as source, tempfile.NamedTemporaryFile(
                        dir=folder, prefix=f".{mid}-", suffix=".part", delete=False) as output:
                    staging = Path(output.name)
                    while chunk := source.read(BLOCK):
                        if output.tell() + len(chunk) > MAX_DEMO:
                            raise ReplayDownloadError("Распакованный повтор превышает 1 ГиБ.")
                        output.write(chunk)
            if not _complete(staging):
                raise ReplayDownloadError("Файл Source 2 неполный. Для разбора нужен целый повтор.")
            if not _complete(path):
                try:
                    staging.replace(path)
                except PermissionError:
                    # Another process may have published and already mapped the demo on Windows.
                    if not _complete(path):
                        raise
            return path
        except (OSError, EOFError, ValueError) as exc:
            raise ReplayDownloadError("Не удалось распаковать или сохранить файл повтора.") from exc
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
