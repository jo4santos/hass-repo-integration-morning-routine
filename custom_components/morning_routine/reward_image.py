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


class RewardImageError(Exception):
    """A reward image could not be generated.

    ``code`` is one of the ERR_* codes, ``message`` the short pt-PT text shown
    to the family, ``detail`` an optional technical detail for the log (already
    truncated; the caller still scrubs the token from it).
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = ERROR_MESSAGES.get(code, ERROR_MESSAGES[ERR_UNEXPECTED])
        self.detail = detail


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


def parse_response(raw: bytes | str | dict) -> tuple[bytes, str]:
    """Validate the service's answer and return (image bytes, file extension).

    Raises RewardImageError when the answer is too large, is not JSON, carries
    only a text (``error`` / ``answer``), has no media, or the media is not a
    valid base64 PNG / JPEG / WebP.
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

    media = payload.get("media")
    first = media[0] if isinstance(media, list) and media else None
    data = first.get("data") if isinstance(first, dict) else None

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
