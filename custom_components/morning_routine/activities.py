"""Activity configuration logic for the Morning Routine integration.

Pure Python: this module must stay importable without Home Assistant
(no ``homeassistant`` imports), so it can be unit tested on its own.

A child's activity configuration has this shape::

    fixed:            # every day, in display order
      - {id: dressed, name: Vestir, icon: mdi:tshirt-crew, camera_required: true}
      - {id: schoolbag, name: Mochila, icon: mdi:bag-personal, nfc_required: true}
    calendar:         # only on days with a matching calendar event
      - {pattern: D-Natação, id: swimming, name: Natação, icon: mdi:swim, nfc_required: true}
"""
from __future__ import annotations

import copy
import fnmatch
import re
from typing import Any

from .const import (
    CALENDAR_ACTIVITY_MAPPING,
    CHILD_ACTIVITY_OVERRIDES,
    FIXED_ACTIVITIES,
)

DEFAULT_ICON = "mdi:checkbox-marked-circle-outline"

# Value of "source" in the stored state of an activity
SOURCE_FIXED = "default"  # kept as "default" for compatibility with stored data / the card
SOURCE_CALENDAR = "calendar"

CONF_ACTIVITIES_PREFIX = "activities_"

_ID_RE = re.compile(r"^[a-z0-9_]+$")
_FIXED_KEYS = {"id", "name", "icon", "camera_required", "nfc_required"}
_CALENDAR_KEYS = _FIXED_KEYS | {"pattern"}
_BOOL_KEYS = ("camera_required", "nfc_required")

# Error codes (translation keys under options.error in strings.json)
ERR_NOT_MAPPING = "activities_not_mapping"
ERR_INVALID_YAML = "activities_invalid_yaml"
ERR_UNKNOWN_SECTION = "activities_unknown_section"
ERR_NOT_LIST = "activities_not_list"
ERR_NO_FIXED = "activities_no_fixed"
ERR_ITEM_NOT_MAPPING = "activities_item_not_mapping"
ERR_UNKNOWN_KEY = "activities_unknown_key"
ERR_MISSING_ID = "activities_missing_id"
ERR_INVALID_ID = "activities_invalid_id"
ERR_MISSING_NAME = "activities_missing_name"
ERR_MISSING_PATTERN = "activities_missing_pattern"
ERR_DUPLICATE_ID = "activities_duplicate_id"
ERR_INVALID_ICON = "activities_invalid_icon"
ERR_INVALID_BOOL = "activities_invalid_bool"


class ActivityConfigError(ValueError):
    """Invalid activity configuration.

    ``code`` is a translation key, ``detail`` says where the problem is.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        """Initialize the error."""
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def conf_activities_key(child: str) -> str:
    """Return the options key holding the activities of a child."""
    return f"{CONF_ACTIVITIES_PREFIX}{child}"


def default_child_config(child: str | None) -> dict[str, list[dict[str, Any]]]:
    """Return the built-in activity configuration of a child (from const.py)."""
    overrides = CHILD_ACTIVITY_OVERRIDES.get(child or "", {})
    fixed = []
    for base in FIXED_ACTIVITIES:
        item = {**base, **overrides.get(base["id"], {})}
        fixed.append(
            {
                "id": item["id"],
                "name": item["name"],
                "icon": item.get("icon", DEFAULT_ICON),
                "camera_required": bool(item.get("camera_required", False)),
                "nfc_required": bool(item.get("nfc_required", False)),
            }
        )

    calendar = []
    for mapping in CALENDAR_ACTIVITY_MAPPING.get(child or "", []):
        activity = mapping["activity"]
        calendar.append(
            {
                "pattern": mapping["pattern"],
                "id": activity["id"],
                "name": activity["name"],
                "icon": activity.get("icon", DEFAULT_ICON),
                "camera_required": bool(activity.get("camera_required", False)),
                "nfc_required": bool(activity.get("nfc_required", True)),
            }
        )

    return {"fixed": fixed, "calendar": calendar}


def is_empty_config(value: Any) -> bool:
    """Return True when the value means "nothing configured" (use defaults)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (dict, list)):
        return not value
    return False


