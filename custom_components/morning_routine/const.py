"""Constants for the Morning Routine Gamification integration."""

DOMAIN = "morning_routine"

# Children
CHILDREN = ["duarte", "leonor"]

# Children configuration (gender for correct grammar)
CHILDREN_CONFIG = {
    "duarte": {
        "name": "Duarte",
        "gender": "m",  # masculine
        "article": "O",  # O Duarte
        "pronoun_ready": "pronto",  # está pronto
    },
    "leonor": {
        "name": "Leonor",
        "gender": "f",  # feminine
        "article": "A",  # A Leonor
        "pronoun_ready": "pronta",  # está pronta
    },
}

# Built-in activity DEFAULTS.
# Since v2.12.0 the activities of each child can be edited in the integration
# options ("Atividades"); the constants below are only the defaults used while
# a child has no "activities_<child>" option (see activities.py).

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
        "name": "Lavar Dentes e Cara",
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

# Per-child overrides of fixed activities (same id, different label/icon for one child)
CHILD_ACTIVITY_OVERRIDES = {
    "duarte": {
        "schoolbag": {"name": "Coisas"},
    },
}


def get_activity_config(child: str | None, activity_id: str) -> dict:
    """Return the activity config for a child, with that child's overrides applied."""
    base = dict(ACTIVITY_TYPES.get(activity_id, {}))
    base.update(CHILD_ACTIVITY_OVERRIDES.get(child or "", {}).get(activity_id, {}))
    return base


