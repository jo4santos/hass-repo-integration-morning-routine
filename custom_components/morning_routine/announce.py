"""Announcement logic for the Morning Routine integration.

Pure Python: this module must stay importable without Home Assistant
(no ``homeassistant`` imports), so it can be unit tested on its own.

It builds the fixed (fallback) Portuguese sentences, the FACTS given to the
conversation agent, the prompt, and cleans up / validates the agent's answer.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .activities import special_activity_names

MAX_ANSWER_LENGTH = 400

# Announcement kinds
KIND_30_MINUTES = "30_minutes"
KIND_10_MINUTES = "10_minutes"
KIND_LEAVE_NOW = "leave_now"
KIND_COMPLETION = "completion"
KIND_TIME_REMAINING = "time_remaining"
KIND_TIME_WITH_WEATHER = "time_with_weather"
KIND_TIME_WITH_ACTIVITIES = "time_with_activities"

# kind -> (name given to the agent, what the announcement must achieve)
ANNOUNCEMENT_KINDS: dict[str, tuple[str, str]] = {
    KIND_30_MINUTES: (
        "aviso_30_minutos",
        "Dar os bons-dias, avisar que faltam 30 minutos para sair para a escola e resumir a previsão do tempo.",
    ),
    KIND_10_MINUTES: (
        "aviso_10_minutos",
        "Avisar, numa só frase, que faltam 10 minutos para sair para a escola.",
    ),
    KIND_LEAVE_NOW: (
        "hora_de_sair",
        "Avisar, numa só frase, que está na hora de sair para a escola.",
    ),
    KIND_COMPLETION: (
        "rotina_completa",
        "Dar os parabéns à criança indicada em 'conclusao' por ter terminado a rotina da manhã.",
    ),
    KIND_TIME_REMAINING: (
        "tempo_restante",
        "Dizer quantos minutos faltam para sair para a escola.",
    ),
    KIND_TIME_WITH_WEATHER: (
        "tempo_restante_e_meteorologia",
        "Dizer quantos minutos faltam para sair para a escola e resumir a previsão do tempo.",
    ),
    KIND_TIME_WITH_ACTIVITIES: (
        "tempo_restante_meteorologia_e_atividades",
        "Dizer quantos minutos faltam para sair para a escola, resumir a previsão do tempo e lembrar as atividades especiais de hoje de cada criança.",
    ),
}

# Keys allowed in the FACTS given to the agent (nothing else may be sent)
FACT_KEYS = {
    "tipo",
    "objetivo",
    "minutos_para_escola",
    "hora_da_escola",
    "dia_da_semana",
    "data",
    "tempo",
    "criancas",
    "conclusao",
}
WEATHER_FACT_KEYS = {
    "condicao",
    "temperatura_maxima",
    "temperatura_atual",
    "chuva_prevista",
    "roupa",
}
CHILD_FACT_KEYS = {
    "nome",
    "genero",
    "feitas",
    "em_falta",
    "atividades_especiais_hoje",
    "rotina_completa",
}
COMPLETION_FACT_KEYS = {"crianca", "frase_do_dia"}

WEEKDAYS_PT = [
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
]
MONTHS_PT = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

WEATHER_TRANSLATIONS = {
    "clear-night": "céu limpo",
    "cloudy": "nublado",
    "fog": "nevoeiro",
    "hail": "granizo",
    "lightning": "trovoada",
    "lightning-rainy": "trovoada com chuva",
    "partlycloudy": "parcialmente nublado",
    "pouring": "chuva forte",
    "rainy": "chuvoso",
    "snowy": "neve",
    "snowy-rainy": "chuva com neve",
    "sunny": "sol",
    "windy": "ventoso",
    "windy-variant": "ventoso",
    "exceptional": "excecional",
}


def translate_weather_condition(condition: str) -> str:
    """Translate a Home Assistant weather condition to Portuguese."""
    return WEATHER_TRANSLATIONS.get(str(condition).lower(), condition)


def temperature_description(temperature: float) -> str:
    """Describe a temperature (Esmoriz climate) to help choosing clothes."""
    if temperature < 10:
        return "está frio"
    if temperature < 15:
        return "está fresco"
    if temperature < 20:
        return "está ameno"
    if temperature < 25:
        return "está agradável"
    return "está quente"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def build_weather_info(
    condition: str,
    temperature: Any,
    has_forecast: bool,
    precipitation: Any = None,
) -> dict[str, Any]:
    """Summarise the weather for announcements.

    ``temperature`` is today's maximum when a forecast is available, else the
    current temperature. ``rain_expected`` is None when there is no forecast.
    """
    numeric = _is_number(temperature)
    return {
        "condition": condition,
        "temperature": temperature if numeric else None,
        "temperature_is_max": bool(has_forecast),
        "rain_expected": (
            bool(_is_number(precipitation) and precipitation > 0) if has_forecast else None
        ),
        "clothing_hint": temperature_description(temperature) if numeric else None,
    }


# ---------------------------------------------------------------------------
# Fixed (fallback) sentences - identical to the pre-AI announcements
# ---------------------------------------------------------------------------

def weather_sentence(weather: dict[str, Any] | None, closing: bool = True) -> str:
    """Return the fixed weather sentence (with a leading space), or ''."""
    if not weather:
        return ""

    condition = weather.get("condition")
    temperature = weather.get("temperature")
    if _is_number(temperature):
        message = (
            f" A previsão para hoje é {condition}, com máxima de {temperature} graus"
            f" e {weather.get('clothing_hint')}."
        )
    else:
        message = f" A previsão para hoje é {condition}."

    rain_expected = weather.get("rain_expected")
    if rain_expected:
        message += " Há possibilidade de chuva. Não se esqueçam do guarda-chuva!"
    elif rain_expected is not None and closing:
        message += " Tenham um ótimo dia!"
    return message


def special_activities_sentence(children: list[dict[str, Any]]) -> str:
    """Return the fixed sentence(s) listing each child's special activities.

    ``children`` items: {"article": "O", "name": "Duarte", "special": [names]}.
    """
    message = ""
    for child in children:
        names = child.get("special") or []
        if not names:
            continue
        who = f"{child.get('article', 'A')} {child.get('name')}"
        if len(names) == 1:
            message += f" {who} tem {names[0]} hoje."
        elif len(names) == 2:
            message += f" {who} tem {names[0]} e {names[1]} hoje."
        else:
            message += f" {who} tem {', '.join(names[:-1])} e {names[-1]} hoje."
    return message


def completion_fallback(child_config: dict[str, Any], daily_phrase: str | None) -> str:
    """Return the fixed completion announcement, with the daily phrase if any."""
    name = child_config.get("name", "")
    article = child_config.get("article", "A")
    ready = child_config.get("pronoun_ready", "pronta")
    message = f"Parabéns! {article} {name} está {ready}!"
    if daily_phrase:
        message += f" A frase do dia para {article.lower()} {name} é: {daily_phrase}"
    return message


# ---------------------------------------------------------------------------
# Facts and prompt for the conversation agent
# ---------------------------------------------------------------------------

def build_child_facts(
    child_config: dict[str, Any], activities: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Return the facts about one child's routine today."""
    activities = activities or []
    done = [a["name"] for a in activities if a.get("completed")]
    missing = [a["name"] for a in activities if not a.get("completed")]
    return {
        "nome": child_config.get("name", ""),
        "genero": "masculino" if child_config.get("gender") == "m" else "feminino",
        "feitas": done,
        "em_falta": missing,
        "atividades_especiais_hoje": special_activity_names(activities),
        "rotina_completa": bool(activities) and not missing,
    }


