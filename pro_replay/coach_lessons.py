"""Fit a small offline TF–IDF index over timestamped Dota teaching material."""

from collections import Counter
from html import unescape
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from .youtube_captions import video_id


_TIME = r"(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_CUE = re.compile(rf"^({_TIME})\s+-->\s+({_TIME})(?:\s+.*)?$")
_TXT_TIME = re.compile(rf"^\[?({_TIME})\]?\s+(.+)$")
_SKIP_WORDS = set("""как это этой этом чтобы что для при или тут кто где под над после перед если
    когда между него этих какие какой уже игры игру игре игрок игрока героя герое герои
    свои свою свой разбор сравни проверь посмотри для себя как его was can only more
    карту карте матч матча совет dota guide lessons lesson play game your this that what how with
    from which when then about you the for and are his her not have before after""".split())

# Shared terms let a Russian observation find a passage with English subtitles.
# Only specific game concepts are folded together; uncommon item/hero names remain searchable.
_VOCAB = {
    "farm": ("farm*", "фарм*", "farming"),
    "gold": ("gold", "gpm", "networth", "нетворс*", "золот*", "экономик*"),
    "creep": ("lh", "cs", "last_hits", "добив*", "крип*", "creep*"),
    "exp": ("xp", "xpm", "опыт*", "experience"),
    "lane": ("laning", "lane*", "лини*", "лайн*"),
    "wave": ("волн*", "wave*"),
    "vision": ("ward*", "обзор*", "вижен*", "вард*", "vision", "detection", "sentry", "observer"),
    "fight": ("fight*", "teamfight*", "файт*", "драк*", "драч*", "дратьс*", "сражен*"),
    "items": ("items", "item", "itemization", "build*", "предмет*", "билд*", "закуп*", "покуп*"),
    "bkb": ("бкб", "бкбш*"),
    "stick": ("стик*", "magic_stick"),
    "circlet": ("циркл*", "циркулет*"),
    "branch": ("iron_branch", "веточк*"),
    "draft": ("draft*", "драфт*", "matchup*", "матчап*", "пик"),
    "objectives": ("objective*", "объект*", "tower*", "башн*", "строени*", "roshan", "рошан*"),
    "positioning": ("positioning", "позициони*", "дистанц*", "distance"),
    "pull": ("pull*", "отвод*", "пулл*"),
    "rotation": ("rotat*", "ротац*", "ганг*", "gank*"),
    "death": ("die", "death*", "dying", "смерт*", "погиб*"),
    "spell": ("spell*", "каст*", "способност*", "заклинан*"),
    "timing": ("тайминг*", "timing*"),
    "control": ("stun*", "стан", "контрол*", "disable*"),
    "heal": ("heal*", "лечени*", "хил", "лечение"),
}
_EXACT = {v: k for k, aliases in _VOCAB.items() for v in (k, *aliases) if "*" not in v}
_PREFIX = [(v[:-1], k) for k, aliases in _VOCAB.items() for v in aliases if v.endswith("*")]


def _terms(text):
    text = re.sub(r"last[\s-]+hits?|ласт[\s-]*хит\w*", " last_hits ", text.lower().replace("ё", "е"))
    text = re.sub(r"\bblack king bar\b", " bkb ", text)
    text = re.sub(r"\bmagic stick\b", " magic_stick ", text)
    text = re.sub(r"\biron branch(?:es)?\b", " iron_branch ", text)
    words = re.findall(r"[a-zа-я][a-zа-я_0-9]+", text)
    result = []
    for word in words:
        if word in _SKIP_WORDS or len(word) < 2:
            continue
        term = _EXACT.get(word) or next((k for prefix, k in _PREFIX if word.startswith(prefix)), word)
        result.append(term)
    return result


def _url(value, start=None):
    if not isinstance(value, str):
        raise ValueError("В источнике нужна ссылка HTTPS.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Нужна публичная ссылка HTTPS без данных для входа.")
    host = parsed.hostname.lower()
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"):
        video = video_id(value)
        stamp = f"&t={int(start)}s" if start is not None else ""
        return f"https://www.youtube.com/watch?v={video}{stamp}"
    return value


def _seconds(timestamp):
    parts = timestamp.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2])
    if seconds >= 60 or (len(parts) == 3 and minutes >= 60):
        raise ValueError(f"Некорректный таймкод {timestamp}.")
    return seconds + 60 * minutes + (3600 * int(parts[0]) if len(parts) == 3 else 0)


