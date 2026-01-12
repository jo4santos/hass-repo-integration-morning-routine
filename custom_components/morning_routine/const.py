"""Constants for the Morning Routine Gamification integration."""

DOMAIN = "morning_routine"

# Children
CHILDREN = ["duarte", "leonor"]

# Fixed activities (always present in the morning routine)
# Order matters: this is the display order
FIXED_ACTIVITIES = [
    {
        "id": "dressed",
        "name": "Vestir",
        "icon": "mdi:tshirt-crew",
        "camera_required": True,
        "nfc_required": False,
    },
    {
        "id": "breakfast",
        "name": "Pequeno-Almoço",
        "icon": "mdi:food-variant",
        "camera_required": False,
        "nfc_required": False,
    },
    {
        "id": "teeth",
        "name": "Escovar Dentes",
        "icon": "mdi:toothbrush",
        "camera_required": False,
        "nfc_required": False,
    },
    {
        "id": "schoolbag",
        "name": "Mochila",
        "icon": "mdi:bag-personal",
        "camera_required": False,
        "nfc_required": True,
    },
    {
        "id": "lunchbox",
        "name": "Lancheira",
        "icon": "mdi:bag-checked",
        "camera_required": False,
        "nfc_required": True,
    },
]

# Calendar activity mapping (dynamic activities based on calendar events)
# Supports wildcards with fnmatch (e.g., "D-Música*" matches "D-Música Trompete", "D-Música Formação", etc.)
CALENDAR_ACTIVITY_MAPPING = {
    "duarte": [
        {
            "pattern": "D-Música*",  # Matches any music class
            "activity": {
                "id": "music",
                "name": "Música",
                "icon": "mdi:trumpet",
                "nfc_required": True,
            }
        },
        {
            "pattern": "D-Natação",
            "activity": {
                "id": "swimming",
                "name": "Natação",
                "icon": "mdi:swim",
                "nfc_required": True,
            }
        },
        {
            "pattern": "D-Jiu Jitsu",
            "activity": {
                "id": "jiujitsu",
                "name": "Jiu Jitsu",
                "icon": "mdi:karate",
                "nfc_required": True,
            }
        },
    ],
    "leonor": [
        {
            "pattern": "L-Música*",  # Matches all music classes
            "activity": {
                "id": "music",
                "name": "Música",
                "icon": "mdi:flute",
                "nfc_required": True,
            }
        },
        {
            "pattern": "L-Taekwondo",
            "activity": {
                "id": "taekwondo",
                "name": "Taekwondo",
                "icon": "mdi:karate",
                "nfc_required": True,
            }
        },
        {
            "pattern": "L-Ed Física",
            "activity": {
                "id": "physical_education",
                "name": "Ed Física",
                "icon": "mdi:run",
                "nfc_required": True,
            }
        },
    ],
}

# Legacy activity types (kept for NFC mapping compatibility)
# Build complete activity types from both fixed activities and calendar mappings
ACTIVITY_TYPES = {activity["id"]: activity for activity in FIXED_ACTIVITIES}

# Add all possible calendar activities from all children
for child_mappings in CALENDAR_ACTIVITY_MAPPING.values():
    for mapping in child_mappings:
        activity_config = mapping["activity"]
        activity_id = activity_config["id"]
        if activity_id not in ACTIVITY_TYPES:
            ACTIVITY_TYPES[activity_id] = activity_config

# Config keys
CONF_CALENDAR_ENTITY = "calendar_entity"
CONF_RESET_TIME = "reset_time"
CONF_BUSINESS_DAYS_ONLY = "business_days_only"
CONF_NFC_MAPPINGS = "nfc_mappings"
CONF_REWARD_TYPE = "reward_type"
CONF_OPENAI_ENABLED = "openai_enabled"
CONF_OPENAI_CONFIG_ENTRY = "openai_config_entry"
CONF_OPENAI_PROMPT = "openai_prompt"
CONF_YOUTUBE_PLAYLIST_ID = "youtube_playlist_id"

# Defaults
DEFAULT_RESET_TIME = "06:00:00"
DEFAULT_BUSINESS_DAYS_ONLY = True
DEFAULT_REWARD_TYPE = "quote"
DEFAULT_OPENAI_ENABLED = False
DEFAULT_OPENAI_PROMPT = "Uma ilustração divertida e colorida a celebrar {child} completando a rotina matinal. Estilo alegre e encorajador."
DEFAULT_YOUTUBE_PLAYLIST_ID = "PLbtUQWldWkKg3jG-lKeRfD5rEtR0u4sqc"

# Storage keys
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_storage"

# Events
EVENT_ACTIVITY_COMPLETED = f"{DOMAIN}_activity_completed"
EVENT_ROUTINE_COMPLETE = f"{DOMAIN}_routine_complete"
EVENT_RESET = f"{DOMAIN}_reset"

# Platforms
PLATFORMS = ["sensor"]
