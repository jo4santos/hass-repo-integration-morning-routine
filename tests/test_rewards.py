"""Tests of the v2.13.0 rewards: image generated from the photo (v2.13.1: confirmation follow-up;
v2.13.2: the last good image is kept on failure, transient errors are retried once) and the streak.

No network and no Home Assistant: the image service is a fake aiohttp session
and Home Assistant is stubbed exactly as in test_coordinator.

Run from the repository root with:  python3 -m unittest discover -s tests -v
"""
import asyncio
import base64
import copy
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types
import unittest
import warnings
from datetime import date, datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _loader import PACKAGE, PACKAGE_DIR  # noqa: E402
import test_coordinator as tc  # noqa: E402  (installs the Home Assistant stubs)

from morning_routine import reward_image as ri  # noqa: E402
from morning_routine import streak as st  # noqa: E402

integration = tc.integration
const = tc.const

TOKEN = "SECRET-TOKEN-abc123XYZ"
API_URL = "https://images.example.test/api/v1/ask"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes" * 4
JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-bytes" * 4
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"fake-webp" * 4
PHOTO = b"\xff\xd8\xff\xe0" + b"the-photo-of-the-child" * 3


def setUpModule():
    logging.disable(logging.CRITICAL)
    # the test loader imports the package __init__ under another name (see test_coordinator)
    warnings.filterwarnings("ignore", message="__package__ != __spec__.parent", category=DeprecationWarning)


def tearDownModule():
    logging.disable(logging.NOTSET)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _ok(image: bytes = PNG) -> bytes:
    return json.dumps({"answer": "", "media": [{"mime": "image/png", "data": _b64(image)}]}).encode()


# ---------------------------------------------------------------------------
# Pure logic: prompt
# ---------------------------------------------------------------------------

class TestPrompt(unittest.TestCase):
    def render(self, child, **kwargs):
        config = const.CHILDREN_CONFIG[child]
        return ri.render_prompt(
            const.DEFAULT_REWARD_IMAGE_PROMPT, name=config["name"], gender=config["gender"], **kwargs
        )

    def test_leonor_with_notes_and_activities(self):
        self.assertEqual(
            self.render("leonor", notes="tem 8 anos e já sabe ler bem", activities=["Taekwondo"], weekday="sexta-feira"),
            "Gera uma imagem engraçada em estilo cartoon com uma personagem baseada nesta fotografia. "
            "Deve ser divertida para uma menina ver (Leonor, tem 8 anos e já sabe ler bem). "
            "Deve conter algo relacionado com Taekwondo. "
            "A roupa que a criança está a usar na fotografia deve estar parecida no cartoon. "
            "Pode incluir uma pequena história inventada com pouco texto. "
            "Atenção ao texto na imagem: tem de estar em português de Portugal perfeito. "
            "Gera já a imagem nesta resposta, sem pedir confirmação.",
        )

    def test_duarte_without_notes_or_activities(self):
        self.assertEqual(
            self.render("duarte"),
            "Gera uma imagem engraçada em estilo cartoon com uma personagem baseada nesta fotografia. "
            "Deve ser divertida para um menino ver (Duarte). "
            "A roupa que a criança está a usar na fotografia deve estar parecida no cartoon. "
            "Pode incluir uma pequena história inventada com pouco texto. "
            "Atenção ao texto na imagem: tem de estar em português de Portugal perfeito. "
            "Gera já a imagem nesta resposta, sem pedir confirmação.",
        )

    def test_notes_only_and_activities_only(self):
        with_notes = self.render("duarte", notes="  tem 10 anos, gosta de futebol. ")
        self.assertIn("ver (Duarte, tem 10 anos, gosta de futebol). A roupa", with_notes)
        self.assertNotIn("relacionado", with_notes)

        with_activities = self.render("leonor", notes="", activities=["Música", "Taekwondo"])
        self.assertIn("ver (Leonor). Deve conter algo relacionado com Música e Taekwondo. A roupa", with_activities)

    def test_join_pt(self):
        self.assertEqual(ri.join_pt([]), "")
        self.assertEqual(ri.join_pt(None), "")
        self.assertEqual(ri.join_pt(["A"]), "A")
        self.assertEqual(ri.join_pt(["A", "B"]), "A e B")
        self.assertEqual(ri.join_pt(["A", "B", "C"]), "A, B e C")
        self.assertEqual(ri.join_pt(["A", " ", ""]), "A")

    def test_every_placeholder(self):
        text = ri.render_prompt(
            "{name}|{child_noun}|{notes}|{activities}|{weekday}|{notes_clause}|{activities_clause}",
            name="Leonor", gender="f", notes="8 anos", activities=["Música"], weekday="segunda-feira",
        )
        self.assertEqual(
            text, "Leonor|uma menina|8 anos|Música|segunda-feira|, 8 anos|Deve conter algo relacionado com Música."
        )
        self.assertEqual(ri.child_noun("m"), "um menino")
        self.assertEqual(ri.child_noun("f"), "uma menina")

    def test_robust_to_unknown_placeholders_and_braces(self):
        template = 'Olá {name}, {unknown} {0} {} {{x}} {"json": 1} {name'
        self.assertEqual(
            ri.render_prompt(template, name="Duarte", gender="m"),
            'Olá Duarte, {unknown} {0} {} {{x}} {"json": 1} {name',
        )
        self.assertEqual(ri.render_prompt(None, name="Duarte", gender="m"), "")
        self.assertEqual(ri.render_prompt("", name=None, gender=None), "")
        # values are inserted as they are, never re-interpreted as placeholders
        self.assertEqual(ri.render_prompt("{notes}", name="X", gender="m", notes="gosta de {name}"), "gosta de {name}")


# ---------------------------------------------------------------------------
# Pure logic: request and response
# ---------------------------------------------------------------------------

class TestRequest(unittest.TestCase):
    def test_body_shape(self):
        body = ri.build_request_body("Imagens", "Um pedido", PHOTO)
        self.assertEqual(list(body), ["topic", "text", "media"])
        self.assertEqual(body["topic"], "Imagens")
        self.assertEqual(body["text"], "Um pedido")
        self.assertEqual(body["media"], [{"mime": "image/jpeg", "data": _b64(PHOTO)}])
        self.assertEqual(base64.b64decode(body["media"][0]["data"]), PHOTO)
        json.dumps(body)  # serialisable

    def test_headers(self):
        self.assertEqual(
            ri.build_request_headers("abc"),
            {"Authorization": "Bearer abc", "Content-Type": "application/json"},
        )

    def test_url_validation(self):
        for good in ("https://orchestron.tech/api/v1/ask", "http://192.168.1.10:8080/ask", " https://x.pt "):
            self.assertTrue(ri.is_valid_api_url(good), good)
        for bad in ("", None, "orchestron.tech/api", "ftp://x.pt", "file:///etc/passwd", "https://", 5):
            self.assertFalse(ri.is_valid_api_url(bad), bad)


