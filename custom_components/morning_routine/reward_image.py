"""Reward image generated from the child's photo (pure logic).

Pure Python: this module must stay importable without Home Assistant
(no ``homeassistant`` / ``aiohttp`` imports), so it can be unit tested on its own.

It renders the prompt, builds the request body for the external image
service, and validates / decodes the service's answer. The HTTP call itself
and the file IO live in the coordinator (``__init__.py``).

Service contract (as used by the owner's own service):

    POST <url>   Authorization: Bearer <token>   Content-Type: application/json
    {"topic": "Imagens", "text": <prompt>,
     "media": [{"mime": "image/jpeg", "data": <base64 of the photo>}]}

    answer (JSON): the image at ``media[0].data`` (base64); otherwise a text
    at ``answer`` or ``error``.

Confirmation follow-up (v2.13.1): the service may answer the first request
with a text only ("the image is planned, waiting for your approval"). The
coordinator then sends the confirmation text, without the photo:

    {"topic": "Imagens", "text": <confirmation text>, ...echoed identifiers}

How the service links the two turns is not documented (it may simply be the
topic), so the identifiers of the previous answer are echoed back - see
``continuation_fields``.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

# Reward image states exposed in the sensor (attribute reward_image_status)
STATUS_GENERATING = "generating"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# Sanity limit for the service's answer (bytes of the HTTP body)
MAX_RESPONSE_BYTES = 15 * 1024 * 1024

PHOTO_MIME = "image/jpeg"

# Transient failures (v2.13.2): each request (the first one and every confirmation
# follow-up) is tried once more, after RETRY_DELAY seconds, before it counts as failed.
RETRY_DELAY = 10.0
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

# Result of the last generation attempt (attribute reward_image_last_attempt)
ATTEMPT_OK = "ok"
ATTEMPT_FAILED = "failed"

# Error codes -> short pt-PT message stored in the child's data (never secrets)
ERR_NO_TOKEN = "no_token"
ERR_NO_PHOTO = "no_photo"
ERR_PHOTO_UNREADABLE = "photo_unreadable"
ERR_TIMEOUT = "timeout"
ERR_HTTP = "http_error"
ERR_CONNECTION = "connection_error"
ERR_TOO_LARGE = "too_large"
ERR_INVALID_JSON = "invalid_json"
ERR_SERVICE_ERROR = "service_error"
ERR_NO_MEDIA = "no_media"
ERR_BAD_BASE64 = "bad_base64"
ERR_NOT_AN_IMAGE = "not_an_image"
ERR_WRITE_FAILED = "write_failed"
ERR_INTERRUPTED = "interrupted"
ERR_UNEXPECTED = "unexpected"

ERROR_MESSAGES = {
    ERR_NO_TOKEN: "Falta configurar o token do serviço de imagens.",
    ERR_NO_PHOTO: "Ainda não há fotografia de hoje.",
    ERR_PHOTO_UNREADABLE: "Não foi possível ler a fotografia.",
    ERR_TIMEOUT: "O serviço de imagens demorou demasiado tempo.",
    ERR_HTTP: "O serviço de imagens respondeu com um erro.",
    ERR_CONNECTION: "Não foi possível contactar o serviço de imagens.",
    ERR_TOO_LARGE: "A resposta do serviço de imagens é demasiado grande.",
    ERR_INVALID_JSON: "A resposta do serviço de imagens não é válida.",
    ERR_SERVICE_ERROR: "O serviço de imagens devolveu um erro.",
    ERR_NO_MEDIA: "O serviço de imagens não devolveu nenhuma imagem.",
    ERR_BAD_BASE64: "A imagem recebida está corrompida.",
    ERR_NOT_AN_IMAGE: "O ficheiro recebido não é uma imagem válida.",
    ERR_WRITE_FAILED: "Não foi possível guardar a imagem.",
    ERR_INTERRUPTED: "A geração da imagem foi interrompida.",
    ERR_UNEXPECTED: "Erro inesperado ao gerar a imagem.",
}

# Added to the message when the confirmation follow-up was tried and no image came back
APPROVAL_MESSAGE = "O serviço de imagens ficou à espera de aprovação e não devolveu a imagem."

# Added to the message when a failed (re)generation left the previous image of the day in place
KEPT_PREVIOUS_MESSAGE = "Mantém-se a imagem anterior."


def failure_message(code: str, followups: int = 0) -> str:
    """Return the pt-PT message of a failure.

    When at least one confirmation follow-up was sent and the service still
    answered with a text or an error, the message also says that the service
    was waiting for approval.
    """
    message = ERROR_MESSAGES.get(code, ERROR_MESSAGES[ERR_UNEXPECTED])
    if followups > 0 and code in (ERR_NO_MEDIA, ERR_SERVICE_ERROR):
        message = f"{message} {APPROVAL_MESSAGE}"
    return message


class RewardImageError(Exception):
    """A reward image could not be generated.

    ``code`` is one of the ERR_* codes, ``message`` the short pt-PT text shown
    to the family, ``detail`` an optional technical detail for the log (already
    truncated; the caller still scrubs the token from it).
    """

    def __init__(
        self, code: str, detail: str | None = None, *, status: int | None = None, transient: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = ERROR_MESSAGES.get(code, ERROR_MESSAGES[ERR_UNEXPECTED])
        self.detail = detail
        self.status = status  # HTTP status, for ERR_HTTP
        self.transient = transient  # worth one automatic retry (see is_transient)


def is_transient(error: Any) -> bool:
    """True for a failure worth one automatic retry.

    HTTP 429 / 500 / 502 / 503 / 504, a timeout or a connection error. Never
    another 4xx, an ``error`` answer or anything wrong with a 200 answer.
    """
    if not isinstance(error, RewardImageError):
        return False
    if error.code == ERR_HTTP:
        return error.status in TRANSIENT_HTTP_STATUSES
    return error.code == ERR_TIMEOUT or (error.code == ERR_CONNECTION and error.transient)


def last_attempt(result: str, timestamp: str) -> dict[str, str]:
    """Return the value of ``reward_image_last_attempt``: result ('ok' / 'failed') and ISO time."""
    return {"result": result, "at": timestamp}


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def join_pt(names: list[str] | None) -> str:
    """Join names the Portuguese way: 'A', 'A e B', 'A, B e C'."""
    names = [str(name).strip() for name in (names or []) if str(name).strip()]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} e {names[-1]}"


def child_noun(gender: str | None) -> str:
    """Return 'um menino' / 'uma menina' from the CHILDREN_CONFIG gender."""
    return "um menino" if gender == "m" else "uma menina"


def render_prompt(
    template: str | None,
    *,
    name: str,
    gender: str | None,
    notes: str | None = None,
    activities: list[str] | None = None,
    weekday: str = "",
) -> str:
    """Render the prompt template.

    Placeholders: {name}, {child_noun}, {notes}, {notes_clause}, {activities},
    {activities_clause}, {weekday}. Unknown placeholders (and any other braces)
    are left untouched - this never raises.
    """
    notes = " ".join(str(notes or "").split()).strip(" ,.;")
    activities_text = join_pt(activities)
    values = {
        "name": str(name or ""),
        "child_noun": child_noun(gender),
        "notes": notes,
        "notes_clause": f", {notes}" if notes else "",
        "activities": activities_text,
        "activities_clause": (
            f"Deve conter algo relacionado com {activities_text}. " if activities_text else ""
        ),
        "weekday": str(weekday or ""),
    }

    def replace(match: re.Match) -> str:
        key = match.group(1)
        return values[key] if key in values else match.group(0)

    return _PLACEHOLDER_RE.sub(replace, str(template or "")).strip()


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

def build_request_body(topic: str, prompt: str, photo_bytes: bytes) -> dict[str, Any]:
    """Return the JSON body the image service expects."""
    return {
        "topic": topic,
        "text": prompt,
        "media": [
            {
                "mime": PHOTO_MIME,
                "data": base64.b64encode(photo_bytes).decode("ascii"),
            }
        ],
    }


# Top-level keys of an answer that look like conversation identifiers.
CONTINUATION_KEYS = (
    "conversation_id",
    "conversationId",
    "thread_id",
    "threadId",
    "session_id",
    "sessionId",
    "chat_id",
    "chatId",
    "context_id",
    "id",
    "message_id",
    "messageId",
    "reply_to",
)


def continuation_fields(payload: Any) -> dict[str, Any]:
    """Return the conversation identifiers of an answer, to echo back in the follow-up.

    Heuristic: the continuation mechanism of the service is unknown (it may
    simply be the topic). So every TOP-LEVEL key of the previous answer whose
    name is in CONTINUATION_KEYS and whose value is a scalar (non-empty text
    or a number) is sent back unchanged, under the same name. Nested values,
    lists, booleans, nulls and any other key are ignored.
    """
    if not isinstance(payload, dict):
        return {}
    fields: dict[str, Any] = {}
    for key in CONTINUATION_KEYS:
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, str) and value.strip() or isinstance(value, (int, float)):
            fields[key] = value
    return fields


def build_followup_body(topic: str, text: str, previous: Any = None) -> dict[str, Any]:
    """Return the body of a confirmation follow-up: same topic, no photo.

    ``previous`` is the decoded previous answer; its conversation identifiers
    are echoed (see ``continuation_fields``). They never replace topic / text.
    """
    return {"topic": topic, "text": text, **continuation_fields(previous)}


def normalize_confirm_attempts(value: Any, default: int = 2, minimum: int = 0, maximum: int = 3) -> int:
    """Return the number of confirmation follow-ups allowed (clamped; 0 disables)."""
    try:
        attempts = int(float(value))
    except (TypeError, ValueError, OverflowError):
        attempts = default
    return min(max(attempts, minimum), maximum)


def build_request_headers(token: str) -> dict[str, str]:
    """Return the HTTP headers of the request (contains the secret token)."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def is_valid_api_url(url: Any) -> bool:
    """Accept only absolute http(s) URLs."""
    return isinstance(url, str) and bool(re.match(r"^https?://[^\s/]+", url.strip()))


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

