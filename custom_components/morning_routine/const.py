"""Constants for the Morning Routine Gamification integration."""

DOMAIN = "morning_routine"

# Children
CHILDREN = ["duarte", "leonor"]

# Activity types (base set - extended by calendar)
ACTIVITY_TYPES = {
    "dressed": {"icon": "mdi:tshirt-crew", "name": "Vestir", "camera_required": True, "nfc_required": False},
    "breakfast": {"icon": "mdi:food-apple", "name": "Pequeno-Almoço", "camera_required": False, "nfc_required": False},
    "schoolbag": {"icon": "mdi:bag-personal", "name": "Mochila da Escola", "camera_required": False, "nfc_required": True},
    "lunchbag": {"icon": "mdi:food", "name": "Saco do Almoço", "camera_required": False, "nfc_required": True},
    "music_instrument": {"icon": "mdi:music", "name": "Instrumento Musical", "camera_required": False, "nfc_required": True},
    "sports_bag": {"icon": "mdi:karate", "name": "Saco de Desporto", "camera_required": False, "nfc_required": True},
    "teeth": {"icon": "mdi:tooth", "name": "Lavar os Dentes", "camera_required": False, "nfc_required": False},
}

# Instrument-specific icons
INSTRUMENT_ICONS = {
    "duarte": "mdi:trumpet",  # Trompete
    "leonor": "mdi:flute",    # Flauta transversal
}

# Config keys
CONF_CALENDAR_ENTITY = "calendar_entity"
CONF_RESET_TIME = "reset_time"
CONF_BUSINESS_DAYS_ONLY = "business_days_only"
CONF_NFC_MAPPINGS = "nfc_mappings"
CONF_OPENAI_ENABLED = "openai_enabled"
CONF_OPENAI_PROMPT = "openai_prompt"

# Defaults
DEFAULT_RESET_TIME = "06:00:00"
DEFAULT_BUSINESS_DAYS_ONLY = True
DEFAULT_OPENAI_ENABLED = False
DEFAULT_OPENAI_PROMPT = "Uma ilustração divertida e colorida a celebrar {child} completando a rotina matinal. Estilo alegre e encorajador."

# Storage keys
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_storage"

# Events
EVENT_ACTIVITY_COMPLETED = f"{DOMAIN}_activity_completed"
EVENT_ROUTINE_COMPLETE = f"{DOMAIN}_routine_complete"
EVENT_RESET = f"{DOMAIN}_reset"

# Platforms
PLATFORMS = ["sensor"]