class TestParseResponse(unittest.TestCase):
    def assertFails(self, raw, code):
        with self.assertRaises(ri.RewardImageError) as caught:
            ri.parse_response(raw)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.message, ri.ERROR_MESSAGES[code])
        return caught.exception

    def test_success(self):
        self.assertEqual(ri.parse_response(_ok(PNG)), (PNG, "png"))
        self.assertEqual(ri.parse_response(_ok(JPEG)), (JPEG, "jpg"))
        self.assertEqual(ri.parse_response(_ok(WEBP)), (WEBP, "webp"))
        # str / dict input, data URI prefix and line breaks in the base64
        self.assertEqual(ri.parse_response(_ok(PNG).decode()), (PNG, "png"))
        wrapped = "data:image/png;base64," + "\n".join(_b64(PNG)[i:i + 16] for i in range(0, len(_b64(PNG)), 16))
        self.assertEqual(ri.parse_response({"media": [{"data": wrapped}]}), (PNG, "png"))

    def test_error_text(self):
        error = self.assertFails(json.dumps({"error": "quota exceeded"}), ri.ERR_SERVICE_ERROR)
        self.assertEqual(error.detail, "quota exceeded")
        # error wins over answer; long texts are truncated to one line
        error = self.assertFails({"error": "linha 1\nlinha 2 " + "x" * 500, "answer": "y"}, ri.ERR_SERVICE_ERROR)
        self.assertLessEqual(len(error.detail), 203)
        self.assertNotIn("\n", error.detail)

    def test_answer_text_only(self):
        error = self.assertFails(json.dumps({"answer": "Não consigo gerar essa imagem."}), ri.ERR_NO_MEDIA)
        self.assertIn("Não consigo gerar essa imagem.", error.detail)

    def test_missing_media(self):
        for payload in ({}, {"media": []}, {"media": None}, {"media": [{}]}, {"media": [{"data": ""}]},
                        {"media": [{"data": 5}]}, {"media": "abc"}, {"media": ["abc"]}):
            self.assertFails(payload, ri.ERR_NO_MEDIA)

    def test_not_json(self):
        for raw in (b"<html>502 Bad Gateway</html>", b"", "não é json", b"\xff\xfe\x00", b"[1, 2]", b'"texto"'):
            self.assertFails(raw, ri.ERR_INVALID_JSON)

    def test_bad_base64(self):
        for data in ("isto não é base64!!!", "abc", "####"):
            self.assertFails({"media": [{"data": data}]}, ri.ERR_BAD_BASE64)

    def test_wrong_signature(self):
        for content in (b"GIF89a" + b"x" * 20, b"<svg></svg>", b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVEfmt ", b"\x89PN"):
            self.assertFails({"media": [{"data": _b64(content)}]}, ri.ERR_NOT_AN_IMAGE)

    def test_oversize(self):
        self.assertEqual(ri.MAX_RESPONSE_BYTES, 15 * 1024 * 1024)
        self.assertFails(b" " * (ri.MAX_RESPONSE_BYTES + 1), ri.ERR_TOO_LARGE)
        self.assertFails({"media": [{"data": "A" * (ri.MAX_RESPONSE_BYTES + 4)}]}, ri.ERR_TOO_LARGE)

    def test_error_never_carries_image_data(self):
        data = _b64(b"GIF89a" + b"SENSITIVE-IMAGE-DATA" * 10)
        error = self.assertFails({"media": [{"data": data}]}, ri.ERR_NOT_AN_IMAGE)
        self.assertIsNone(error.detail)
        self.assertNotIn(data[:20], str(error))

    def test_files(self):
        self.assertEqual(ri.reward_filename("duarte", "20261113_082000"), "duarte_20261113_082000_reward.png")
        self.assertEqual(ri.reward_filename("leonor", "20261113_082000", "webp"), "leonor_20261113_082000_reward.webp")
        self.assertEqual(
            ri.reward_url("duarte_20261113_082000_reward.png", "20261113_082000"),
            "/local/morning_routine_photos/duarte_20261113_082000_reward.png?v=20261113_082000",
        )
        self.assertTrue(ri.is_reward_file("duarte_20261113_082000_reward.png"))
        self.assertTrue(ri.is_reward_file("leonor_20261113_082000_reward.jpg"))
        for other in ("duarte_20261113_082000.jpg", "duarte_breakfast_20261113_082000.webm", "duarte_reward_20261113.png", ""):
            self.assertFalse(ri.is_reward_file(other), other)

    def test_scrub(self):
        self.assertEqual(ri.scrub(f"Bearer {TOKEN} recusado", TOKEN), "Bearer *** recusado")
        self.assertEqual(ri.scrub("nada", None, ""), "nada")


# ---------------------------------------------------------------------------
# Pure logic: streak
# ---------------------------------------------------------------------------

MON, TUE, WED, THU, FRI, SAT, SUN = (date(2026, 11, day) for day in range(9, 16))
NEXT_MON, NEXT_TUE = date(2026, 11, 16), date(2026, 11, 17)


class TestStreak(unittest.TestCase):
    def test_days_are_what_they_claim(self):
        self.assertEqual([d.weekday() for d in (MON, FRI, SAT, SUN, NEXT_MON)], [0, 4, 5, 6, 0])

    def test_first_completion_and_once_a_day(self):
        data = {}
        self.assertEqual(st.current(data), (0, 0))
        self.assertTrue(st.on_completion(data, MON, True))
        self.assertEqual(st.current(data), (1, 1))
        self.assertEqual(data["streak_last_date"], "2026-11-09")
        self.assertFalse(st.on_completion(data, MON, True))  # same day: not counted twice
        self.assertEqual(st.current(data), (1, 1))

    def test_consecutive_days(self):
        data = {}
        for expected, day in enumerate((MON, TUE, WED, THU, FRI), start=1):
            st.on_reset(data, day, True)
            st.on_completion(data, day, True)
            self.assertEqual(st.current(data), (expected, expected))

    def test_weekend_does_not_break_with_business_days_only(self):
        data = {}
        st.on_completion(data, THU, True)
        st.on_completion(data, FRI, True)
        for day in (SAT, SUN, NEXT_MON):
            st.on_reset(data, day, True)
            self.assertEqual(st.current(data), (2, 2), day)
        st.on_completion(data, NEXT_MON, True)
        self.assertEqual(st.current(data), (3, 3))

    def test_weekend_breaks_when_every_day_counts(self):
        data = {}
        st.on_completion(data, FRI, False)
        st.on_reset(data, SAT, False)
        self.assertEqual(st.current(data), (1, 1))  # Friday was done: still alive on Saturday
        st.on_reset(data, SUN, False)  # Saturday missed
        self.assertEqual(st.current(data), (0, 1))
        st.on_completion(data, NEXT_MON, False)
        self.assertEqual(st.current(data), (1, 1))

    def test_missed_business_day_resets_at_next_reset(self):
        data = {}
        st.on_completion(data, MON, True)
        st.on_completion(data, TUE, True)
        st.on_completion(data, WED, True)
        st.on_reset(data, THU, True)
        self.assertEqual(st.current(data), (3, 3))  # Thursday not over yet
        st.on_reset(data, FRI, True)  # Thursday ended without a completed routine
        self.assertEqual(st.current(data), (0, 3))
        st.on_completion(data, FRI, True)
        self.assertEqual(st.current(data), (1, 3))  # best is kept

    def test_missed_friday_is_noticed_on_saturday_or_monday(self):
        for reset_day in (SAT, NEXT_MON):
            data = {}
            st.on_completion(data, THU, True)
            st.on_reset(data, reset_day, True)
            self.assertEqual(st.current(data), (0, 1), reset_day)

    def test_completion_without_reset_after_a_gap_starts_again(self):
        data = {}
        st.on_completion(data, MON, True)
        st.on_completion(data, TUE, True)
        st.on_completion(data, FRI, True)  # Wednesday and Thursday missed, no reset in between
        self.assertEqual(st.current(data), (1, 2))

    def test_weekend_completion_is_a_bonus(self):
        data = {}
        st.on_completion(data, FRI, True)
        st.on_completion(data, SAT, True)
        self.assertEqual(st.current(data), (2, 2))
        st.on_reset(data, NEXT_MON, True)
        st.on_completion(data, NEXT_MON, True)
        self.assertEqual(st.current(data), (3, 3))

    def test_best_streak(self):
        data = {}
        for day in (MON, TUE, WED):
            st.on_completion(data, day, True)
        st.on_reset(data, FRI, True)
        for day in (FRI, NEXT_MON):
            st.on_reset(data, day, True)
            st.on_completion(data, day, True)
        self.assertEqual(st.current(data), (2, 3))

    def test_manual_reset_the_same_day_keeps_today(self):
        data = {}
        st.on_completion(data, MON, True)
        st.on_completion(data, TUE, True)
        self.assertFalse(st.on_reset(data, TUE, True))
        self.assertEqual(st.current(data), (2, 2))
        self.assertFalse(st.on_completion(data, TUE, True))
        self.assertEqual(st.current(data), (2, 2))

    def test_uncompletion_takes_today_back(self):
        data = {}
        st.on_completion(data, MON, True)
        st.on_completion(data, TUE, True)
        self.assertTrue(st.on_uncompletion(data, TUE))
        self.assertEqual(st.current(data), (1, 1))
        self.assertEqual(data["streak_last_date"], "2026-11-09")
        self.assertFalse(st.on_uncompletion(data, TUE))  # only once
        st.on_completion(data, TUE, True)  # ticked again
        self.assertEqual(st.current(data), (2, 2))
        # yesterday can never be taken back
        st.on_reset(data, WED, True)
        self.assertFalse(st.on_uncompletion(data, WED))
        self.assertEqual(st.current(data), (2, 2))

    def test_garbage_in_storage(self):
        data = {"streak": "x", "best_streak": None, "streak_last_date": "not a date", "streak_undo": "?"}
        self.assertEqual(st.current(data), (0, 0))
        st.on_reset(data, MON, True)
        st.on_completion(data, MON, True)
        self.assertEqual(st.current(data), (1, 1))
        self.assertEqual(st.current({"streak": 5, "best_streak": 2}), (5, 5))
        json.dumps(data)  # stays serialisable for the store


# ---------------------------------------------------------------------------
# Pure logic: confirmation follow-up (v2.13.1)
# ---------------------------------------------------------------------------

class TestFollowUpLogic(unittest.TestCase):
    def test_continuation_fields(self):
        previous = {
            "answer": "Ficou à espera da tua aprovação.",
            "conversation_id": "c-1", "conversationId": "c-2", "thread_id": "t-1", "threadId": "t-2",
            "session_id": "s-1", "sessionId": "s-2", "chat_id": 7, "chatId": "ch-2", "context_id": "x-1",
            "id": 12345, "message_id": "m-1", "messageId": "m-2", "reply_to": "r-1",
            "topic": "Outro", "text": "nao", "media": [], "model": "x", "usage": {"id": "nested"},
        }
        self.assertEqual(
            ri.continuation_fields(previous),
            {"conversation_id": "c-1", "conversationId": "c-2", "thread_id": "t-1", "threadId": "t-2",
             "session_id": "s-1", "sessionId": "s-2", "chat_id": 7, "chatId": "ch-2", "context_id": "x-1",
             "id": 12345, "message_id": "m-1", "messageId": "m-2", "reply_to": "r-1"},
        )
        # only scalars: no nested values, lists, booleans, nulls or empty texts
        self.assertEqual(
            ri.continuation_fields({"id": {"a": 1}, "thread_id": ["t"], "session_id": None, "chat_id": True,
                                    "conversation_id": "  ", "message_id": 0}),
            {"message_id": 0},
        )
        for garbage in (None, [], "abc", 5):
            self.assertEqual(ri.continuation_fields(garbage), {})

    def test_followup_body(self):
        self.assertEqual(ri.build_followup_body("Imagens", "Confirmo."), {"topic": "Imagens", "text": "Confirmo."})
        body = ri.build_followup_body(
            "Imagens", "Confirmo.",
            {"answer": "x", "conversation_id": "c-1", "id": 9, "topic": "Outro", "text": "nao",
             "media": [{"data": _b64(PNG)}]},
        )
        self.assertEqual(body, {"topic": "Imagens", "text": "Confirmo.", "conversation_id": "c-1", "id": 9})
        self.assertNotIn("media", body)

    def test_needs_confirmation(self):
        self.assertTrue(ri.needs_confirmation({"answer": "Ficou à espera da tua aprovação."}))
        self.assertTrue(ri.needs_confirmation({"answer": "x", "media": [], "error": None}))
        for payload in ({"answer": "x", "error": "sem saldo"}, {"answer": ""}, {"answer": "  "}, {}, {"media": []},
                        {"answer": 5}, {"answer": "x", "media": [{"data": _b64(PNG)}]}, None, [], "texto"):
            self.assertFalse(ri.needs_confirmation(payload), payload)

    def test_describe_response(self):
        text = ri.describe_response(
            {"answer": "linha 1\nlinha 2 " + "x" * 500, "media": [{"data": _b64(PNG)}, {"data": _b64(JPEG)}],
             "conversation_id": "c-1"}
        )
        self.assertIn("keys=[answer, media, conversation_id]", text)
        self.assertIn("media=yes (count=2)", text)
        self.assertIn('answer="linha 1 linha 2 xxx', text)
        self.assertNotIn(_b64(PNG)[:16], text)
        self.assertNotIn("c-1", text)  # key names only, never the values
        self.assertLess(len(text), 320)
        self.assertIn('media=no (count=0) error=yes answer=""', ri.describe_response({"error": "x"}))
        self.assertIn("media=no (count=1)", ri.describe_response({"media": [{}]}))
        self.assertEqual(ri.describe_response([1]), "not a JSON object")

    def test_failure_message(self):
        messages = ri.ERROR_MESSAGES
        approval = "O serviço de imagens ficou à espera de aprovação e não devolveu a imagem."
        self.assertEqual(ri.failure_message(ri.ERR_NO_MEDIA), messages[ri.ERR_NO_MEDIA])
        self.assertEqual(ri.failure_message(ri.ERR_NO_MEDIA, 0), messages[ri.ERR_NO_MEDIA])
        self.assertEqual(ri.failure_message(ri.ERR_NO_MEDIA, 1), f"{messages[ri.ERR_NO_MEDIA]} {approval}")
        self.assertEqual(ri.failure_message(ri.ERR_SERVICE_ERROR, 2), f"{messages[ri.ERR_SERVICE_ERROR]} {approval}")
        self.assertEqual(ri.failure_message(ri.ERR_TIMEOUT, 2), messages[ri.ERR_TIMEOUT])
        self.assertEqual(ri.failure_message("???", 1), messages[ri.ERR_UNEXPECTED])

    def test_attempts_are_clamped(self):
        for stored, expected in ((None, 2), (0, 0), (1, 1), (3, 3), (9, 3), (-4, 0), ("abc", 2), ("1", 1), (2.0, 2)):
            self.assertEqual(ri.normalize_confirm_attempts(stored), expected, stored)


# ---------------------------------------------------------------------------
# Coordinator: generation flow with a fake image service
# ---------------------------------------------------------------------------

class _Content:
    def __init__(self, body: bytes):
        self._body = body

    async def iter_chunked(self, size):
        for start in range(0, len(self._body), size):
            yield self._body[start:start + size]


class _Response:
    def __init__(self, status=200, body=b"", headers=None, gate=None, error=None):
        self.status = status
        self.headers = headers or {}
        self.content = _Content(body)
        self._gate = gate
        self._error = error

    async def __aenter__(self):
        if self._gate is not None:
            await self._gate.wait()
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Fake aiohttp session: answers with the queued responses, records the requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, json=None, headers=None, **kwargs):
        self.requests.append({"url": url, "json": copy.deepcopy(json), "headers": dict(headers or {})})
        return self.responses.pop(0)