# Calendar activity mapping (dynamic activities based on calendar events)
# Supports wildcards with fnmatch (e.g., "D-Música*" matches "D-Música Trompete", "D-Música Formação", etc.)
CALENDAR_ACTIVITY_MAPPING = {
    "duarte": [
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
            "pattern": "D-Taekwondo",
            "activity": {
                "id": "taekwondo",
                "name": "Taekwondo",
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
                "icon": "mdi:music",
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
CONF_ANNOUNCEMENTS_ENABLED = "announcements_enabled"
CONF_MEDIA_PLAYER_ENTITY = "media_player_entity"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_SCHOOL_TIME = "school_time"
CONF_DAILY_PHRASE_ENABLED = "daily_phrase_enabled"
CONF_DAILY_PHRASE_PROMPT = "daily_phrase_prompt"
CONF_GDRIVE_ENABLED = "gdrive_enabled"
CONF_GDRIVE_CLIENT_ID = "gdrive_client_id"
CONF_GDRIVE_CLIENT_SECRET = "gdrive_client_secret"
CONF_GDRIVE_FOLDER_ID = "gdrive_folder_id"
# Per-child activities: options key "activities_<child>" (see activities.conf_activities_key)
CONF_AI_AGENT = "ai_agent"
CONF_AI_ANNOUNCEMENTS_ENABLED = "ai_announcements_enabled"
CONF_AI_ANNOUNCEMENT_PROMPT = "ai_announcement_prompt"
CONF_AI_TIMEOUT = "ai_timeout"
CONF_ANNOUNCE_SCRIPT = "announce_script"
# Reward image generated from the child's photo by an external service (v2.13.0)
CONF_REWARD_IMAGE_API_URL = "reward_image_api_url"
CONF_REWARD_IMAGE_API_TOKEN = "reward_image_api_token"
CONF_REWARD_IMAGE_API_TOPIC = "reward_image_api_topic"
CONF_REWARD_IMAGE_PROMPT = "reward_image_prompt"
CONF_REWARD_IMAGE_TIMEOUT = "reward_image_timeout"
# Per-child notes used in the prompt: options key "reward_image_notes_<child>"
CONF_REWARD_IMAGE_NOTES_PREFIX = "reward_image_notes_"


def conf_reward_image_notes_key(child: str) -> str:
    """Return the options key of a child's notes for the reward image prompt."""
    return f"{CONF_REWARD_IMAGE_NOTES_PREFIX}{child}"


# Reward types (stored values; the first three exist since the early versions)
REWARD_TYPE_QUOTE = "quote"
REWARD_TYPE_YOUTUBE_VIDEO = "youtube_video"
REWARD_TYPE_AI_IMAGE = "ai_image"
REWARD_TYPE_AI_IMAGE_FROM_PHOTO = "ai_image_from_photo"
REWARD_TYPES = [
    REWARD_TYPE_QUOTE,
    REWARD_TYPE_YOUTUBE_VIDEO,
    REWARD_TYPE_AI_IMAGE,
    REWARD_TYPE_AI_IMAGE_FROM_PHOTO,
]

# Defaults
DEFAULT_RESET_TIME = "06:00:00"
DEFAULT_BUSINESS_DAYS_ONLY = True
DEFAULT_REWARD_TYPE = "quote"
DEFAULT_OPENAI_ENABLED = False
DEFAULT_OPENAI_PROMPT = "Uma ilustração divertida e colorida a celebrar {child} completando a rotina matinal. Estilo alegre e encorajador."
DEFAULT_YOUTUBE_PLAYLIST_ID = "PLbtUQWldWkKg3jG-lKeRfD5rEtR0u4sqc"
DEFAULT_ANNOUNCEMENTS_ENABLED = False
DEFAULT_SCHOOL_TIME = "08:50:00"
DEFAULT_DAILY_PHRASE_ENABLED = False
DEFAULT_DAILY_PHRASE_PROMPT = "Gera uma frase curta e inspiradora para {child} (7-10 anos) para começar o dia. Pode ser uma piada leve, uma motivação ou algo alegre. Máximo 2 frases curtas em português de Portugal."
DEFAULT_GDRIVE_ENABLED = False
DEFAULT_AI_ANNOUNCEMENTS_ENABLED = False
DEFAULT_AI_TIMEOUT = 8
DEFAULT_AI_ANNOUNCEMENT_PROMPT = (
    "És a voz da casa e vais fazer um anúncio da rotina da manhã, dito em voz alta "
    "nas colunas da casa, para crianças entre os 7 e os 10 anos.\n"
    "Regras:\n"
    "- Escreve em português de Portugal (nunca do Brasil), num tom caloroso, alegre e natural.\n"
    "- Sê breve: no máximo 2 a 3 frases curtas (cerca de 35 palavras). Nos avisos de "
    "10 minutos e de hora de sair usa apenas 1 frase.\n"
    "- Diz sempre o facto essencial do anúncio (por exemplo quantos minutos faltam, "
    "que está na hora de sair, ou quem terminou a rotina).\n"
    "- Menciona as atividades em falta de cada criança só quando for útil, pelo nome e sem ralhar.\n"
    "- Se os factos tiverem previsão do tempo e o anúncio a pedir, resume-a e sugere a roupa "
    "ou o guarda-chuva.\n"
    "- No anúncio de rotina completa dá os parabéns à criança pelo nome e diz o tempo que ainda "
    "tem, seguindo a dica_de_tempo dos factos: se ainda há tempo, diz quantos minutos faltam e "
    "podes sugerir que aproveite para brincar; se não, diz que foi mesmo a tempo de sair ou que "
    "está na hora de sair. Não sugiras mais nada.\n"
    "- Se existir frase do dia, diz-a no fim tal como está.\n"
    "- Varia as palavras de dia para dia; não repitas sempre a mesma fórmula.\n"
    "- Escreve os números por extenso para serem bem lidos em voz alta (dez minutos, dezoito graus).\n"
    "- Não uses emojis, markdown, listas, aspas nem títulos. Responde só com o texto a dizer.\n"
    "- Usa apenas os factos do JSON abaixo; nunca inventes nada que lá não esteja."
)
DEFAULT_REWARD_IMAGE_API_URL = "https://orchestron.tech/api/v1/ask"
DEFAULT_REWARD_IMAGE_API_TOPIC = "Imagens"
DEFAULT_REWARD_IMAGE_TIMEOUT = 180
MIN_REWARD_IMAGE_TIMEOUT = 30
MAX_REWARD_IMAGE_TIMEOUT = 600
DEFAULT_REWARD_IMAGE_NOTES = {
    "duarte": "",
    "leonor": "tem 8 anos e já sabe ler bem",
}
DEFAULT_REWARD_IMAGE_PROMPT = (
    "Gera uma imagem engraçada em estilo cartoon com uma personagem baseada nesta fotografia. "
    "Deve ser divertida para {child_noun} ver ({name}{notes_clause}). "
    "{activities_clause}"
    "A roupa que a criança está a usar na fotografia deve estar parecida no cartoon. "
    "Pode incluir uma pequena história inventada com pouco texto. "
    "Atenção ao texto na imagem: tem de estar em português de Portugal perfeito. "
    "Gera já a imagem nesta resposta, sem pedir confirmação."
)
# Timeout (seconds) when asking the agent for the daily phrases (not time critical)
DAILY_PHRASE_TIMEOUT = 30

# Storage keys
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_storage"

# Events
EVENT_ACTIVITY_COMPLETED = f"{DOMAIN}_activity_completed"
EVENT_ROUTINE_COMPLETE = f"{DOMAIN}_routine_complete"
EVENT_RESET = f"{DOMAIN}_reset"

# Platforms
PLATFORMS = ["sensor"]