def build_facts(
    kind: str,
    *,
    now: datetime,
    school_time: str,
    minutes_to_school: int | None,
    children: list[dict[str, Any]],
    weather: dict[str, Any] | None = None,
    completion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the FACTS dict given to the conversation agent.

    Only the values passed in are used - never configuration or credentials.
    ``children`` are the dicts returned by build_child_facts().
    ``completion``: {"child": name, "daily_phrase": str | None}.
    """
    kind_name, goal = ANNOUNCEMENT_KINDS[kind]
    facts: dict[str, Any] = {
        "tipo": kind_name,
        "objetivo": goal,
        "hora_da_escola": str(school_time)[:5],
        "dia_da_semana": WEEKDAYS_PT[now.weekday()],
        "data": f"{now.day} de {MONTHS_PT[now.month - 1]}",
    }
    if minutes_to_school is not None:
        facts["minutos_para_escola"] = int(minutes_to_school)

    if weather:
        tempo: dict[str, Any] = {"condicao": weather.get("condition")}
        if weather.get("temperature") is not None:
            key = (
                "temperatura_maxima"
                if weather.get("temperature_is_max")
                else "temperatura_atual"
            )
            tempo[key] = weather["temperature"]
        if weather.get("rain_expected") is not None:
            tempo["chuva_prevista"] = bool(weather["rain_expected"])
        if weather.get("clothing_hint"):
            tempo["roupa"] = weather["clothing_hint"]
        facts["tempo"] = tempo

    facts["criancas"] = [
        {key: value for key, value in child.items() if key in CHILD_FACT_KEYS}
        for child in children
    ]

    if completion:
        conclusao: dict[str, Any] = {"crianca": completion.get("child")}
        if completion.get("daily_phrase"):
            conclusao["frase_do_dia"] = completion["daily_phrase"]
        facts["conclusao"] = conclusao

    return facts


def build_prompt(instructions: str, facts: dict[str, Any]) -> str:
    """Return the text sent to the conversation agent: instructions + facts."""
    facts_json = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    return (
        f"{(instructions or '').strip()}\n\n"
        f"Anúncio a fazer agora: {facts.get('tipo')}. {facts.get('objetivo')}\n"
        f"FACTOS (JSON): {facts_json}"
    )


# ---------------------------------------------------------------------------
# Answer handling
# ---------------------------------------------------------------------------

def extract_speech(response: Any) -> str | None:
    """Extract the spoken text from a conversation.process response.

    Returns None when there is no text or the agent reported an error.
    """
    if not isinstance(response, dict):
        return None
    inner = response.get("response")
    if not isinstance(inner, dict):
        return None
    if inner.get("response_type") == "error":
        return None
    speech = inner.get("speech")
    if not isinstance(speech, dict):
        return None
    plain = speech.get("plain")
    if not isinstance(plain, dict):
        return None
    text = plain.get("speech")
    return text if isinstance(text, str) and text.strip() else None


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictographs, emoticons, transport, flags, supplemental
    "\U00002190-\U000021FF"  # arrows
    "\U00002300-\U000023FF"  # technical (watch, hourglass...)
    "\U00002460-\U000024FF"  # enclosed alphanumerics
    "\U000025A0-\U000027BF"  # shapes, misc symbols, dingbats
    "\U00002900-\U0000297F"
    "\U00002B00-\U00002BFF"  # stars, arrows
    "\U00003030\U0000303D\U00003297\U00003299"
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D\U000020E3"  # zero-width joiner, keycap
    "\U000E0020-\U000E007F"  # tags
    "]+"
)
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*")
_LINE_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+•·]\s+|\d{1,2}[.)]\s+(?=\D))")
_UNDERSCORE_EMPHASIS_RE = re.compile(r"(?<![^\W_])_{1,3}([^_\n]+?)_{1,3}(?![^\W_])")
_DOUBLE_QUOTES_RE = re.compile("[\"“”„‟«»‹›]")
_SINGLE_QUOTES_RE = re.compile("(?<![^\\W\\d_])['‘’]|['‘’](?![^\\W\\d_])")


def sanitize_answer(text: Any) -> str:
    """Make an agent answer safe for TTS: no markdown, emoji, quotes or lists."""
    if not isinstance(text, str):
        return ""

    text = _FENCE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)

    lines = []
    for line in text.splitlines():
        line = _LINE_PREFIX_RE.sub("", line).strip()
        if line:
            lines.append(line)
    text = " ".join(lines)

    text = text.replace("*", "").replace("`", "").replace("~~", "")
    text = _UNDERSCORE_EMPHASIS_RE.sub(r"\1", text)
    text = _EMOJI_RE.sub(" ", text)
    text = _DOUBLE_QUOTES_RE.sub("", text)
    text = _SINGLE_QUOTES_RE.sub("", text)

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def choose_message(
    answer: Any, fallback: str, max_length: int = MAX_ANSWER_LENGTH
) -> tuple[str, str, str]:
    """Pick the announcement text: the agent's answer if usable, else the fallback.

    Returns (message, path, reason) with path "ai" or "fallback".
    """
    cleaned = sanitize_answer(answer)
    if not cleaned:
        return fallback, "fallback", "empty answer"
    if len(cleaned) > max_length:
        return fallback, "fallback", f"answer too long ({len(cleaned)} chars)"
    return cleaned, "ai", "ok"