class RewardImageTestCase(tc.CoordinatorTestCase):
    OPTIONS = {
        const.CONF_REWARD_TYPE: const.REWARD_TYPE_AI_IMAGE_FROM_PHOTO,
        const.CONF_REWARD_IMAGE_API_URL: API_URL,
        const.CONF_REWARD_IMAGE_API_TOKEN: TOKEN,
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name
        folder = self.folder
        counter = {"n": 0}

        class ImageHandler:
            def __init__(self, hass, uploader=None):
                self.storage_path = folder

            async def save_photo(self, child, photo_data):
                counter["n"] += 1
                path = os.path.join(folder, f"{child}_20261113_08{counter['n']:02d}00.jpg")
                with open(path, "wb") as handle:
                    handle.write(base64.b64decode(photo_data))
                return path

        fake = types.ModuleType(f"{PACKAGE}.image_handler")
        fake.ImageHandler = ImageHandler
        patcher = mock.patch.dict(sys.modules, {f"{PACKAGE}.image_handler": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        # v2.13.2: no real waiting before the automatic retry of a transient failure
        delay = mock.patch.object(ri, "RETRY_DELAY", 0)
        delay.start()
        self.addCleanup(delay.stop)

    async def make_reward(self, *responses, **options):
        hass, entry, coordinator = await self.make(**{**self.OPTIONS, **options})
        hass.http_session = _Session(*responses)
        return hass, entry, coordinator

    async def photo(self, coordinator, child="leonor", content=PHOTO):
        await coordinator.save_photo(child, _b64(content))

    async def settle(self, hass):
        await asyncio.gather(*hass.tasks, return_exceptions=True)

    def files(self):
        return sorted(os.listdir(self.folder))


class TestRewardImageFlow(RewardImageTestCase):
    async def test_success(self):
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)))
        coordinator.data["leonor"]["activities"].append(
            {"id": "taekwondo", "name": "Taekwondo", "source": "calendar", "completed": False}
        )
        await self.photo(coordinator)

        # the photo activity is done at once; the image is still being generated
        leonor = coordinator.data["leonor"]
        self.assertTrue(leonor["activities"][0]["completed"])
        self.assertEqual(leonor["reward_image_status"], "generating")
        self.assertIsNone(leonor["reward_image"])
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_status"], "generating")

        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIsNone(leonor["reward_image_error"])
        self.assertEqual(
            leonor["reward_image"],
            "/local/morning_routine_photos/leonor_20261113_082000_reward.png?v=20261113_082000",
        )
        self.assertEqual(self.files(), ["leonor_20261113_080100.jpg", "leonor_20261113_082000_reward.png"])
        with open(os.path.join(self.folder, "leonor_20261113_082000_reward.png"), "rb") as handle:
            self.assertEqual(handle.read(), PNG)
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_status"], "ready")
        self.assertEqual(coordinator.data["duarte"].get("reward_image_status"), None)

        # the request is exactly what the service expects
        (request,) = hass.http_session.requests
        self.assertEqual(request["url"], API_URL)
        self.assertEqual(
            request["headers"], {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        )
        body = request["json"]
        self.assertEqual(set(body), {"topic", "text", "media"})
        self.assertEqual(body["topic"], "Imagens")
        self.assertEqual(body["media"], [{"mime": "image/jpeg", "data": _b64(PHOTO)}])
        self.assertIn("uma menina ver (Leonor, tem 8 anos e já sabe ler bem). ", body["text"])
        self.assertIn("Deve conter algo relacionado com Taekwondo. ", body["text"])
        self.assertNotIn("{", body["text"])
        self.assertNotIn(TOKEN, json.dumps(body))

    async def test_options_are_used(self):
        hass, _, coordinator = await self.make_reward(
            _Response(body=_ok(JPEG)),
            **{
                const.CONF_REWARD_IMAGE_API_TOPIC: "Desenhos",
                const.CONF_REWARD_IMAGE_PROMPT: "Desenha {name}, {child_noun}{notes_clause}, {weekday}. {outro}",
                "reward_image_notes_duarte": "tem 10 anos",
            },
        )
        await self.photo(coordinator, "duarte")
        await self.settle(hass)
        body = hass.http_session.requests[0]["json"]
        self.assertEqual(body["topic"], "Desenhos")
        self.assertEqual(body["text"], "Desenha Duarte, um menino, tem 10 anos, sexta-feira. {outro}")
        # a JPEG answer is stored as .jpg
        self.assertTrue(coordinator.data["duarte"]["reward_image"].startswith(
            "/local/morning_routine_photos/duarte_20261113_082000_reward.jpg?v="
        ))

        # an emptied notes field means "no notes" (the default is not used)
        hass, _, coordinator = await self.make_reward(
            _Response(body=_ok()), **{"reward_image_notes_leonor": ""}
        )
        await self.photo(coordinator)
        await self.settle(hass)
        self.assertIn("ver (Leonor). A roupa", hass.http_session.requests[0]["json"]["text"])

    async def assertFailure(self, response, message, retried=False, **options):
        # a transient failure is tried twice (v2.13.2), anything else only once
        hass, _, coordinator = await self.make_reward(response, response, **options)
        await self.photo(coordinator)
        await self.settle(hass)
        if not hass.http_session.requests:
            self.assertEqual(len(hass.http_session.responses), 2, message)  # nothing was sent at all
        else:
            self.assertEqual(len(hass.http_session.requests), 2 if retried else 1, message)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], message)
        self.assertIsNone(leonor["reward_image"])
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_status"], "failed")
        # the routine itself is untouched: photo saved, activity done, nothing else written
        self.assertTrue(leonor["photo_path"].endswith(".jpg"))
        self.assertTrue(leonor["activities"][0]["completed"])
        self.assertEqual([name for name in self.files() if "reward" in name], [])
        return hass, coordinator

    async def test_failures(self):
        messages = ri.ERROR_MESSAGES
        await self.assertFailure(_Response(status=500, body=b"boom"), messages[ri.ERR_HTTP], retried=True)
        await self.assertFailure(_Response(status=401, body=b'{"error":"unauthorized"}'), messages[ri.ERR_HTTP])
        await self.assertFailure(_Response(body=b'{"error":"sem saldo"}'), messages[ri.ERR_SERVICE_ERROR])
        # (text only answer without the v2.13.1 confirmation follow-up: see TestConfirmationFollowUp)
        await self.assertFailure(
            _Response(body=b'{"answer":"Nao posso."}'), messages[ri.ERR_NO_MEDIA],
            **{const.CONF_REWARD_IMAGE_CONFIRM_ATTEMPTS: 0},
        )
        await self.assertFailure(_Response(body=b'{"media":[]}'), messages[ri.ERR_NO_MEDIA])
        await self.assertFailure(_Response(body=b'{"media":[{"data":"%%%"}]}'), messages[ri.ERR_BAD_BASE64])
        await self.assertFailure(
            _Response(body=json.dumps({"media": [{"data": _b64(b"GIF89a-not-allowed")}]}).encode()),
            messages[ri.ERR_NOT_AN_IMAGE],
        )
        await self.assertFailure(_Response(body=b"<html>"), messages[ri.ERR_INVALID_JSON])
        await self.assertFailure(
            _Response(error=OSError("connection refused")), messages[ri.ERR_CONNECTION], retried=True
        )
        for message in messages.values():
            self.assertNotIn(TOKEN, message)

    async def test_oversize(self):
        with mock.patch.object(ri, "MAX_RESPONSE_BYTES", 1000):
            # announced by the server
            await self.assertFailure(
                _Response(body=_ok(), headers={"Content-Length": "1001"}), ri.ERROR_MESSAGES[ri.ERR_TOO_LARGE]
            )
            # not announced: stopped while reading
            await self.assertFailure(_Response(body=b" " * 5000), ri.ERROR_MESSAGES[ri.ERR_TOO_LARGE])

    async def test_timeout(self):
        gate = asyncio.Event()  # never set: the service never answers
        hass, _, coordinator = await self.make_reward(
            _Response(body=_ok(), gate=gate), _Response(body=_ok(), gate=gate)
        )
        with mock.patch.object(coordinator, "_reward_image_timeout", return_value=0.01):
            await self.photo(coordinator)
            await self.settle(hass)
        self.assertEqual(len(hass.http_session.requests), 2)  # a timeout is retried once
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "failed")
        self.assertEqual(coordinator.data["leonor"]["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_TIMEOUT])

    async def test_timeout_option_is_clamped(self):
        for stored, expected in ((None, 180), (5, 30), (45, 45), (9999, 600), ("abc", 180), ("60", 60)):
            options = {} if stored is None else {const.CONF_REWARD_IMAGE_TIMEOUT: stored}
            _, _, coordinator = await self.make_reward(**options)
            self.assertEqual(coordinator._reward_image_timeout(), expected, stored)

    async def test_unreadable_photo(self):
        hass, _, coordinator = await self.make_reward()
        coordinator._start_reward_image("leonor", os.path.join(self.folder, "missing.jpg"))
        await self.settle(hass)
        self.assertEqual(coordinator.data["leonor"]["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_PHOTO_UNREADABLE])
        self.assertEqual(hass.http_session.requests, [])

    async def test_without_token_nothing_is_sent(self):
        hass, _, coordinator = await self.make_reward(**{const.CONF_REWARD_IMAGE_API_TOKEN: ""})
        await self.photo(coordinator)
        await self.settle(hass)
        self.assertEqual(hass.http_session.requests, [])
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_NO_TOKEN])
        self.assertTrue(leonor["activities"][0]["completed"])

    async def test_invalid_url_nothing_is_sent(self):
        hass, coordinator = await self.assertFailure(
            _Response(body=_ok()), ri.ERROR_MESSAGES[ri.ERR_CONNECTION],
            **{const.CONF_REWARD_IMAGE_API_URL: "file:///etc/passwd"},
        )
        self.assertEqual(hass.http_session.requests, [])

    async def test_other_reward_types_never_call_the_service(self):
        for reward_type in ("quote", "youtube_video", "ai_image"):
            hass, _, coordinator = await self.make_reward(**{const.CONF_REWARD_TYPE: reward_type})
            await self.photo(coordinator)
            await self.settle(hass)
            self.assertEqual(hass.http_session.requests, [], reward_type)
            self.assertIsNone(coordinator.data["leonor"].get("reward_image_status"), reward_type)
            self.assertIsNone(coordinator.data["leonor"].get("reward_image"), reward_type)

    async def test_token_and_image_never_logged(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)

        responses = [
            _Response(body=_ok(PNG)),
            _Response(status=403, body=f"token {TOKEN} invalid".encode()),
            _Response(body=json.dumps({"error": f"Bearer {TOKEN} recusado"}).encode()),
            _Response(body=json.dumps({"answer": f"O token {TOKEN} expirou"}).encode()),
            _Response(error=RuntimeError(f"Cannot connect, Authorization: Bearer {TOKEN}")),
            _Response(body=b"{nope"),
        ]
        with self.assertLogs(level="DEBUG") as captured:
            for response in responses:
                hass, _, coordinator = await self.make_reward(response, response)
                await self.photo(coordinator)
                await self.settle(hass)
            # no token configured, and the regenerate service
            hass, _, coordinator = await self.make_reward(_Response(body=_ok()))
            await self.photo(coordinator)
            await self.settle(hass)
            hass.http_session.responses[:] = [_Response(status=502), _Response(status=502)]
            await coordinator.regenerate_reward("leonor")
            await self.settle(hass)

        output = "\n".join(captured.output)
        self.assertIn("retrying after transient error (HTTP 502)", output)
        self.assertIn("retrying after transient error (RuntimeError)", output)
        self.assertIn("Reward image for leonor failed: http_error (HTTP 403)", output)
        self.assertIn("Reward image for leonor failed: service_error (Bearer *** recusado)", output)
        self.assertIn("Reward image for leonor failed: connection_error (RuntimeError)", output)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("Authorization", output)
        self.assertNotIn(_b64(PNG)[:24], output)
        self.assertNotIn(_b64(PHOTO)[:24], output)
        # nor in what is stored / shown in the sensor
        self.assertNotIn(TOKEN, json.dumps(coordinator.store.saved))
        self.assertNotIn(TOKEN, json.dumps(coordinator.data))

    async def test_new_photo_cancels_and_replaces(self):
        gate = asyncio.Event()  # the first request never gets an answer
        hass, _, coordinator = await self.make_reward(
            _Response(body=_ok(PNG), gate=gate), _Response(body=_ok(JPEG))
        )
        await self.photo(coordinator, content=PHOTO)
        await asyncio.sleep(0)
        first = coordinator._reward_image_tasks["leonor"]
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "generating")

        second_photo = PHOTO + b"-second"
        await self.photo(coordinator, content=second_photo)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "generating")
        self.assertIsNot(coordinator._reward_image_tasks["leonor"], first)
        await self.settle(hass)

        self.assertTrue(first.cancelled())
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIn("_reward.jpg?v=", leonor["reward_image"])
        requests = hass.http_session.requests
        self.assertEqual(len(requests), 2)
        self.assertEqual(base64.b64decode(requests[1]["json"]["media"][0]["data"]), second_photo)
        self.assertEqual(coordinator._reward_image_tasks, {})

    async def test_new_photo_replaces_a_finished_image(self):
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)), _Response(body=_ok(JPEG)))
        await self.photo(coordinator)
        await self.settle(hass)
        self.assertIn("leonor_20261113_082000_reward.png", self.files())

        await self.photo(coordinator)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "generating")
        self.assertIsNone(coordinator.data["leonor"]["reward_image"])  # the old image is not shown meanwhile
        await self.settle(hass)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "ready")
        self.assertEqual(
            self.files(),
            ["leonor_20261113_080100.jpg", "leonor_20261113_080200.jpg", "leonor_20261113_082000_reward.jpg"],
        )

    async def test_a_late_failure_of_a_replaced_generation_changes_nothing(self):
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)))
        await self.photo(coordinator)
        await self.settle(hass)
        stale = asyncio.ensure_future(coordinator._generate_reward_image("leonor", "/nonexistent.jpg"))
        await stale  # not the registered task: must not touch the state
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "ready")

    async def test_reset_clears_and_cancels(self):
        gate = asyncio.Event()
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG), gate=gate), _Response(body=_ok(PNG)))
        await self.photo(coordinator, "duarte")
        await asyncio.sleep(0)
        task = coordinator._reward_image_tasks["duarte"]

        await coordinator.reset_routine(None)
        await self.settle(hass)
        self.assertTrue(task.cancelled())
        for child in const.CHILDREN:
            for key in ("reward_image", "reward_image_status", "reward_image_error", "reward_image_path",
                        "reward_image_last_attempt", "photo_path"):
                self.assertIsNone(coordinator.data[child][key], (child, key))
        self.assertIsNone(coordinator.store.saved["duarte"]["reward_image_status"])

        # a finished image is cleared as well
        await self.photo(coordinator, "duarte")
        await self.settle(hass)
        self.assertEqual(coordinator.data["duarte"]["reward_image_status"], "ready")
        self.assertEqual(coordinator.data["duarte"]["reward_image_last_attempt"]["result"], "ok")
        await coordinator.reset_routine("duarte")
        self.assertIsNone(coordinator.data["duarte"]["reward_image"])
        self.assertIsNone(coordinator.data["duarte"]["reward_image_status"])
        self.assertIsNone(coordinator.data["duarte"]["reward_image_last_attempt"])

    async def test_shutdown_cancels(self):
        gate = asyncio.Event()
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG), gate=gate))
        await self.photo(coordinator)
        await asyncio.sleep(0)
        task = coordinator._reward_image_tasks["leonor"]
        await coordinator.async_shutdown()
        await self.settle(hass)
        self.assertTrue(task.cancelled())

    async def test_restart_while_generating_becomes_failed(self):
        hass, _, coordinator = await self.make_reward()
        stored = copy.deepcopy(coordinator.data)
        stored["leonor"]["reward_image_status"] = "generating"
        coordinator.store.saved = stored
        await coordinator._load_data()
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "failed")
        self.assertEqual(coordinator.data["leonor"]["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_INTERRUPTED])

    async def test_state_stored_by_an_older_version(self):
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)))
        stored = copy.deepcopy(coordinator.data)
        for child in const.CHILDREN:
            for key in ("reward_image_status", "reward_image_error", "reward_image_path",
                        "streak", "best_streak", "streak_last_date"):
                stored[child].pop(key, None)
        coordinator.store.saved = stored
        await coordinator._load_data()
        await self.photo(coordinator)
        await self.settle(hass)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "ready")
        await coordinator.reset_routine(None)
        self.assertEqual(st.current(coordinator.data["leonor"]), (0, 0))

    async def test_regenerate_service(self):
        errors = sys.modules["homeassistant.exceptions"].ServiceValidationError
        hass, _, coordinator = await self.make_reward(_Response(status=401), _Response(body=_ok(PNG)))

        # no photo yet: a clear error, nothing sent
        with self.assertRaises(errors) as caught:
            await coordinator.regenerate_reward("leonor")
        self.assertIn("fotografia", str(caught.exception))
        with self.assertRaises(errors):
            await coordinator.regenerate_reward("maria")
        self.assertEqual(hass.http_session.requests, [])

        await self.photo(coordinator)
        await self.settle(hass)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "failed")

        # run again from today's photo
        await coordinator.regenerate_reward("leonor")
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "generating")
        self.assertIsNone(coordinator.data["leonor"]["reward_image_error"])
        await self.settle(hass)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "ready")
        self.assertEqual(len(hass.http_session.requests), 2)

        # token removed meanwhile
        hass, _, coordinator = await self.make_reward(**{const.CONF_REWARD_IMAGE_API_TOKEN: ""})
        coordinator.data["leonor"]["photo_path"] = "/tmp/x.jpg"
        with self.assertRaises(errors) as caught:
            await coordinator.regenerate_reward("leonor")
        self.assertIn("token", str(caught.exception))

    async def test_completion_event_and_no_second_generation(self):
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)))
        await self.photo(coordinator, "duarte")
        await self.settle(hass)
        for activity in coordinator.data["duarte"]["activities"]:
            await coordinator.complete_activity("duarte", activity["id"])
        await self.settle(hass)
        self.assertEqual(len(hass.http_session.requests), 1)
        events = [data for name, data in hass.bus.events if name == const.EVENT_ROUTINE_COMPLETE]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reward_type"], "ai_image_from_photo")
        self.assertEqual(events[0]["reward_image_status"], "ready")
        self.assertIn("duarte_20261113_082000_reward.png", events[0]["reward_image"])

    async def test_reward_type_change_reaches_the_sensors(self):
        hass, entry, coordinator = await self.make()
        self.assertEqual(coordinator.reward_type, "quote")
        updates = coordinator.updates
        entry.options = {**entry.options, const.CONF_REWARD_TYPE: const.REWARD_TYPE_AI_IMAGE_FROM_PHOTO}
        await coordinator.async_options_updated()
        self.assertEqual(coordinator.reward_type, "ai_image_from_photo")
        self.assertEqual(coordinator.updates, updates + 1)
        await coordinator.async_options_updated()
        self.assertEqual(coordinator.updates, updates + 1)

    async def test_google_drive_copy(self):
        uploads = []

        class Uploader:
            is_enabled = True

            async def upload_file(self, filepath, filename=None, mime_type=None, child=None):
                uploads.append((os.path.basename(filepath), child))
                return "file-id"

        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)), _Response(status=500))
        coordinator.google_drive_uploader = Uploader()
        await self.photo(coordinator)
        await self.settle(hass)
        self.assertEqual(uploads, [("leonor_20261113_082000_reward.png", "leonor")])
        await self.photo(coordinator)  # failed generation: nothing to upload
        await self.settle(hass)
        self.assertEqual(len(uploads), 1)


