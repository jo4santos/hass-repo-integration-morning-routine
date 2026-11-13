# Morning Routine Gamification

A Home Assistant custom integration that gamifies morning routines for children. Track daily activities, capture photos, integrate with NFC tags for bag tracking, sync with Google Calendar, and reward completed routines with AI-generated images.

## Features

- **Dual-child tracking**: Separate routines for Duarte and Leonor
- **Activity tracking**: Getting dressed, breakfast, schoolbag, lunch bag, music instruments, sports bag, teeth brushing
- **Photo capture**: Take photos when getting dressed (via companion card)
- **NFC tag integration**: Scan NFC tags to mark bag activities as complete
- **Google Calendar sync**: Automatically add calendar events as activities
- **AI rewards**: Generate celebration images with OpenAI DALL-E when all activities are complete
- **Reward image from the photo** (v2.13.0+): a cartoon generated from the photo the child takes in the morning, shown when everything is done
- **Streak** (v2.13.0+): consecutive completed routine days per child
- **Daily reset**: Automatically reset activities at configurable time (business days only)
- **State persistence**: Activities survive Home Assistant restarts

## Installation

### Via HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Go to "Integrations"
3. Click the three dots menu (top right)
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/jo4santos/hass-repo-integration-morning-routine`
6. Category: Integration
7. Click "Add"
8. Click "Install" on the Morning Routine Gamification card
9. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/morning_routine` directory to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

### Initial Setup

1. Go to **Settings** → **Devices & Services**
2. Click **+ ADD INTEGRATION**
3. Search for "Morning Routine Gamification"
4. Configure the integration:
   - **Calendar Entity**: (Optional) Select your Google Calendar entity for activity sync
   - **Reset Time**: Time to reset activities daily (default: 06:00:00)
   - **Business Days Only**: Only reset on weekdays Mon-Fri (default: enabled)

### Options

After setup, click "Configure" on the integration card. The options are split in sections:

- **Geral**: calendar entity, reset time, business days only
- **Atividades**: the activities of each child (see below)
- **Anúncios e IA**: voice announcements, house announcement script, AI generated texts, daily phrase
- **Recompensas**: reward type, image generated from the photo (external service), YouTube playlist, AI image reward
- **Google Drive**: upload of photos and audios

Each section only changes its own settings; everything else is kept.

### Activities (v2.12.0+)

The activities of each child are edited in **Atividades**, one YAML field per child:

```yaml
fixed:            # every day, in display order
  - {id: dressed, name: Vestir, icon: mdi:tshirt-crew, camera_required: true}
  - {id: breakfast, name: Pequeno-Almoço, icon: mdi:food-variant}
  - {id: teeth, name: Lavar Dentes e Cara, icon: mdi:toothbrush}
  - {id: schoolbag, name: Coisas, icon: mdi:bag-personal, nfc_required: true}
  - {id: lunchbox, name: Lancheira, icon: mdi:bag-checked, nfc_required: true}
calendar:         # only on days with a matching calendar event
  - {pattern: D-Natação, id: swimming, name: Natação, icon: mdi:swim, nfc_required: true}
  - {pattern: "D-Música*", id: music, name: Música, icon: mdi:music, nfc_required: true}
```

- `id` (required): lowercase letters, digits and `_`, unique per child. NFC tag mappings and services
  refer to activities by `id`, so keep the id when you only want to rename an activity.
- `name` (required), `icon` (optional, must start with `mdi:`),
  `camera_required` / `nfc_required` (optional, default `false`).
- `pattern` (calendar items only, required): matched against the calendar event title, `*` wildcards allowed.
  The first matching pattern wins.
- At least one `fixed` activity is required. Clear a field completely to go back to the built-in defaults
  (the lists in `const.py`), which are also what is used until you edit anything.
- Saving applies immediately: today's progress is kept for activities that still exist, renamed activities
  are updated, new fixed ones are added (not done) and removed ones disappear.
- In announcements, the "special activities of today" are the `calendar` ones.

### Voice announcements, AI texts and the house announcement script (v2.12.0+)

In **Anúncios e IA**:

- **Script de anúncios da casa** (`announce_script`): when set, every announcement is handed to that script
  with `script.turn_on` and `variables: {message: "<text>"}` (not waiting for it to finish), so the script
  decides speakers and volume. When empty (or if the script is unavailable) the integration uses `tts.speak`
  with `tts.home_assistant_cloud` on the configured media player, as before.
- **Gerar o texto dos anúncios com IA** (`ai_announcements_enabled`, off by default) + **Agente de conversação**
  (`ai_agent`): the 30-minute, 10-minute, time-to-leave and routine-complete announcements (and the three
  `announce_time_*` services) are written on the spot by the conversation agent from the facts of the moment
  (minutes left, weather, activities done / missing per child, special activities, daily phrase).
  The style rules are editable (`ai_announcement_prompt`).