def _clean(text):
    return " ".join(unescape(re.sub(r"<[^>\n]+>", "", text)).split())


def _subtitle_cues(content):
    cues = []
    for block in re.split(r"\n[ \t]*\n+", content.strip()):
        lines = block.strip().splitlines()
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if index is None:
            continue
        match = _CUE.fullmatch(lines[index].strip())
        if not match:
            raise ValueError("Некорректная строка тайминга в субтитрах.")
        start, end = map(_seconds, match.groups())
        words = _clean(" ".join(lines[index + 1:])).split()
        if start > end:
            raise ValueError("Конец фрагмента предшествует началу.")
        if words:
            cues.append((start, end, words))
    return sorted(cues, key=lambda c: c[0])


def _group_cues(cues):
    passages = []
    for start, end, words in cues:
        if (not passages or start - passages[-1]["end"] > 3 or
                end - passages[-1]["start"] > 32 or len(passages[-1]["words"]) >= 52):
            passages.append({"start": start, "end": end, "words": words})
            continue
        last = passages[-1]
        overlap = 0
        if start <= last["end"] + 0.1:
            for n in range(min(len(last["words"]), len(words)), 0, -1):
                if last["words"][-n:] == words[:n]:
                    overlap = n
                    break
        last["words"].extend(words[overlap:])
        last["end"] = max(last["end"], end)
    return [{"start": p["start"], "end": p["end"], "text": " ".join(p["words"])} for p in passages]


def _text_passages(content):
    passages = []
    for block in re.split(r"\n\s*\n", content):
        current = None
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            match = _TXT_TIME.match(line)
            if match or current is None:
                current = {"start": _seconds(match[1]) if match else None, "text": match[2] if match else line}
                passages.append(current)
            else:
                current["text"] += " " + line
    return passages