def _parse_item(raw: Any, section: str, index: int, seen_ids: set[str]) -> dict[str, Any]:
    """Validate and normalise one activity item."""
    where = f"{section}[{index + 1}]"
    if not isinstance(raw, dict):
        raise ActivityConfigError(ERR_ITEM_NOT_MAPPING, where)

    allowed = _CALENDAR_KEYS if section == "calendar" else _FIXED_KEYS
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise ActivityConfigError(ERR_UNKNOWN_KEY, f"{where}: {', '.join(unknown)}")

    activity_id = raw.get("id")
    if activity_id is None or activity_id == "":
        raise ActivityConfigError(ERR_MISSING_ID, where)
    if not isinstance(activity_id, str) or not _ID_RE.match(activity_id):
        raise ActivityConfigError(ERR_INVALID_ID, f"{where}: {activity_id}")
    where = f"{where} ({activity_id})"

    name = raw.get("name")
    if isinstance(name, (int, float)) and not isinstance(name, bool):
        name = str(name)
    if not isinstance(name, str) or not name.strip():
        raise ActivityConfigError(ERR_MISSING_NAME, where)

    item: dict[str, Any] = {}
    if section == "calendar":
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ActivityConfigError(ERR_MISSING_PATTERN, where)
        item["pattern"] = pattern.strip()

    if activity_id in seen_ids:
        raise ActivityConfigError(ERR_DUPLICATE_ID, where)
    seen_ids.add(activity_id)

    icon = raw.get("icon")
    if icon is None or icon == "":
        icon = DEFAULT_ICON
    elif not isinstance(icon, str) or not icon.startswith("mdi:") or len(icon) <= 4:
        raise ActivityConfigError(ERR_INVALID_ICON, f"{where}: {icon}")

    item["id"] = activity_id
    item["name"] = name.strip()
    item["icon"] = icon

    for key in _BOOL_KEYS:
        value = raw.get(key, False)
        if value is None:
            value = False
        if not isinstance(value, bool):
            raise ActivityConfigError(ERR_INVALID_BOOL, f"{where}: {key}")
        item[key] = value

    return item


