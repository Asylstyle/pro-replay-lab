import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pro_replay.coach_lessons import retrieve_lessons, train_lessons


class LessonTrainingTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.url = "https://youtu.be/abcdefghijk"
        self.manifest = self.dir / "lessons.json"

    def fit(self, *sources):
        self.manifest.write_text(json.dumps({"sources": sources}), encoding="utf-8")
        return train_lessons(self.manifest)

    def source(self, **fields):
        return {"title": "Тренер", "url": self.url, **fields}

    def test_rolling_vtt_merges_overlap_and_keeps_cue_times(self):
        vtt = self.dir / "carry.vtt"
        content = ("WEBVTT\n\nNOTE commentary\nnot a lesson\n\n"
                   "00:01:20.250 --> 00:01:24.000 align:start\n<c>farm next wave</c>\n\n"
                   "00:01:23.950 --> 00:01:27.500\nnext wave then camp\n\n"
                   "03:20.000 --> 03:24.000\nchoose Black King Bar\n")
        vtt.write_text(content, encoding="utf-8-sig")
        model = self.fit(self.source(file="carry.vtt", roles=[1]))
        self.assertEqual(model["passage_count"], 2)
        hit, = retrieve_lessons(model, "фарм волна", position=1)
        self.assertEqual((hit["start"], hit["end"]), (80.25, 87.5))
        self.assertEqual(hit["text"], "farm next wave then camp")
        self.assertEqual(hit["kind"], "subtitle")
        self.assertEqual(hit["url"], "https://www.youtube.com/watch?v=abcdefghijk&t=80s")
        self.assertEqual(vtt.read_text(encoding="utf-8-sig"), content)
        self.assertFalse(retrieve_lessons(model, "фарм", position=5))
        self.assertFalse(retrieve_lessons(model, "брюссель"))

    def test_srt_commas_and_corpus_frequencies_survive_json(self):
        (self.dir / "team.srt").write_text("1\n00:05:04,600 --> 00:05:08,200\nvision observer ward\n\n"
                                           "2\n00:06:00,000 --> 00:06:07,800\nward sentry roshan\n",
                                           encoding="utf-8")
        model = self.fit(self.source(file="team.srt"))
        self.assertGreater(model["idf"]["objectives"], model["idf"]["vision"])
        hits = retrieve_lessons(json.loads(json.dumps(model)), "рошан", position=5)
        self.assertEqual([h["start"] for h in hits], [360.0])

    def test_plain_text_accepts_known_times_and_keeps_untimed_text_unanchored(self):
        (self.dir / "notes.txt").write_text("Золото после отводов.\nДавление на лес.\n\n"
                                            "[02:29] Сначала нажать стан.\nЗатем выйти из драки.\n"
                                            "04:30 Другой тайминг покупки.\n", encoding="utf-8")
        model = self.fit(self.source(file="notes.txt"))
        report = {row["start"]: row for row in model["segments"]}
        self.assertEqual(set(report), {None, 149.0, 270.0})
        self.assertTrue(report[149]["text"].endswith("из драки."))
        self.assertNotIn("&t=", report[None]["url"])
        self.assertEqual(report[None]["kind"], "local_text")

    def test_late_terms_in_long_text_are_present_in_returned_evidence(self):
        (self.dir / "notes.txt").write_text("волна " * 65 + "драка BKB", encoding="utf-8")
        model = self.fit(self.source(file="notes.txt"))
        hit, = retrieve_lessons(model, "BKB")
        self.assertIn("BKB", hit["text"])

    def test_fits_directly_from_timed_caption_concepts_without_publishing_words(self):
        (self.dir / "speech.txt").write_text("[00:05] фарм до окна\n[00:10] БКБ азимут БКБ\n"
                                             "[00:12] Драка\n[00:15] рошан после окна\n", encoding="utf-8")
        window = {"text": "Останови повтор и оцени решение.", "start": 10, "end": 15}
        note = self.source(kind="transcript_note", segments=[window])
        model = self.fit(dict(note, concept_file="speech.txt"))
        hit, = retrieve_lessons(model, "black king bar")
        self.assertEqual(hit["caption_concepts"], {"bkb": 2, "fight": 1})
        self.assertNotIn("азимут", json.dumps(model, ensure_ascii=False))
        self.assertFalse(retrieve_lessons(model, "фарм рошан"))
        portable = dict(note, segments=[dict(window, caption_concepts=hit["caption_concepts"])])
        self.assertEqual(self.fit(portable), model)
        self.assertFalse(retrieve_lessons(self.fit(note), "бкб"))
        with self.assertRaisesRegex(ValueError, "частоты"):
            self.fit(dict(note, segments=[dict(window, caption_concepts={"азимут": 1})]))

    def test_unsafe_invalid_and_unreadable_sources_fail_explained(self):
        for source in [self.source(file="../neighbor.srt"), self.source(file="wrong.exe"),
                       self.source(roles=[True], segments=[{"text": "урон"}]),
                       self.source(url="javascript:foo", segments=[{"text": "урон"}]),
                       self.source(segments=[{"text": "урон", "start": -12}]),
                       self.source(segments=[{"text": "урон", "start": 30, "end": 29}])]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.fit(source)
        (self.dir / "notes.srt").write_text("00:10:80 --> 00:12:01\nfarm\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "таймкод"):
            self.fit(self.source(file="notes.srt"))

    def test_source_kinds_timestamps_links_and_deduplication(self):
        step = {"text": "ward vision", "url": "https://example.org/tutorial#page10"}
        model = self.fit(self.source(url="https://example.org/tutorial", kind="licensed_note",
                                     segments=[step, dict(step)]),
                         self.source(kind="description_note", segments=[{"text": "фарм на линии"}]))
        self.assertEqual(model["source_count"], 2)
        self.assertEqual(model["passage_count"], 2)
        self.assertEqual(retrieve_lessons(model, "обзор")[0]["url"], step["url"])
        self.assertEqual(retrieve_lessons(model, "фарм")[0]["kind"], "description_note")

    def test_bundled_training_can_be_reproduced_and_cites_satanic(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        trained = train_lessons(examples / "coach-lessons-sources.json")
        bundled = json.loads((examples / "coach-lessons-model.json").read_text(encoding="utf-8"))
        self.assertEqual(bundled, trained)
        hit = retrieve_lessons(bundled, "предметы bkb драка", position=1, limit=1)[0]
        self.assertEqual((hit["kind"], hit["start"], hit["end"]), ("transcript_note", 1460, 1467))
        self.assertEqual(hit["caption_concepts"], {"bkb": 2, "fight": 1})
        self.assertIn("Xqapp58b9_Q&t=1460s", hit["url"])
        self.assertIn("Copyright (c) ModDota", bundled["notices"][0]["text"])


if __name__ == "__main__":
    unittest.main()
