# Morning Routine Gamification

A Home Assistant custom integration that gamifies morning routines for children. Track daily activities, capture photos, integrate with NFC tags for bag tracking, sync with Google Calendar, and reward completed routines with AI-generated images.

## Features

- **Dual-child tracking**: Separate routines for Duarte and Leonor
- **Activity tracking**: Getting dressed, breakfast, schoolbag, lunch bag, music instruments, sports bag, teeth brushing
- **Photo capture**: Take photos when getting dressed (via companion card)
- **NFC tag integration**: Scan NFC tags to mark bag activities as complete
- **Google Calendar sync**: Automatically add calendar events as activities
- **AI rewards**: Generate celebration images with OpenAI DALL-E when all activities are complete
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
- **Recompensas**: reward type, YouTube playlist, AI image reward
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

### Tests

The configuration / announcement logic is tested without Home Assistant:

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
- `reward_image`: Path to AI-generated reward image
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

Regenerate AI reward image for a child (requires OpenAI enabled).

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