def detect_image_extension(data: bytes) -> str | None:
    """Return 'png' / 'jpg' / 'webp' from the file signature, None if unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def short_text(value: Any, limit: int = 200) -> str:
    """Return a single-line, truncated text safe to put in a log line."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[:limit]}..."


def scrub(text: Any, *secrets: str | None) -> str:
    """Remove secrets from a text before it is logged or stored."""
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result


def load_payload(raw: bytes | str | dict) -> dict:
    """Decode the service's answer into a dict.

    Raises RewardImageError when it is too large or is not a JSON object.
    """
    if isinstance(raw, (bytes, bytearray, str)):
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RewardImageError(ERR_TOO_LARGE, f"{len(raw)} bytes")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise RewardImageError(ERR_INVALID_JSON) from None
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise RewardImageError(ERR_INVALID_JSON)
    return payload


def _media_data(payload: dict) -> Any:
    media = payload.get("media")
    first = media[0] if isinstance(media, list) and media else None
    return first.get("data") if isinstance(first, dict) else None


def has_usable_media(payload: Any) -> bool:
    """True when the answer carries something at ``media[0].data``."""
    if not isinstance(payload, dict):
        return False
    data = _media_data(payload)
    return isinstance(data, str) and bool(data.strip())


def needs_confirmation(payload: Any) -> bool:
    """True for a successful text-only answer: no usable media, no ``error``, a text at ``answer``.

    This is how the service answers when it planned the image and waits for approval.
    """
    if not isinstance(payload, dict) or has_usable_media(payload) or payload.get("error"):
        return False
    answer = payload.get("answer")
    return isinstance(answer, str) and bool(answer.strip())