def parse_child_config(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Validate a child's activity configuration and return it normalised.

    Accepts a mapping or a YAML string. Raises ActivityConfigError.
    """
    if isinstance(value, str):
        import yaml  # PyYAML ships with Home Assistant

        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError as err:
            mark = getattr(err, "problem_mark", None)
            detail = f"linha {mark.line + 1}" if mark is not None else ""
            raise ActivityConfigError(ERR_INVALID_YAML, detail) from err

    if not isinstance(value, dict):
        raise ActivityConfigError(ERR_NOT_MAPPING)

    unknown = sorted(str(key) for key in value if key not in ("fixed", "calendar"))
    if unknown:
        raise ActivityConfigError(ERR_UNKNOWN_SECTION, ", ".join(unknown))

    fixed_raw = value.get("fixed")
    calendar_raw = value.get("calendar")
    if calendar_raw is None:
        calendar_raw = []
    if fixed_raw is None:
        raise ActivityConfigError(ERR_NO_FIXED)
    if not isinstance(fixed_raw, list):
        raise ActivityConfigError(ERR_NOT_LIST, "fixed")
    if not isinstance(calendar_raw, list):
        raise ActivityConfigError(ERR_NOT_LIST, "calendar")
    if not fixed_raw:
        raise ActivityConfigError(ERR_NO_FIXED)

    seen_ids: set[str] = set()
    fixed = [_parse_item(raw, "fixed", i, seen_ids) for i, raw in enumerate(fixed_raw)]
    calendar = [
        _parse_item(raw, "calendar", i, seen_ids) for i, raw in enumerate(calendar_raw)
    ]
    return {"fixed": fixed, "calendar": calendar}


def effective_child_config(
    options: dict[str, Any] | None, child: str, logger: Any = None
) -> dict[str, list[dict[str, Any]]]:
    """Return the activity configuration in force for a child (options → defaults)."""
    value = (options or {}).get(conf_activities_key(child))
    if is_empty_config(value):
        return default_child_config(child)
    try:
        return parse_child_config(value)
    except ActivityConfigError as err:
        if logger is not None:
            logger.error(
                "Invalid activities configuration for %s (%s), using built-in defaults",
                child,
                err,
            )
        return default_child_config(child)


def activity_index(config: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Return {activity id: definition + source} for fixed and calendar activities."""
    index: dict[str, dict[str, Any]] = {}
    for item in config.get("fixed", []):
        index[item["id"]] = {**item, "source": SOURCE_FIXED}
    for item in config.get("calendar", []):
        index.setdefault(item["id"], {**item, "source": SOURCE_CALENDAR})
    return index


def activity_ids(config: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Return every activity id known for a child (fixed first, then calendar)."""
    return list(activity_index(config))


def activity_name(config: dict[str, list[dict[str, Any]]], activity_id: str) -> str:
    """Return the display name of an activity (the id itself when unknown)."""
    return activity_index(config).get(activity_id, {}).get("name", activity_id)


def _new_state(definition: dict[str, Any], source: str) -> dict[str, Any]:
    """Return the stored state of a not yet completed activity."""
    return {
        "id": definition["id"],
        "name": definition["name"],
        "icon": definition.get("icon", DEFAULT_ICON),
        "completed": False,
        "completed_at": None,
        "camera_required": bool(definition.get("camera_required", False)),
        "nfc_required": bool(definition.get("nfc_required", False)),
        "source": source,
    }


def build_fixed_activities(config: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return a fresh (nothing completed) list with the fixed activities."""
    return [_new_state(item, SOURCE_FIXED) for item in config.get("fixed", [])]


def match_calendar_activities(
    config: dict[str, list[dict[str, Any]]],
    summaries: list[str],
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Return the calendar activities triggered by today's event summaries.

    The first pattern matching an event wins; each activity is added once.
    """
    matched: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        summary = summary or ""
        for item in config.get("calendar", []):
            if fnmatch.fnmatch(summary, item["pattern"]):
                if item["id"] not in matched:
                    state = _new_state(item, SOURCE_CALENDAR)
                    state["event_summary"] = summary
                    state["last_modified"] = now_iso
                    matched[item["id"]] = state
                # Stop at the first pattern matching this event
                break
    return list(matched.values())


def _updated_state(
    existing: dict[str, Any], definition: dict[str, Any], source: str
) -> dict[str, Any]:
    """Return an existing activity state refreshed with the current definition."""
    state = copy.deepcopy(existing)
    state["name"] = definition["name"]
    state["icon"] = definition.get("icon", DEFAULT_ICON)
    state["camera_required"] = bool(definition.get("camera_required", False))
    state["nfc_required"] = bool(definition.get("nfc_required", False))
    state["source"] = source
    state.setdefault("completed", False)
    state.setdefault("completed_at", None)
    return state


def reconcile_activities(
    current: list[dict[str, Any]] | None, config: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Bring today's activity list in line with the configuration, keeping progress.

    - ids that still exist keep their completion state; name/icon/flags are updated
    - new fixed activities are added (not completed)
    - fixed activities removed from the configuration are dropped
    - calendar activities are kept only while still mapped (same id, and the
      event that created them still matches the pattern)
    - order: fixed activities in configuration order, then calendar ones
    """
    existing: dict[str, dict[str, Any]] = {}
    for activity in current or []:
        if isinstance(activity, dict) and "id" in activity:
            existing.setdefault(activity["id"], activity)

    result: list[dict[str, Any]] = []
    for item in config.get("fixed", []):
        if item["id"] in existing:
            state = _updated_state(existing[item["id"]], item, SOURCE_FIXED)
            state.pop("event_summary", None)
            result.append(state)
        else:
            result.append(_new_state(item, SOURCE_FIXED))

    fixed_ids = {item["id"] for item in config.get("fixed", [])}
    for item in config.get("calendar", []):
        if item["id"] in fixed_ids:
            continue
        previous = existing.get(item["id"])
        if previous is None or previous.get("source") != SOURCE_CALENDAR:
            continue
        summary = previous.get("event_summary")
        if summary is not None and not fnmatch.fnmatch(summary, item["pattern"]):
            continue
        result.append(_updated_state(previous, item, SOURCE_CALENDAR))

    return result


def special_activity_names(activities: list[dict[str, Any]] | None) -> list[str]:
    """Return the names of today's "special" activities (the calendar ones)."""
    return [
        activity["name"]
        for activity in activities or []
        if activity.get("source") == SOURCE_CALENDAR
    ]


def display_config(config: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Return a compact copy for editing in the UI (flags that are false are omitted)."""
    return {
        section: [
            {key: value for key, value in item.items() if value is not False}
            for item in config.get(section, [])
        ]
        for section in ("fixed", "calendar")
    }