- If the agent does not answer within `ai_timeout` seconds (default 8), fails, or returns an empty or overlong
  (> 400 characters) text, the fixed sentence is spoken instead - an announcement is never lost.
  The log says which path was used.
- The daily phrase uses `ai_agent` when no OpenAI configuration is selected. The routine-complete
  announcement no longer needs a daily phrase: without one it just congratulates the child.
  It is made when announcements are enabled and the daily phrase or the AI texts are enabled.

Only routine facts are sent to the agent - never configuration or credentials.

The completion announcement (v2.13.0+) also says how much time is left until school time:
"Parabéns, Duarte! Completaste todas as tarefas. Ainda tens 25 minutos para brincar." with plenty of time,
"Mesmo a tempo de sair!" in the last 3 minutes and "Está na hora de sair!" at or after school time.
The AI agent gets the same information as facts (`minutos_para_escola` and `dica_de_tempo` inside `conclusao`)
and may suggest playing when there is time - nothing else is invented. A prompt customised before v2.13.0 keeps
working: the goal of the announcement, sent with the facts, explains the time hint.

### Reward image generated from the photo (v2.13.0+)

Reward type **Imagem gerada a partir da fotografia** (stored value `ai_image_from_photo`), in *Recompensas*.
The other reward types (`quote`, `youtube_video`, `ai_image`) are unchanged.

How it works:

1. When the child takes the photo (`morning_routine.save_photo`), the photo activity is completed at once and,
   in the background, the photo is sent to the configured image service together with the prompt.
2. The answer is saved next to the photo as `/config/www/morning_routine_photos/<child>_<YYYYMMDD_HHMMSS>_reward.png`
   (`.jpg` / `.webp` when the service answers in those formats) and the sensor gets
   `reward_image` (a `/local/...` URL with a cache-busting query) and `reward_image_status: ready`.
3. When all activities are complete, the companion card shows the image on "Ver Recompensa".
   While it is `generating` the card shows a waiting state; when it `failed` (or there is no photo) the card
   shows the "medalha do dia" (daily phrase and streak), so there is always a reward.

A new photo on the same day generates a new image and replaces the previous one (a generation still running is
cancelled). The daily reset clears the image. `morning_routine.regenerate_reward` runs the generation again
from today's photo. When Google Drive upload is enabled the generated image is uploaded too
(as `<date>_<time>_reward.<ext>`). Reward images are not listed in the photo history.

Options (*Recompensas*):

| Option | Key | Default |
|---|---|---|
| Serviço de imagens: endereço (URL) | `reward_image_api_url` | `https://orchestron.tech/api/v1/ask` |
| Serviço de imagens: token | `reward_image_api_token` | (empty) |
| Serviço de imagens: tópico | `reward_image_api_topic` | `Imagens` |
| Pedido (prompt) da imagem | `reward_image_prompt` | see below |
| Sobre o Duarte / Sobre a Leonor | `reward_image_notes_duarte` / `reward_image_notes_leonor` | (empty) / `tem 8 anos e já sabe ler bem` |
| Tempo máximo de espera pela imagem | `reward_image_timeout` | 180 s (30-600) |
| Texto de confirmação automática (v2.13.1+) | `reward_image_confirm_text` | `Confirmo. Gera já a imagem, sem pedir mais confirmações.` |
| Número de confirmações automáticas (v2.13.1+) | `reward_image_confirm_attempts` | 2 (0-3; 0 disables the follow-up) |

The token is a secret: it is stored in the integration options, is never written to the log and is never shown
again in the form. Leave the field empty to keep the stored token, type a new one to replace it, or tick
"Apagar o token guardado" to erase it.

Request sent to the service (the answer must carry the image, base64, at `media[0].data`; a text at `answer`
or `error` instead means no image):

```
POST <reward_image_api_url>
Authorization: Bearer <token>
Content-Type: application/json

{"topic": "Imagens", "text": "<prompt>", "media": [{"mime": "image/jpeg", "data": "<base64 of the photo>"}]}
```

Answers larger than 15 MB or that are not a PNG, JPEG or WebP image are rejected.

#### Automatic confirmation follow-up (v2.13.1+)

The service may answer the first request with a text only - it plans the image and waits for an approval
turn (for example "A banda desenhada ainda não foi gerada. Ficou à espera da tua aprovação..."). When an answer
is HTTP 200, has no usable `media[0].data`, has no `error` and has a text at `answer`, the integration sends a
confirmation follow-up to the same URL, with the same token and topic and **without the photo**:

```
{"topic": "Imagens", "text": "Confirmo. Gera já a imagem, sem pedir mais confirmações."}
```