WAITING = "A banda desenhada da Leonor ainda não foi gerada. Ficou à espera da tua aprovação."
APPROVAL = "O serviço de imagens ficou à espera de aprovação e não devolveu a imagem."
CONFIRM = "Confirmo. Gera já a imagem, sem pedir mais confirmações."


def _text(answer: str = WAITING, **extra) -> _Response:
    return _Response(body=json.dumps({"answer": answer, **extra}).encode())


class TestConfirmationFollowUp(RewardImageTestCase):
    """v2.13.1: a text only answer is followed by the hardcoded confirmation."""

    async def run_flow(self, *responses, **options):
        hass, _, coordinator = await self.make_reward(*responses, **options)
        await self.photo(coordinator)
        await self.settle(hass)
        return hass, coordinator, coordinator.data["leonor"]

    async def test_success_on_the_first_follow_up_and_body_shape(self):
        gate = asyncio.Event()
        hass, _, coordinator = await self.make_reward(
            _text(conversation_id="conv-42", id=7, model="x", usage={"id": "nested"}),
            _Response(body=_ok(PNG), gate=gate),
        )
        await self.photo(coordinator)
        for _ in range(50):
            if len(hass.http_session.requests) == 2:
                break
            await asyncio.sleep(0)
        # the follow-up is running: still "generating", nothing failed in between
        self.assertEqual(len(hass.http_session.requests), 2)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "generating")
        self.assertIsNone(coordinator.data["leonor"]["reward_image_error"])
        gate.set()
        await self.settle(hass)

        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIsNone(leonor["reward_image_error"])
        self.assertIn("_reward.png?v=", leonor["reward_image"])

        first, second = hass.http_session.requests
        self.assertEqual(base64.b64decode(first["json"]["media"][0]["data"]), PHOTO)
        # same URL, same token, same topic; the confirmation text; echoed ids; no photo
        self.assertEqual(second["url"], API_URL)
        self.assertEqual(second["headers"], first["headers"])
        self.assertEqual(
            second["json"], {"topic": "Imagens", "text": CONFIRM, "conversation_id": "conv-42", "id": 7}
        )
        self.assertNotIn("media", second["json"])

    async def test_without_identifiers_only_topic_and_text(self):
        hass, _, leonor = await self.run_flow(
            _text(), _Response(body=_ok(JPEG)),
            **{const.CONF_REWARD_IMAGE_API_TOPIC: "Desenhos", const.CONF_REWARD_IMAGE_CONFIRM_TEXT: " confirmo "},
        )
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(hass.http_session.requests[1]["json"], {"topic": "Desenhos", "text": "confirmo"})

    async def test_success_on_the_second_follow_up(self):
        hass, _, leonor = await self.run_flow(
            _text(thread_id="t-1"), _text("Queres mesmo?", thread_id="t-2"), _Response(body=_ok(PNG))
        )
        self.assertEqual(leonor["reward_image_status"], "ready")
        requests = hass.http_session.requests
        self.assertEqual(len(requests), 3)
        # each follow-up echoes the identifiers of the answer just before it
        self.assertEqual(requests[1]["json"], {"topic": "Imagens", "text": CONFIRM, "thread_id": "t-1"})
        self.assertEqual(requests[2]["json"], {"topic": "Imagens", "text": CONFIRM, "thread_id": "t-2"})

    async def test_exhausted_attempts(self):
        hass, coordinator, leonor = await self.run_flow(_text(), _text(), _text(), _Response(body=_ok(PNG)))
        self.assertEqual(len(hass.http_session.requests), 3)  # default: 2 follow-ups, the image is never asked a 4th time
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_NO_MEDIA]} {APPROVAL}")
        self.assertIsNone(leonor["reward_image"])
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_status"], "failed")
        self.assertEqual([name for name in self.files() if "reward" in name], [])
        self.assertEqual(coordinator._reward_image_tasks, {})

    async def test_attempts_option(self):
        for attempts, expected_requests in ((1, 2), (3, 4), (99, 4)):
            hass, _, leonor = await self.run_flow(
                *[_text() for _ in range(5)], **{const.CONF_REWARD_IMAGE_CONFIRM_ATTEMPTS: attempts}
            )
            self.assertEqual(len(hass.http_session.requests), expected_requests, attempts)
            self.assertEqual(leonor["reward_image_status"], "failed")

    async def test_zero_attempts_keeps_the_old_behaviour(self):
        hass, _, leonor = await self.run_flow(
            _text(), _Response(body=_ok(PNG)), **{const.CONF_REWARD_IMAGE_CONFIRM_ATTEMPTS: 0}
        )
        self.assertEqual(len(hass.http_session.requests), 1)
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_NO_MEDIA])

    async def test_only_a_text_only_success_triggers_it(self):
        spare = lambda: _Response(body=_ok(PNG))  # noqa: E731  (must never be requested)
        cases = (
            (_Response(body=_ok(PNG)), "ready", None),
            (_Response(body=b'{"error":"sem saldo"}'), "failed", ri.ERR_SERVICE_ERROR),
            (_Response(body=json.dumps({"error": "sem saldo", "answer": WAITING}).encode()), "failed", ri.ERR_SERVICE_ERROR),
            (_Response(body=b'{"media":[]}'), "failed", ri.ERR_NO_MEDIA),
            (_Response(body=b'{"answer":""}'), "failed", ri.ERR_NO_MEDIA),
            (_Response(body=b"<html>"), "failed", ri.ERR_INVALID_JSON),
            (_Response(status=401, body=json.dumps({"answer": WAITING}).encode()), "failed", ri.ERR_HTTP),
            (_Response(body=b'{"media":[{"data":"%%%"}],"answer":"aqui"}'), "failed", ri.ERR_BAD_BASE64),
        )
        for response, status, code in cases:
            hass, _, leonor = await self.run_flow(response, spare())
            self.assertEqual(len(hass.http_session.requests), 1, code)
            self.assertEqual(leonor["reward_image_status"], status, code)
            # no follow-up was sent: the message is the plain one
            self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[code] if code else None)

    async def test_error_in_a_follow_up_stops_immediately(self):
        hass, _, leonor = await self.run_flow(
            _text(), _Response(body=b'{"error":"pedido recusado"}'), _Response(body=_ok(PNG))
        )
        self.assertEqual(len(hass.http_session.requests), 2)
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_SERVICE_ERROR]} {APPROVAL}")

    async def test_other_failures_in_a_follow_up(self):
        hass, _, leonor = await self.run_flow(_text(), _Response(status=404), _Response(body=_ok(PNG)))
        self.assertEqual(len(hass.http_session.requests), 2)
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_HTTP])

        # a transient failure of the follow-up is retried once (v2.13.2), then it fails
        hass, _, leonor = await self.run_flow(
            _text(), _Response(status=502), _Response(status=502), _Response(body=_ok(PNG))
        )
        self.assertEqual(len(hass.http_session.requests), 3)
        self.assertNotIn("media", hass.http_session.requests[2]["json"])  # the follow-up again, not the photo
        self.assertEqual(hass.http_session.requests[2]["json"], hass.http_session.requests[1]["json"])
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_HTTP])

        # each follow-up has its own timeout budget; a silent service still ends in "failed"
        gate = asyncio.Event()
        hass, _, coordinator = await self.make_reward(
            _text(), _Response(body=_ok(PNG), gate=gate), _Response(body=_ok(PNG), gate=gate)
        )
        with mock.patch.object(coordinator, "_reward_image_timeout", return_value=0.01):
            await self.photo(coordinator)
            await self.settle(hass)
        self.assertEqual(len(hass.http_session.requests), 3)  # first request, follow-up, its retry
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "failed")
        self.assertEqual(coordinator.data["leonor"]["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_TIMEOUT])

    async def test_cancelled_during_a_follow_up(self):
        gate = asyncio.Event()  # the follow-up never gets an answer
        hass, _, coordinator = await self.make_reward(
            _text(), _Response(body=_ok(PNG), gate=gate), _Response(body=_ok(JPEG))
        )
        await self.photo(coordinator)
        for _ in range(50):
            if len(hass.http_session.requests) == 2:
                break
            await asyncio.sleep(0)
        self.assertEqual(len(hass.http_session.requests), 2)
        first = coordinator._reward_image_tasks["leonor"]

        # a new photo replaces the generation that is waiting for the follow-up
        await self.photo(coordinator, content=PHOTO + b"-second")
        await self.settle(hass)
        self.assertTrue(first.cancelled())
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIn("_reward.jpg?v=", leonor["reward_image"])
        self.assertEqual(len(hass.http_session.requests), 3)
        self.assertIn("media", hass.http_session.requests[2]["json"])
        self.assertEqual(coordinator._reward_image_tasks, {})

    async def test_cancel_during_a_follow_up_leaves_nothing_behind(self):
        gate = asyncio.Event()
        hass, _, coordinator = await self.make_reward(_text(), _Response(body=_ok(PNG), gate=gate))
        await self.photo(coordinator)
        for _ in range(50):
            if len(hass.http_session.requests) == 2:
                break
            await asyncio.sleep(0)
        task = coordinator._reward_image_tasks["leonor"]
        coordinator._cancel_reward_image_task("leonor")
        await self.settle(hass)
        self.assertTrue(task.cancelled())
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "generating")  # owned by whoever cancelled
        self.assertEqual([name for name in self.files() if "reward" in name], [])

    async def test_diagnostics_without_token_or_media(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)

        with self.assertLogs(level="DEBUG") as captured:
            await self.run_flow(
                _text(f"Fica à espera (token {TOKEN}). " + "z" * 400, conversation_id="conv-SECRET-ID"),
                _Response(body=json.dumps(
                    {"answer": "Aqui está!", "media": [{"mime": "image/png", "data": _b64(PNG)}], "usage": {}}
                ).encode()),
            )
            await self.run_flow(_text(), _text(), _text())
            await self.run_flow(_text(), _Response(status=503), _Response(status=503))
        output = "\n".join(captured.output)
        infos = "\n".join(line for line in captured.output if line.startswith("INFO"))

        # every answer: HTTP status, key names, media presence / count, start of the text
        self.assertIn("Reward image service answered the request for leonor: HTTP 200", infos)
        self.assertIn("answered the confirmation follow-up 1/2 for leonor: HTTP 200", infos)
        self.assertIn("answered the confirmation follow-up 1/2 for leonor: HTTP 503", infos)
        self.assertIn(
            "confirmation follow-up 1/2 for leonor: retrying after transient error (HTTP 503)", infos
        )
        self.assertIn("answered the confirmation follow-up 1/2 for leonor (retry): HTTP 503", infos)
        self.assertIn("keys=[answer, conversation_id] media=no (count=0) error=no "
                      'answer="Fica à espera (token ***). zzz', infos)
        self.assertIn('keys=[answer, media, usage] media=yes (count=1) error=no answer="Aqui está!"', infos)
        self.assertIn("text only answer, sending confirmation follow-up 2/2 for leonor", infos)
        self.assertNotIn("z" * 201, output)
        # the final failure says how many follow-ups were tried
        warnings_ = [line for line in captured.output if line.startswith("WARNING")]
        self.assertEqual(len(warnings_), 2)
        self.assertIn("Reward image for leonor failed: no_media (text only: ", warnings_[0])
        self.assertTrue(warnings_[0].endswith("confirmation follow-ups tried: 2"))
        self.assertIn("failed: http_error (HTTP 503) - confirmation follow-ups tried: 1", warnings_[1])
        # never the token, the media or the values of the echoed identifiers
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("Authorization", output)
        self.assertNotIn(_b64(PNG)[:24], output)
        self.assertNotIn(_b64(PHOTO)[:24], output)
        self.assertNotIn("conv-SECRET-ID", output)


# ---------------------------------------------------------------------------
# v2.13.2: the last good image is kept, transient errors are retried once
# ---------------------------------------------------------------------------

KEPT = "Mantém-se a imagem anterior."


class TestTransientLogic(unittest.TestCase):
    def test_is_transient(self):
        for status in (429, 500, 502, 503, 504):
            self.assertTrue(ri.is_transient(ri.RewardImageError(ri.ERR_HTTP, f"HTTP {status}", status=status)))
        for status in (400, 401, 403, 404, 422, 501, None):
            self.assertFalse(ri.is_transient(ri.RewardImageError(ri.ERR_HTTP, status=status)), status)
        self.assertTrue(ri.is_transient(ri.RewardImageError(ri.ERR_TIMEOUT)))
        self.assertTrue(ri.is_transient(ri.RewardImageError(ri.ERR_CONNECTION, "OSError", transient=True)))
        # an invalid URL is not worth a retry, nor is anything wrong with a 200 answer
        self.assertFalse(ri.is_transient(ri.RewardImageError(ri.ERR_CONNECTION, "invalid service URL")))
        for code in (ri.ERR_SERVICE_ERROR, ri.ERR_NO_MEDIA, ri.ERR_INVALID_JSON, ri.ERR_TOO_LARGE, ri.ERR_BAD_BASE64):
            self.assertFalse(ri.is_transient(ri.RewardImageError(code)), code)
        self.assertFalse(ri.is_transient(ValueError("x")))

    def test_constants(self):
        self.assertEqual(ri.RETRY_DELAY, 10)
        self.assertEqual(ri.KEPT_PREVIOUS_MESSAGE, KEPT)
        self.assertEqual(ri.last_attempt("failed", "2026-11-13T08:20:00+00:00"),
                         {"result": "failed", "at": "2026-11-13T08:20:00+00:00"})


class TestKeepLastGoodImage(RewardImageTestCase):
    async def ready(self, *later, **options):
        """A coordinator whose first generation succeeded; ``later`` are the next answers."""
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(PNG)), *later, **options)
        await self.photo(coordinator)
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        return hass, coordinator, dict(leonor)

    async def test_regeneration_failure_keeps_the_previous_image(self):
        hass, coordinator, before = await self.ready(_Response(status=503), _Response(status=503))

        await coordinator.regenerate_reward("leonor")
        leonor = coordinator.data["leonor"]
        # while generating the previous image is still there
        self.assertEqual(leonor["reward_image_status"], "generating")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertIsNone(leonor["reward_image_error"])
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image"], before["reward_image"])
        await self.settle(hass)

        leonor = coordinator.data["leonor"]
        self.assertEqual(len(hass.http_session.requests), 3)  # first image, 503, one retry
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertEqual(leonor["reward_image_path"], before["reward_image_path"])
        self.assertEqual(leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_HTTP]} {KEPT}")
        self.assertEqual(leonor["reward_image_last_attempt"]["result"], "failed")
        self.assertTrue(os.path.isfile(before["reward_image_path"]))  # the file is still on disk
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_status"], "ready")
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image"], before["reward_image"])
        self.assertEqual(coordinator._reward_image_tasks, {})

    async def test_other_failures_keep_it_too(self):
        # an "error" answer, a text only answer after the follow-ups, an unreadable photo
        hass, coordinator, before = await self.ready(_Response(body=b'{"error":"sem saldo"}'))
        await coordinator.regenerate_reward("leonor")
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(len(hass.http_session.requests), 2)  # not retried
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertEqual(leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_SERVICE_ERROR]} {KEPT}")

        text = lambda: _Response(body=json.dumps({"answer": "Fico a aguardar."}).encode())  # noqa: E731
        hass, coordinator, before = await self.ready(text(), text(), text())
        await coordinator.regenerate_reward("leonor")
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertEqual(
            leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_NO_MEDIA]} {ri.APPROVAL_MESSAGE} {KEPT}"
        )

        hass, coordinator, before = await self.ready()
        os.remove(coordinator.data["leonor"]["photo_path"])
        await coordinator.regenerate_reward("leonor")
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertEqual(leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_PHOTO_UNREADABLE]} {KEPT}")

    async def test_failure_without_a_previous_image(self):
        hass, _, coordinator = await self.make_reward(_Response(status=503), _Response(status=503))
        await self.photo(coordinator)
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(len(hass.http_session.requests), 2)
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertIsNone(leonor["reward_image"])
        self.assertIsNone(leonor["reward_image_path"])
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_HTTP])
        self.assertEqual(leonor["reward_image_last_attempt"]["result"], "failed")

        # regenerating after that failure: still nothing to keep
        hass.http_session.responses[:] = [_Response(status=400)]
        await coordinator.regenerate_reward("leonor")
        self.assertIsNone(coordinator.data["leonor"]["reward_image"])
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertIsNone(leonor["reward_image"])
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_HTTP])

    async def test_new_photo_clears_the_previous_image(self):
        hass, coordinator, before = await self.ready(_Response(status=503), _Response(status=503))
        await self.photo(coordinator, content=PHOTO + b"-second")
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "generating")
        self.assertIsNone(leonor["reward_image"])  # it no longer matches the photo
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertIsNone(leonor["reward_image"])
        self.assertIsNone(leonor["reward_image_path"])
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_HTTP])  # no "mantém-se"

    async def test_new_photo_without_token_clears_the_previous_image(self):
        hass, coordinator, before = await self.ready()
        with mock.patch.object(coordinator, "_reward_image_token", return_value=""):
            await self.photo(coordinator, content=PHOTO + b"-second")
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertIsNone(leonor["reward_image"])
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_NO_TOKEN])

    async def test_successful_regeneration_replaces_the_image(self):
        hass, coordinator, before = await self.ready(_Response(body=_ok(JPEG)))
        await coordinator.regenerate_reward("leonor")
        self.assertEqual(coordinator.data["leonor"]["reward_image"], before["reward_image"])
        await self.settle(hass)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIn("_reward.jpg?v=", leonor["reward_image"])
        self.assertIsNone(leonor["reward_image_error"])
        self.assertEqual(leonor["reward_image_last_attempt"]["result"], "ok")
        self.assertEqual([name for name in self.files() if "reward" in name], ["leonor_20261113_082000_reward.jpg"])

    async def test_regeneration_cancelled_by_a_second_one_keeps_the_image(self):
        gate = asyncio.Event()
        hass, coordinator, before = await self.ready(
            _Response(body=_ok(JPEG), gate=gate), _Response(status=400)
        )
        await coordinator.regenerate_reward("leonor")
        await asyncio.sleep(0)
        first = coordinator._reward_image_tasks["leonor"]
        await coordinator.regenerate_reward("leonor")
        await self.settle(hass)
        self.assertTrue(first.cancelled())
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertTrue(leonor["reward_image_error"].endswith(KEPT))
        self.assertTrue(os.path.isfile(before["reward_image_path"]))

    async def test_restart_while_regenerating_keeps_the_image(self):
        hass, coordinator, before = await self.ready()
        stored = copy.deepcopy(coordinator.data)
        stored["leonor"]["reward_image_status"] = "generating"
        coordinator.store.saved = stored
        await coordinator._load_data()
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertEqual(leonor["reward_image"], before["reward_image"])
        self.assertEqual(leonor["reward_image_error"], f"{ri.ERROR_MESSAGES[ri.ERR_INTERRUPTED]} {KEPT}")

    async def test_last_attempt_attribute(self):
        hass, coordinator, before = await self.ready(_Response(status=400))
        now = sys.modules["homeassistant.util.dt"].utcnow().isoformat()
        self.assertEqual(before["reward_image_last_attempt"], {"result": "ok", "at": now})
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_last_attempt"]["result"], "ok")
        self.assertIsNone(coordinator.data["duarte"]["reward_image_last_attempt"])

        await coordinator.regenerate_reward("leonor")
        # unchanged until the new attempt ends
        self.assertEqual(coordinator.data["leonor"]["reward_image_last_attempt"]["result"], "ok")
        await self.settle(hass)
        self.assertEqual(coordinator.data["leonor"]["reward_image_last_attempt"], {"result": "failed", "at": now})
        self.assertEqual(coordinator.store.saved["leonor"]["reward_image_last_attempt"]["result"], "failed")

        # exposed by the sensor, next to the existing attributes
        with open(os.path.join(tc.PACKAGE_DIR, "sensor.py"), encoding="utf-8") as handle:
            source = handle.read()
        for attribute in ("reward_image", "reward_image_status", "reward_image_error", "reward_image_last_attempt"):
            self.assertIn(f'"{attribute}": child_data.get("{attribute}")', source)