def caption_concepts(path, segments):
    """Count fixed game concepts in cues that start within each half-open video interval.

    The resulting dictionaries contain no caption strings, names outside the fixed
    vocabulary or word order. A timestamped local TXT export, SRT or VTT works.
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".txt":
        rows = _text_passages(content)
        cues = [(r["start"], r["start"], r["text"].split()) for r in rows if r["start"] is not None]
    elif path.suffix.lower() in (".srt", ".vtt"):
        cues = _subtitle_cues(content)
    else:
        raise ValueError("Для признаков субтитров нужен TXT с таймкодами, SRT или VTT.")
    if not cues:
        raise ValueError("В файле субтитров нет фрагментов с таймкодами.")
    result = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Каждому фрагменту нужны границы времени.")
        start, end = segment.get("start"), segment.get("end")
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)) or not 0 <= start < end:
            raise ValueError("Для частот субтитров нужны начало и конец фрагмента в секундах.")
        pieces = _group_cues([cue for cue in cues if start <= cue[0] < end])
        found = Counter(t for p in pieces for t in _terms(p["text"]) if t in _VOCAB)
        result.append(dict(sorted(found.items())))
    return result


def _source_file(directory, name):
    if not isinstance(name, str) or not name:
        raise ValueError("Укажи путь к файлу субтитров или заметок.")
    path = (directory / name).resolve()
    if not path.is_relative_to(directory) or path.suffix.lower() not in (".srt", ".vtt", ".txt"):
        raise ValueError("Ожидается SRT, VTT или TXT в папке манифеста или её подпапке.")
    return path


def _source_segments(source, directory):
    if not isinstance(source, dict):
        raise ValueError("Каждый источник должен быть объектом.")
    if ("file" in source) == ("segments" in source):
        raise ValueError("Укажи один файл или список учебных фрагментов.")
    if "segments" in source:
        kind = source.get("kind", "editorial_note")
        if kind not in ("chapter_note", "description_note", "editorial_note", "licensed_note", "transcript_note"):
            raise ValueError("Неизвестный вид учебной заметки.")
        segments = source["segments"]
        if "concept_file" in source:
            if kind != "transcript_note" or not isinstance(segments, list):
                raise ValueError("concept_file используется для списка заметок по субтитрам.")
            counts = caption_concepts(_source_file(directory, source["concept_file"]), segments)
            segments = [dict(segment, caption_concepts=count) for segment, count in zip(segments, counts)]
        return kind, segments
    path = _source_file(directory, source["file"])
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".txt":
        return "local_text", _text_passages(content)
    return "subtitle", _group_cues(_subtitle_cues(content))


def _vector(terms, idf):
    weights = {k: (1 + math.log(freq)) * idf[k] for k, freq in Counter(terms).items() if k in idf}
    norm = math.sqrt(sum(value * value for value in weights.values()))
    return {k: v / norm for k, v in weights.items()} if norm else {}


def train_lessons(manifest_path):
    """Read local sources listed in a manifest and fit inverse document frequencies on their passages."""
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    sources = manifest.get("sources") if isinstance(manifest, dict) else None
    if not isinstance(sources, list) or not sources:
        raise ValueError("Для обучения нужен непустой список sources.")
    docs, terms_by_doc, seen = [], [], set()
    for source in sources:
        kind, segments = _source_segments(source, path.parent)
        if not isinstance(segments, list) or not segments:
            raise ValueError("Источник не содержит читаемых учебных фрагментов.")
        title = source.get("title")
        roles = source.get("roles", [])
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Добавь название источника.")
        if not isinstance(roles, list) or any(type(n) is not int or not 1 <= n <= 5 for n in roles):
            raise ValueError("roles принимает список позиций от 1 до 5.")
        url = _url(source.get("url"))
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
                raise ValueError("Каждому фрагменту нужен текст.")
            start, end = segment.get("start"), segment.get("end")
            if any(t is not None and (type(t) not in (int, float) or not math.isfinite(t) or t < 0)
                   for t in (start, end)) or (end is not None and (start is None or end < start)):
                raise ValueError("Укажи правильный диапазон времени в секундах.")
            concepts = segment.get("caption_concepts", {})
            if (not isinstance(concepts, dict) or (concepts and (kind != "transcript_note"
                    or start is None or end is None or start == end)) or any(
                    t not in _VOCAB or type(n) is not int or n <= 0 or n > 1_000_000 for t, n in concepts.items())):
                raise ValueError("Укажи частоты только для известных игровых понятий из субтитров.")
            evidence_url = _url(segment.get("url", url), start)
            words = _clean(segment["text"]).split()
            for i in range(0, len(words), 52):
                excerpt = " ".join(words[i:i + 52])
                terms = Counter(_terms(excerpt))
                identity = (evidence_url, start, end, excerpt)
                if not terms or identity in seen:
                    continue
                seen.add(identity)
                doc = {"title": title.strip(), "url": evidence_url, "source_url": url, "roles": roles,
                       "start": start, "end": end, "kind": kind, "text": excerpt}
                if i == 0 and concepts:
                    terms.update(concepts)
                    doc["caption_concepts"] = concepts
                docs.append(doc)
                terms_by_doc.append(terms)
    if not docs:
        raise ValueError("В материалах нет текста, пригодного для поиска.")
    frequency = Counter(term for terms in terms_by_doc for term in set(terms))
    idf = {term: 1 + math.log((1 + len(docs)) / (1 + frequency[term])) for term in sorted(frequency)}
    for doc, terms in zip(docs, terms_by_doc):
        doc["weights"] = _vector(terms, idf)
    return {"format": "pro-replay-lessons-v1", "algorithm": "TF-IDF + cosine", "idf": idf,
            "notices": manifest.get("notices", []),
            "source_count": len({d["source_url"] for d in docs}), "passage_count": len(docs), "segments": docs}


def retrieve_lessons(model, query, *, position=None, limit=2):
    """Return relevant, source-attributed passages; role is a constraint, never a reason to match."""
    if not isinstance(model, dict) or model.get("format") != "pro-replay-lessons-v1":
        raise ValueError("Неизвестный формат индекса учебных материалов.")
    if type(limit) is not int or limit <= 0:
        return []
    query_vec = _vector(_terms(query), model["idf"])
    if not query_vec:
        return []
    hits = []
    for doc in model["segments"]:
        if position and doc["roles"] and position not in doc["roles"]:
            continue
        score = sum(w * doc["weights"].get(k, 0) for k, w in query_vec.items())
        if score >= 0.12:
            hit = {k: v for k, v in doc.items() if k != "weights"}
            hit["score"] = round(score, 5)
            hits.append(hit)
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]
