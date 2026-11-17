"""Import an editable video's caption track through the YouTube Data API."""

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .source import SourceError


_ENDPOINT = "https://www.googleapis.com/youtube/v3/captions"
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_LIMIT = 8 * 1024 * 1024


def video_id(video: str) -> str:
    """Extract a video ID without making a request to the supplied URL."""
    message = "Укажи ID видео YouTube или HTTPS-ссылку на конкретное видео."
    if not isinstance(video, str):
        raise ValueError(message)
    video = video.strip()
    if _VIDEO_ID.fullmatch(video):
        return video
    try:
        parsed = urlsplit(video)
        host = parsed.netloc.lower()
        parts = parsed.path.strip("/").split("/")
        candidate = ""
        if host == "youtu.be" and len(parts) == 1:
            candidate = parts[0]
        elif host in ("youtube.com", "m.youtube.com", "www.youtube.com"):
            if parts == ["watch"]:
                ids = parse_qs(parsed.query).get("v", [])
                if len(ids) == 1:
                    candidate = ids[0]
            elif len(parts) == 2 and parts[0] in ("embed", "shorts", "live"):
                candidate = parts[1]
        if parsed.scheme == "https" and _VIDEO_ID.fullmatch(candidate):
            return candidate
    except ValueError:
        pass
    raise ValueError(message)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(url, token, media_type):
    request = Request(url, headers={"Accept": media_type, "User-Agent": "pro-replay-lab/1"})
    request.add_unredirected_header("Authorization", "Bearer " + token)
    try:
        with build_opener(_NoRedirects()).open(request, timeout=20) as response:
            data = response.read(_LIMIT + 1)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        hints = {
            401: "Проверь срок действия токена OAuth.",
            403: "Проверь доступ на редактирование видео, область доступа OAuth и квоту API.",
            404: "Видео или дорожка субтитров недоступны.",
            429: "Исчерпан лимит запросов. Попробуй позже.",
        }
        raise SourceError(f"YouTube API (HTTP {status}). {hints.get(status, 'Запрос не выполнен.')}", status) from None
    except (OSError, HTTPException):
        raise SourceError("Не удалось связаться с YouTube API.") from None
    if len(data) > _LIMIT:
        raise SourceError("Ответ YouTube API превышает 8 МиБ.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        raise SourceError("YouTube API вернул текст в неизвестной кодировке.") from None


def download_captions(video: str, language: str, token: str) -> str:
    """Return UTF-8 WebVTT for an exact language, preferring a published manual track."""
    identifier = video_id(video)
    if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
        raise ValueError("Укажи код языка субтитров, например ru, en или pt-BR.")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
        raise ValueError("Для загрузки субтитров требуется действующий токен YouTube OAuth.")
    query = urlencode({"part": "snippet", "videoId": identifier})
    body = _read(f"{_ENDPOINT}?{query}", token, "application/json")
    try:
        payload = json.loads(body)
        tracks = payload["items"]
        if not isinstance(tracks, list):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise SourceError("Не удалось прочитать список дорожек YouTube.") from None

    choices = []
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("snippet"), dict):
            continue
        snippet = track["snippet"]
        track_id = track.get("id")
        if not isinstance(track_id, str) or not track_id.strip() or track_id in (".", ".."):
            continue
        if str(snippet.get("language", "")).lower() != language.lower():
            continue
        kind = str(snippet.get("trackKind", "standard")).lower()
        if kind == "forced" or snippet.get("status", "serving") != "serving":
            continue
        choices.append((bool(snippet.get("isDraft")), kind == "asr", track_id))
    if not choices:
        raise SourceError(f"У видео нет готовой полной дорожки на языке {language}.")
    track_id = quote(min(choices)[2], safe="")
    text = _read(f"{_ENDPOINT}/{track_id}?tfmt=vtt", token, "application/octet-stream, text/vtt")
    if not re.match(r"^WEBVTT(?:[ \t][^\r\n]*)?\r?\n", text) or " --> " not in text:
        raise SourceError("YouTube API не вернул дорожку WebVTT с таймингами.")
    return text