class TestTransientRetry(RewardImageTestCase):
    async def run_flow(self, *responses, **options):
        hass, _, coordinator = await self.make_reward(*responses, **options)
        await self.photo(coordinator)
        await self.settle(hass)
        return hass, coordinator, coordinator.data["leonor"]

    async def test_503_then_200_succeeds_with_one_retry(self):
        hass, _, leonor = await self.run_flow(_Response(status=503), _Response(body=_ok(PNG)))
        requests = hass.http_session.requests
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]["json"], requests[0]["json"])  # the same request, photo included
        self.assertEqual(requests[1]["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIsNone(leonor["reward_image_error"])
        self.assertIn("_reward.png?v=", leonor["reward_image"])
        self.assertEqual(leonor["reward_image_last_attempt"]["result"], "ok")

    async def test_every_transient_failure_is_retried_once(self):
        gate = asyncio.Event()
        for first in (
            _Response(status=429), _Response(status=500), _Response(status=502), _Response(status=504),
            _Response(error=OSError("connection reset")),
        ):
            hass, _, leonor = await self.run_flow(first, _Response(body=_ok(PNG)))
            self.assertEqual(len(hass.http_session.requests), 2)
            self.assertEqual(leonor["reward_image_status"], "ready")
        hass, _, coordinator = await self.make_reward(_Response(body=_ok(), gate=gate), _Response(body=_ok(PNG)))
        with mock.patch.object(coordinator, "_reward_image_timeout", return_value=0.01):
            await self.photo(coordinator)
            await self.settle(hass)
        self.assertEqual(len(hass.http_session.requests), 2)
        self.assertEqual(coordinator.data["leonor"]["reward_image_status"], "ready")

    async def test_503_twice_fails(self):
        hass, _, leonor = await self.run_flow(
            _Response(status=503), _Response(status=503), _Response(body=_ok(PNG))
        )
        self.assertEqual(len(hass.http_session.requests), 2)  # one retry only
        self.assertEqual(leonor["reward_image_status"], "failed")
        self.assertIsNone(leonor["reward_image"])
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_HTTP])

    async def test_400_and_error_answers_are_not_retried(self):
        for response in (
            _Response(status=400), _Response(status=401), _Response(status=403), _Response(status=404),
            _Response(body=b'{"error":"sem saldo"}'), _Response(body=b"<html>"),
        ):
            hass, _, leonor = await self.run_flow(response, _Response(body=_ok(PNG)))
            self.assertEqual(len(hass.http_session.requests), 1)
            self.assertEqual(leonor["reward_image_status"], "failed")
        # an invalid URL is not retried either (nothing is sent at all)
        with mock.patch("asyncio.sleep", side_effect=AssertionError("no retry expected")):
            hass, _, leonor = await self.run_flow(**{const.CONF_REWARD_IMAGE_API_URL: "ftp://x"})
        self.assertEqual(hass.http_session.requests, [])
        self.assertEqual(leonor["reward_image_error"], ri.ERROR_MESSAGES[ri.ERR_CONNECTION])

    async def test_follow_up_is_retried_too(self):
        text = _Response(body=json.dumps({"answer": "Fico a aguardar.", "conversation_id": "c1"}).encode())
        hass, _, leonor = await self.run_flow(text, _Response(status=503), _Response(body=_ok(PNG)))
        requests = hass.http_session.requests
        self.assertEqual(len(requests), 3)
        self.assertIn("media", requests[0]["json"])
        self.assertNotIn("media", requests[1]["json"])
        self.assertEqual(requests[2]["json"], requests[1]["json"])
        self.assertEqual(leonor["reward_image_status"], "ready")

    async def test_the_delay_is_the_module_constant(self):
        delays = []
        real_sleep = asyncio.sleep

        async def fake_sleep(delay, *args, **kwargs):
            delays.append(delay)
            await real_sleep(0)

        with mock.patch.object(ri, "RETRY_DELAY", 7.5), mock.patch("asyncio.sleep", fake_sleep):
            hass, _, leonor = await self.run_flow(_Response(status=503), _Response(body=_ok(PNG)))
        self.assertEqual(delays, [7.5])
        self.assertEqual(leonor["reward_image_status"], "ready")

    async def test_cancelled_during_the_retry_delay(self):
        with mock.patch.object(ri, "RETRY_DELAY", 3600):
            hass, coordinator, before = await TestKeepLastGoodImage.ready(
                self, _Response(status=503), _Response(body=_ok(JPEG))
            )
            await coordinator.regenerate_reward("leonor")
            for _ in range(50):
                if len(hass.http_session.requests) == 2:
                    break
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(len(hass.http_session.requests), 2)  # the 503 was received: now waiting
            task = coordinator._reward_image_tasks["leonor"]
            self.assertFalse(task.done())

            # a new photo cancels the wait at once and starts over
            await self.photo(coordinator, content=PHOTO + b"-second")
            await self.settle(hass)
        self.assertTrue(task.cancelled())
        self.assertEqual(len(hass.http_session.requests), 3)
        leonor = coordinator.data["leonor"]
        self.assertEqual(leonor["reward_image_status"], "ready")
        self.assertIn("_reward.jpg?v=", leonor["reward_image"])
        self.assertEqual(coordinator._reward_image_tasks, {})

    async def test_reset_and_shutdown_cancel_the_retry_delay(self):
        for stop in ("reset", "shutdown"):
            with mock.patch.object(ri, "RETRY_DELAY", 3600):
                hass, _, coordinator = await self.make_reward(_Response(status=503), _Response(body=_ok(PNG)))
                await self.photo(coordinator)
                for _ in range(50):
                    await asyncio.sleep(0)
                task = coordinator._reward_image_tasks["leonor"]
                self.assertFalse(task.done())
                if stop == "reset":
                    await coordinator.reset_routine(None)
                else:
                    await coordinator.async_shutdown()
                await self.settle(hass)
            self.assertTrue(task.cancelled(), stop)
            self.assertEqual(len(hass.http_session.requests), 1, stop)
            self.assertEqual([name for name in self.files() if "reward" in name], [], stop)

    async def test_retry_is_logged_without_secrets(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs(level="DEBUG") as captured:
            await self.run_flow(_Response(status=503, body=f"token {TOKEN}".encode()), _Response(body=_ok(PNG)))
            await self.run_flow(
                _Response(error=RuntimeError(f"Authorization: Bearer {TOKEN}")), _Response(body=_ok(PNG))
            )
        output = "\n".join(captured.output)
        infos = [line for line in captured.output if line.startswith("INFO")]
        self.assertTrue(any("request for leonor: retrying after transient error (HTTP 503)" in line for line in infos))
        self.assertTrue(any("retrying after transient error (RuntimeError)" in line for line in infos))
        self.assertIn("answered the request for leonor (retry): HTTP 200", output)
        self.assertNotIn(TOKEN, output)
        self.assertNotIn("Authorization", output)


class TestStreakInCoordinator(tc.CoordinatorTestCase):
    def at(self, day: date, hour=8, minute=20):
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
        dt = sys.modules["homeassistant.util.dt"]
        patcher = mock.patch.multiple(dt, now=lambda: moment, utcnow=lambda: moment)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    async def complete_all(self, coordinator, child):
        for activity in coordinator.data[child]["activities"]:
            await coordinator.complete_activity(child, activity["id"])

    async def test_week_with_weekend_and_a_missed_day(self):
        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCEMENTS_ENABLED: False})

        for expected, day in ((1, THU), (2, FRI)):
            patcher = self.at(day)
            await coordinator.reset_routine(None)
            await self.complete_all(coordinator, "duarte")
            self.assertEqual(st.current(coordinator.data["duarte"]), (expected, expected))
            patcher.stop()
        self.assertEqual(st.current(coordinator.data["leonor"]), (0, 0))

        # the daily reset also runs at the weekend: it must not break the streak
        for day in (SAT, SUN):
            patcher = self.at(day, hour=6, minute=0)
            await coordinator.reset_routine(None)
            self.assertEqual(st.current(coordinator.data["duarte"]), (2, 2))
            patcher.stop()

        patcher = self.at(NEXT_MON)
        await coordinator.reset_routine(None)
        await self.complete_all(coordinator, "duarte")
        self.assertEqual(st.current(coordinator.data["duarte"]), (3, 3))
        self.assertEqual(coordinator.store.saved["duarte"]["streak"], 3)
        patcher.stop()

        # Tuesday is missed: noticed at Wednesday's reset
        patcher = self.at(NEXT_TUE)
        await coordinator.reset_routine(None)
        self.assertEqual(st.current(coordinator.data["duarte"]), (3, 3))
        patcher.stop()
        self.at(date(2026, 11, 18))
        await coordinator.reset_routine(None)
        self.assertEqual(st.current(coordinator.data["duarte"]), (0, 3))
        await self.complete_all(coordinator, "duarte")
        self.assertEqual(st.current(coordinator.data["duarte"]), (1, 3))

    async def test_untick_and_manual_reset_the_same_day(self):
        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCEMENTS_ENABLED: False})
        self.at(MON)
        await self.complete_all(coordinator, "leonor")
        self.assertEqual(st.current(coordinator.data["leonor"]), (1, 1))

        await coordinator.complete_activity("leonor", "teeth", completed=False)
        self.assertEqual(st.current(coordinator.data["leonor"]), (0, 0))
        self.assertEqual(coordinator.store.saved["leonor"]["streak"], 0)
        await coordinator.complete_activity("leonor", "teeth")
        self.assertEqual(st.current(coordinator.data["leonor"]), (1, 1))

        # reset button on the card after finishing: today stays counted, and only once
        await coordinator.reset_routine("leonor")
        self.assertEqual(st.current(coordinator.data["leonor"]), (1, 1))
        await self.complete_all(coordinator, "leonor")
        self.assertEqual(st.current(coordinator.data["leonor"]), (1, 1))

    async def test_every_day_counts_without_business_days_only(self):
        hass, _, coordinator = await self.make(
            **{const.CONF_ANNOUNCEMENTS_ENABLED: False, const.CONF_BUSINESS_DAYS_ONLY: False}
        )
        patcher = self.at(FRI)
        await self.complete_all(coordinator, "duarte")
        patcher.stop()
        patcher = self.at(SAT)
        await coordinator.reset_routine(None)
        patcher.stop()
        self.at(SUN)
        await coordinator.reset_routine(None)  # Saturday missed
        self.assertEqual(st.current(coordinator.data["duarte"]), (0, 1))