def describe_response(payload: Any, limit: int = 200) -> str:
    """Return a one-line summary of an answer for the log.

    Only the top-level KEY NAMES, whether there is media and how many items,
    and the first ``limit`` characters of the answer text. Never the media
    data. The caller still scrubs the token from the result.
    """
    if not isinstance(payload, dict):
        return "not a JSON object"
    keys = ", ".join(short_text(key, 40) for key in list(payload)[:30])
    media = payload.get("media")
    count = len(media) if isinstance(media, list) else 0
    present = "yes" if has_usable_media(payload) else "no"
    answer = payload.get("answer")
    answer_text = short_text(answer, limit) if isinstance(answer, str) else ""
    error = "yes" if payload.get("error") else "no"
    return f'keys=[{keys}] media={present} (count={count}) error={error} answer="{answer_text}"'


def parse_response(raw: bytes | str | dict) -> tuple[bytes, str]:
    """Validate the service's answer and return (image bytes, file extension).

    Raises RewardImageError when the answer is too large, is not JSON, carries
    only a text (``error`` / ``answer``), has no media, or the media is not a
    valid base64 PNG / JPEG / WebP.
    """
    payload = load_payload(raw)
    data = _media_data(payload)

    if not isinstance(data, str) or not data.strip():
        error = payload.get("error")
        if error:
            raise RewardImageError(ERR_SERVICE_ERROR, short_text(error))
        answer = payload.get("answer")
        if answer:
            raise RewardImageError(ERR_NO_MEDIA, f"text only: {short_text(answer)}")
        raise RewardImageError(ERR_NO_MEDIA)

    data = data.strip()
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    data = "".join(data.split())
    if len(data) > MAX_RESPONSE_BYTES:
        raise RewardImageError(ERR_TOO_LARGE, f"{len(data)} base64 chars")

    try:
        image = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise RewardImageError(ERR_BAD_BASE64) from None

    extension = detect_image_extension(image)
    if extension is None:
        raise RewardImageError(ERR_NOT_AN_IMAGE)
    return image, extension


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

REWARD_FILE_RE = re.compile(r"_reward\.(?:png|jpg|webp)$")


def reward_filename(child: str, timestamp: str, extension: str = "png") -> str:
    """Return '<child>_<YYYYMMDD_HHMMSS>_reward.<ext>'."""
    return f"{child}_{timestamp}_reward.{extension}"


def is_reward_file(filename: str) -> bool:
    """True for a file written by this module (kept out of the photo history)."""
    return bool(REWARD_FILE_RE.search(filename or ""))


def reward_url(filename: str, version: str) -> str:
    """Return the /local URL of a reward image with a cache-busting query."""
    return f"/local/morning_routine_photos/{filename}?v={version}"