plus the conversation identifiers of the previous answer, when it has any. This is a heuristic, because the
way the service links the two turns is not known (it may simply be the topic): every top-level key of the
previous answer named `conversation_id`, `conversationId`, `thread_id`, `threadId`, `session_id`, `sessionId`,
`chat_id`, `chatId`, `context_id`, `id`, `message_id`, `messageId` or `reply_to` whose value is a text or a
number is echoed back unchanged, under the same name. Nothing else of the answer is sent back.

- Up to `reward_image_confirm_attempts` follow-ups are sent (default 2), one after each text only answer;
  `0` disables the follow-up (behaviour of v2.13.0).
- Each follow-up has the same timeout as the first request (`reward_image_timeout`), so in the worst case the
  generation takes (1 + attempts) x timeout. The status stays `generating` until the end, which is always
  `ready` or `failed`.
- An `error` in any answer stops at once. When at least one follow-up was sent and the service still answered
  with a text or an error, `reward_image_error` also says
  "O serviço de imagens ficou à espera de aprovação e não devolveu a imagem.".

Diagnostics: for every answer the log gets (INFO) the HTTP status, the top-level key names of the JSON,
whether media was present and how many items, and the first 200 characters of the answer text. The final
failure (WARNING) says how many confirmation follow-ups were tried. The token, the photo and the media data
are never logged. To see the INFO lines:

```yaml
logger:
  logs:
    custom_components.morning_routine: info
```

Default prompt (placeholders: `{name}`, `{child_noun}` = "um menino" / "uma menina", `{notes}`, `{notes_clause}`,
`{activities}`, `{activities_clause}` = today's special (calendar) activities, `{weekday}`; unknown placeholders
are left as they are):

> Gera uma imagem engraçada em estilo cartoon com uma personagem baseada nesta fotografia. Deve ser divertida
> para {child_noun} ver ({name}{notes_clause}). {activities_clause}A roupa que a criança está a usar na
> fotografia deve estar parecida no cartoon. Pode incluir uma pequena história inventada com pouco texto.
> Atenção ao texto na imagem: tem de estar em português de Portugal perfeito. Gera já a imagem nesta resposta,
> sem pedir confirmação.

**Privacy**: with this reward type the child's photo leaves the house - it is sent to the configured external
service every time a photo is taken. Nothing is sent with the other reward types or without a token.

### Streak (v2.13.0+)

Each child has a streak of consecutive completed routine days (`streak` and `best_streak` sensor attributes).
A day counts once, when the last activity is completed. With "business days only" the weekends are not routine
days and never break the streak; a routine day (holidays included) that ends without a completed routine resets
the streak at the next daily reset. Un-ticking an activity on the same day takes the day back.

### Tests

The configuration / announcement / reward logic is tested without Home Assistant (and without network):

```bash
python3 -m unittest discover -s tests -v
```

## Entities Created

### Sensors

- `sensor.duarte_morning_status` - Duarte's completion percentage (0-100%)
- `sensor.leonor_morning_status` - Leonor's completion percentage (0-100%)

Each sensor includes attributes:
- `activities`: List of all activities with completion status
- `last_activity_time`: Timestamp of last completed activity
- `photo_path`: Path to captured photo
- `reward_type`: Configured reward type (`quote`, `youtube_video`, `ai_image`, `ai_image_from_photo`)
- `reward_image`: Reward image - a `/local/...` URL for `ai_image_from_photo`, a file path for `ai_image`
- `reward_image_status`: `generating`, `ready` or `failed` (`ai_image_from_photo` only, else empty)
- `reward_image_error`: Short reason when the image failed
- `daily_phrase`: Today's phrase (when daily phrases are enabled)
- `streak` / `best_streak`: Consecutive completed routine days, now and best ever
- `all_complete`: Boolean indicating if all activities are complete
- `progress`: Completion percentage

## Services

### `morning_routine.complete_activity`

Manually mark an activity as complete.

```yaml
service: morning_routine.complete_activity
data:
  child: duarte
  activity: breakfast
```

**Parameters:**
- `child`: `duarte` or `leonor`
- `activity`: Activity ID as configured for that child in the options (defaults: `dressed`, `breakfast`, `teeth`, `schoolbag`, `lunchbox`, plus the calendar ones such as `swimming`, `music`, `taekwondo`). Unknown ids are rejected.

### `morning_routine.save_photo`

Save a photo from camera capture (used by companion card).

```yaml
service: morning_routine.save_photo
data:
  child: duarte
  photo_data: <base64_encoded_jpeg>
```

### `morning_routine.reset_routine`

Manually reset activities for a child or all children.

```yaml
service: morning_routine.reset_routine
data:
  child: all  # or "duarte" / "leonor"
```