class TestCompletionTime(tc.CoordinatorTestCase):
    async def spoken_at(self, hour, minute):
        hass, _, coordinator = await self.make()
        moment = datetime(2026, 11, 13, hour, minute, tzinfo=timezone.utc)
        dt = sys.modules["homeassistant.util.dt"]
        with mock.patch.multiple(dt, now=lambda: moment, utcnow=lambda: moment):
            await coordinator._announce_completion("duarte")
        return self.spoken(hass)[0][1]

    async def test_fixed_sentence_follows_the_clock(self):
        base = "Parabéns, Duarte! Completaste todas as tarefas."
        self.assertEqual(await self.spoken_at(8, 25), f"{base} Ainda tens 25 minutos para brincar.")
        self.assertEqual(await self.spoken_at(8, 46), f"{base} Ainda tens 4 minutos para brincar.")
        self.assertEqual(await self.spoken_at(8, 47), f"{base} Mesmo a tempo de sair!")
        self.assertEqual(await self.spoken_at(8, 49), f"{base} Mesmo a tempo de sair!")
        self.assertEqual(await self.spoken_at(8, 50), f"{base} Está na hora de sair!")
        self.assertEqual(await self.spoken_at(9, 15), f"{base} Está na hora de sair!")
        self.assertEqual(await self.spoken_at(6, 30), f"{base} Ainda tens mais de uma hora para brincar.")

    async def test_ai_gets_the_time_facts(self):
        hass, _, coordinator = await self.make(
            **{const.CONF_AI_ANNOUNCEMENTS_ENABLED: True, const.CONF_AI_AGENT: tc.AGENT}
        )
        hass.services.handlers[("conversation", "process")] = lambda data: tc._answer("Boa, Duarte!")
        moment = datetime(2026, 11, 13, 8, 48, tzinfo=timezone.utc)
        dt = sys.modules["homeassistant.util.dt"]
        with mock.patch.multiple(dt, now=lambda: moment, utcnow=lambda: moment):
            await coordinator._announce_completion("duarte")
        text = hass.services.of("conversation", "process")[0][2]["text"]
        facts = json.loads(text.split("FACTOS (JSON): ", 1)[1])
        self.assertEqual(facts["minutos_para_escola"], 2)
        self.assertEqual(
            facts["conclusao"],
            {"crianca": "Duarte", "minutos_para_escola": 2, "dica_de_tempo": "mesmo a tempo de sair"},
        )
        self.assertIn("dica_de_tempo", text.split("FACTOS (JSON)")[0])  # default rules explain it


# ---------------------------------------------------------------------------
# Options flow: the token is a secret
# ---------------------------------------------------------------------------

def _load_config_flow():
    """Import config_flow.py with a few more Home Assistant fakes."""

    class _Flow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

        def async_create_entry(self, title="", data=None):
            return {"type": "create_entry", "data": data}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

    class _Anything:
        """Stands for any selector / config class: remembers how it was built."""

        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

        def __class_getitem__(cls, item):
            return cls

    class _SelectorModule(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return type(name, (_Anything,), {"URL": "url", "PASSWORD": "password", "BOX": "box"})

    config_entries = sys.modules["homeassistant.config_entries"]
    config_entries.ConfigFlow = _Flow
    config_entries.OptionsFlow = _Flow
    sys.modules["homeassistant.core"].callback = lambda func: func
    flow_module = types.ModuleType("homeassistant.data_entry_flow")
    flow_module.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = flow_module
    selector = _SelectorModule("homeassistant.helpers.selector")
    sys.modules["homeassistant.helpers.selector"] = selector
    sys.modules["homeassistant.helpers"].selector = selector
    sys.modules["homeassistant"].config_entries = config_entries
    uploader = types.ModuleType(f"{PACKAGE}.google_drive_uploader")
    uploader.GoogleDriveUploader = object
    sys.modules.setdefault(f"{PACKAGE}.google_drive_uploader", uploader)

    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.config_flow", os.path.join(PACKAGE_DIR, "config_flow.py")
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PACKAGE
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(getattr(sys.modules["homeassistant"], "_is_stub", False), "needs the Home Assistant stubs")
class TestRewardsOptionsStep(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_flow = _load_config_flow()

    def flow(self, options=None):
        handler = self.config_flow.OptionsFlowHandler()
        handler.config_entry = tc._Entry(options or {})
        handler.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_entries=lambda domain: [])
        )
        return handler

    NEW = const.REWARD_TYPE_AI_IMAGE_FROM_PHOTO

    async def test_form_never_contains_the_token(self):
        result = await self.flow({const.CONF_REWARD_IMAGE_API_TOKEN: TOKEN}).async_step_rewards()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["description_placeholders"], {"token_state": "guardado"})

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield from walk(key)
                    yield from walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from walk(item)
            elif hasattr(value, "kwargs"):
                yield from walk(value.args)
                yield from walk(value.kwargs)
            else:
                yield value

        self.assertNotIn(TOKEN, [str(item) for item in walk(result)])
        fields = list(result["data_schema"].schema)
        for key in (const.CONF_REWARD_TYPE, const.CONF_REWARD_IMAGE_API_URL, const.CONF_REWARD_IMAGE_API_TOKEN,
                    const.CONF_REWARD_IMAGE_API_TOPIC, const.CONF_REWARD_IMAGE_PROMPT, const.CONF_REWARD_IMAGE_TIMEOUT,
                    "reward_image_notes_duarte", "reward_image_notes_leonor", const.CONF_YOUTUBE_PLAYLIST_ID,
                    const.CONF_REWARD_IMAGE_CONFIRM_TEXT, const.CONF_REWARD_IMAGE_CONFIRM_ATTEMPTS):
            self.assertIn(key, fields)

        result = await self.flow().async_step_rewards()
        self.assertEqual(result["description_placeholders"], {"token_state": "ainda não configurado"})

    async def test_empty_token_keeps_the_stored_one(self):
        stored = {const.CONF_REWARD_IMAGE_API_TOKEN: TOKEN, "activities_duarte": {"fixed": []}, const.CONF_AI_AGENT: "x"}
        result = await self.flow(stored).async_step_rewards({const.CONF_REWARD_TYPE: self.NEW})
        data = result["data"]
        self.assertEqual(data[const.CONF_REWARD_IMAGE_API_TOKEN], TOKEN)
        self.assertEqual(data[const.CONF_REWARD_TYPE], self.NEW)
        self.assertEqual(data[const.CONF_REWARD_IMAGE_API_URL], const.DEFAULT_REWARD_IMAGE_API_URL)
        self.assertEqual(data[const.CONF_REWARD_IMAGE_API_TOPIC], "Imagens")
        self.assertEqual(data[const.CONF_REWARD_IMAGE_TIMEOUT], 180)
        self.assertNotIn(const.CONF_REWARD_IMAGE_PROMPT, data)  # default prompt is not frozen in the options
        self.assertNotIn("reward_image_api_token_clear", data)
        # other sections are untouched
        self.assertEqual(data["activities_duarte"], {"fixed": []})
        self.assertEqual(data[const.CONF_AI_AGENT], "x")

    async def test_new_token_replaces_and_clear_erases(self):
        stored = {const.CONF_REWARD_IMAGE_API_TOKEN: TOKEN}
        result = await self.flow(stored).async_step_rewards(
            {const.CONF_REWARD_TYPE: self.NEW, const.CONF_REWARD_IMAGE_API_TOKEN: "  novo-token  "}
        )
        self.assertEqual(result["data"][const.CONF_REWARD_IMAGE_API_TOKEN], "novo-token")

        result = await self.flow(stored).async_step_rewards(
            {const.CONF_REWARD_TYPE: "quote", "reward_image_api_token_clear": True}
        )
        self.assertEqual(result["data"][const.CONF_REWARD_IMAGE_API_TOKEN], "")

    async def test_validation(self):
        # the new type needs a token
        result = await self.flow().async_step_rewards({const.CONF_REWARD_TYPE: self.NEW})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {const.CONF_REWARD_IMAGE_API_TOKEN: "reward_image_token_required"})
        # the other types do not
        result = await self.flow().async_step_rewards({const.CONF_REWARD_TYPE: "youtube_video"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][const.CONF_REWARD_TYPE], "youtube_video")
        # URL must be http(s)
        result = await self.flow().async_step_rewards(
            {const.CONF_REWARD_TYPE: "quote", const.CONF_REWARD_IMAGE_API_URL: "orchestron.tech/api"}
        )
        self.assertEqual(result["errors"], {const.CONF_REWARD_IMAGE_API_URL: "reward_image_invalid_url"})

    async def test_values_are_normalised(self):
        result = await self.flow().async_step_rewards(
            {
                const.CONF_REWARD_TYPE: self.NEW,
                const.CONF_REWARD_IMAGE_API_TOKEN: TOKEN,
                const.CONF_REWARD_IMAGE_TIMEOUT: 5000.0,
                const.CONF_REWARD_IMAGE_PROMPT: "Desenha {name}",
                "reward_image_notes_duarte": " tem 10 anos ",
            }
        )
        data = result["data"]
        self.assertEqual(data[const.CONF_REWARD_IMAGE_TIMEOUT], 600)
        self.assertEqual(data[const.CONF_REWARD_IMAGE_PROMPT], "Desenha {name}")
        self.assertEqual(data["reward_image_notes_duarte"], "tem 10 anos")
        self.assertEqual(data["reward_image_notes_leonor"], "")  # emptied field: no notes

    async def test_confirmation_options(self):
        text, attempts = const.CONF_REWARD_IMAGE_CONFIRM_TEXT, const.CONF_REWARD_IMAGE_CONFIRM_ATTEMPTS
        base = {const.CONF_REWARD_TYPE: self.NEW, const.CONF_REWARD_IMAGE_API_TOKEN: TOKEN}
        # defaults: 2 follow-ups; the default text is not frozen in the options
        data = (await self.flow().async_step_rewards(dict(base)))["data"]
        self.assertEqual(data[attempts], 2)
        self.assertNotIn(text, data)
        data = (await self.flow().async_step_rewards(
            {**base, text: const.DEFAULT_REWARD_IMAGE_CONFIRM_TEXT, attempts: 0.0}
        ))["data"]
        self.assertEqual(data[attempts], 0)
        self.assertIs(type(data[attempts]), int)
        self.assertNotIn(text, data)
        # customised text is stored; attempts are clamped to 0-3
        data = (await self.flow().async_step_rewards({**base, text: "  confirmo  ", attempts: 9}))["data"]
        self.assertEqual(data[text], "confirmo")
        self.assertEqual(data[attempts], 3)
        data = (await self.flow().async_step_rewards({**base, attempts: -2}))["data"]
        self.assertEqual(data[attempts], 0)
        # an emptied text goes back to the default
        data = (await self.flow({text: "confirmo"}).async_step_rewards({**base, text: ""}))["data"]
        self.assertNotIn(text, data)