### `morning_routine.regenerate_reward`

Regenerate the reward of a child. With the reward type "Imagem gerada a partir da fotografia" the image is
generated again from today's photo (error if there is no photo yet); with "Imagem gerada por IA" it requires
OpenAI enabled.

```yaml
service: morning_routine.regenerate_reward
data:
  child: duarte
```

## NFC Tag Setup

1. Configure NFC tag mappings via integration options
2. Scan an NFC tag with Home Assistant Companion app
3. Map the tag to a child and activity (e.g., Duarte → Schoolbag)
4. When the tag is scanned, the activity is automatically marked complete

**Recommended Tags:**
- Schoolbag → `schoolbag` activity
- Lunch bag → `lunchbag` activity
- Music instrument case → `music_instrument` activity
- Sports bag (taekwondo/swimming) → `sports_bag` activity

## Google Calendar Integration

The integration can sync activities from your Google Calendar:

1. Create events with child names in the title:
   - "Duarte - Taekwondo Class"
   - "Leonor Swimming Lesson"
   - "Duarte Music Practice"

2. The integration will parse event names and add corresponding activities

3. Activities are synced once daily at reset time

## OpenAI DALL-E Rewards

When enabled, the integration generates a fun, kid-friendly cartoon image when a child completes all activities:

1. Enable in integration options
2. (Optional) Customize the prompt template
3. When all activities complete, an image is automatically generated and stored
4. View the reward image in the sensor attributes or companion card

**Cost**: ~$0.04 per image (OpenAI pricing as of 2026)

## Requirements

- Home Assistant 2024.11.0 or newer
- (Optional) Google Calendar integration for activity sync
- (Optional) OpenAI Conversation integration for AI rewards
- (Optional) Home Assistant Companion app for NFC tag scanning
- Companion Morning Routine Card for full UI experience

## Companion Card

This integration works best with the **Morning Routine Card** (separate installation):
- Visual progress rings
- Activity grid with icons
- Camera capture for "getting dressed"
- Reward image display

Install from: `https://github.com/jo4santos/hass-repo-card-morning-routine`

## Troubleshooting

### Sensors not updating

- Check that the integration is properly configured
- Verify entities exist in **Developer Tools** → **States**
- Check logs for errors: **Settings** → **System** → **Logs**

### NFC tags not working

- Ensure Home Assistant Companion app is installed on your device
- Verify NFC is enabled in device settings
- Check that tags are properly mapped in integration options
- Test tag scanning in **Developer Tools** → **Events** → Listen to `tag_scanned`

### AI rewards not generating

- Verify OpenAI Conversation integration is set up
- Check that "Enable AI Rewards" is enabled in options
- Ensure you have OpenAI API credits
- Check logs for API errors

### Photos not saving

- Verify `/config/morning_routine_photos/` directory exists and is writable
- Check available disk space
- Ensure photo data is properly base64 encoded

## Example Automations

### Daily Reminder

```yaml
automation:
  - alias: "Morning Routine Reminder"
    trigger:
      - platform: time
        at: "07:00:00"
    condition:
      - condition: time
        weekday:
          - mon
          - tue
          - wed
          - thu
          - fri
      - condition: numeric_state
        entity_id: sensor.duarte_morning_status
        below: 100
    action:
      - service: notify.mobile_app_duarte_tablet
        data:
          message: "Don't forget to complete your morning routine!"
```

### Celebration on Completion

```yaml
automation:
  - alias: "Morning Routine Complete Celebration"
    trigger:
      - platform: event
        event_type: morning_routine_routine_complete
    action:
      - service: notify.mobile_app_parent_phone
        data:
          message: "{{ trigger.event.data.child | capitalize }} completed their morning routine! 🎉"
      - service: media_player.play_media
        target:
          entity_id: media_player.home_speaker
        data:
          media_content_id: "https://example.com/celebration.mp3"
          media_content_type: "music"
```

## Privacy & Data

- All photos are stored locally in `/config/morning_routine_photos/`
- No data is sent to external services except:
  - Google Calendar API (if calendar sync enabled)
  - OpenAI API (if AI rewards enabled)
  - The configured image service (only with the reward type "Imagem gerada a partir da fotografia"):
    **the child's photo is sent to it** to generate the reward image
- NFC tag IDs are stored locally in integration config
- Activity completion data is persisted in Home Assistant storage

## Support

For issues, feature requests, or questions:
- GitHub Issues: https://github.com/jo4santos/hass-repo-integration-morning-routine/issues
- Community Forum: https://community.home-assistant.io/

## License

Apache License 2.0

## Credits

Developed by [@jo4santos](https://github.com/jo4santos) for Duarte and Leonor's morning adventures! 🌅