class TestRewardTranslations(unittest.TestCase):
    def _load(self, *parts):
        with open(os.path.join(PACKAGE_DIR, *parts), encoding="utf-8") as handle:
            return json.load(handle)

    def test_fields_and_errors_translated_and_files_identical(self):
        strings = self._load("strings.json")
        self.assertEqual(strings, self._load("translations", "pt.json"))
        step = strings["options"]["step"]["rewards"]
        for key in (const.CONF_REWARD_TYPE, const.CONF_REWARD_IMAGE_API_URL, const.CONF_REWARD_IMAGE_API_TOKEN,
                    "reward_image_api_token_clear", const.CONF_REWARD_IMAGE_API_TOPIC, const.CONF_REWARD_IMAGE_PROMPT,
                    const.CONF_REWARD_IMAGE_TIMEOUT, "reward_image_notes_duarte", "reward_image_notes_leonor",
                    "reward_image_confirm_text", "reward_image_confirm_attempts"):
            self.assertIn(key, step["data"])
            self.assertIn(key, step["data_description"])
        self.assertIn("{token_state}", step["description"])
        for code in ("reward_image_invalid_url", "reward_image_token_required"):
            self.assertIn(code, strings["options"]["error"])

    def test_constants(self):
        self.assertEqual(const.REWARD_TYPES, ["quote", "youtube_video", "ai_image", "ai_image_from_photo"])
        self.assertEqual(const.DEFAULT_REWARD_TYPE, "quote")
        self.assertEqual(const.DEFAULT_REWARD_IMAGE_API_URL, "https://orchestron.tech/api/v1/ask")
        self.assertEqual(const.DEFAULT_REWARD_IMAGE_NOTES, {"duarte": "", "leonor": "tem 8 anos e já sabe ler bem"})
        self.assertEqual(const.CONF_REWARD_IMAGE_CONFIRM_TEXT, "reward_image_confirm_text")
        self.assertEqual(const.CONF_REWARD_IMAGE_CONFIRM_ATTEMPTS, "reward_image_confirm_attempts")
        self.assertEqual(
            const.DEFAULT_REWARD_IMAGE_CONFIRM_TEXT, "Confirmo. Gera já a imagem, sem pedir mais confirmações."
        )
        self.assertEqual(const.DEFAULT_REWARD_IMAGE_CONFIRM_ATTEMPTS, 2)
        self.assertEqual(
            [const.conf_reward_image_notes_key(child) for child in const.CHILDREN],
            ["reward_image_notes_duarte", "reward_image_notes_leonor"],
        )


if __name__ == "__main__":
    unittest.main()
