# Morning Routine Integration - Quick Start Guide

## Installation

1. **Copy integration files:**
   ```bash
   cp -r /Users/josesantos/dev/hass-repo/integrations/morning_routine/custom_components/morning_routine /config/custom_components/
   ```

2. **Restart Home Assistant**

3. **Add the integration:**
   - Go to **Settings** → **Devices & Services**
   - Click **+ ADD INTEGRATION**
   - Search for "Morning Routine Gamification"
   - Click to add

## Initial Configuration

### Step 1: Basic Setup

1. **Calendar Entity** (optional): Select your Google Calendar entity (e.g., `calendar.family`)
2. **Reset Time**: Set when activities should reset daily (default: 06:00:00)
3. **Business Days Only**: Enable to only reset Mon-Fri (recommended: ON)
4. Click **Submit**

### Step 2: Verify Entities Created

Check that these sensors exist:
- `sensor.duarte_morning_status`
- `sensor.leonor_morning_status`

Go to **Developer Tools** → **States** to verify.

## Setting Up NFC Tags

### Map an NFC Tag (Service-Based)

1. **Call the add_nfc_mapping service:**
   - Go to **Developer Tools** → **Services**
   - Select `morning_routine.add_nfc_mapping`
   - Fill in:
     - **Child**: `duarte` or `leonor`
     - **Activity**: Choose from dropdown (schoolbag, lunchbag, music_instrument, sports_bag)
     - **Timeout**: 30 seconds (default)
   - Click **Call Service**

2. **Scan the NFC tag within 30 seconds**
   - Use Home Assistant Companion app on your phone/tablet
   - Tap the NFC tag
   - You'll see a notification: "✅ NFC tag mapped successfully!"

3. **Repeat for each bag:**
   - Duarte's schoolbag
   - Duarte's lunch bag
   - Duarte's music instruments
   - Duarte's sports bag (taekwondo/swimming)
   - Leonor's schoolbag
   - Leonor's lunch bag
   - Leonor's music instruments
   - Leonor's sports bag

### List Current Mappings

```yaml
service: morning_routine.list_nfc_mappings
```

Response will show all mapped tags.

### Remove a Mapping

```yaml
service: morning_routine.remove_nfc_mapping
data:
  tag_id: "04:AB:CD:EF:12:34:80"  # Replace with actual tag ID
```

## Installing the Card

1. **Copy card file:**
   ```bash
   cp /Users/josesantos/dev/hass-repo/cards/morning_routine_card/morning-routine-card.js /config/www/
   ```

2. **Add as resource:**
   - Go to **Settings** → **Dashboards** → **Resources** (top right menu)
   - Click **+ ADD RESOURCE**
   - URL: `/local/morning-routine-card.js`
   - Resource type: **JavaScript Module**
   - Click **Create**

3. **Add card to dashboard:**
   - Edit any dashboard
   - Click **+ ADD CARD**
   - Scroll down to **Custom: Morning Routine Card**
   - Or manually add:

```yaml
type: custom:morning-routine-card
children:
  - entity: sensor.duarte_morning_status
    name: Duarte
    color: "#4CAF50"
  - entity: sensor.leonor_morning_status
    name: Leonor
    color: "#2196F3"
layout: horizontal
show_progress: true
```

4. **Hard refresh browser:** Ctrl+F5 (Windows/Linux) or Cmd+Shift+R (Mac)

## Testing the System

### Test 1: Manual Activity Completion

```yaml
service: morning_routine.complete_activity
data:
  child: duarte
  activity: breakfast
```

Sensor `sensor.duarte_morning_status` should update from 0% to ~14% (1 of 7 activities).

### Test 2: Camera Photo Capture

1. Open the card on a device with HTTPS enabled
2. Click the "Getting Dressed" activity tile
3. Camera modal should open
4. Allow camera permissions (first time)
5. Take a photo
6. Photo saves to `/config/morning_routine_photos/duarte_YYYYMMDD_HHMMSS.jpg`
7. Activity marked complete

### Test 3: NFC Tag Scan

1. Map an NFC tag to Duarte/schoolbag (see above)
2. Scan the tag with Home Assistant Companion app
3. Check sensor - schoolbag activity should be marked complete

### Test 4: Reset Routine

**Manual reset:**
```yaml
service: morning_routine.reset_routine
data:
  child: all
```

All activities reset to incomplete, progress goes to 0%.

**Automatic reset:**
- Wait until configured reset time (default 6:00 AM)
- Check sensors - should reset automatically on business days

### Test 5: Calendar Sync

1. **Create calendar event:**
   - Event title: "Duarte - Taekwondo Class"
   - Date: Today
   - Save in configured calendar

2. **Trigger manual reset:**
   ```yaml
   service: morning_routine.reset_routine
   data:
     child: all
   ```

3. **Check sensor attributes:**
   - `sensor.duarte_morning_status` attributes should include a new activity:
     - Name: "Taekwondo Bag"
     - Icon: "mdi:karate"
     - Source: "calendar"

4. **Map NFC tag to calendar activity:**
   - Note the activity ID from attributes (e.g., `calendar_sports_bag_...`)
   - You can manually complete it via service or NFC tag

### Test 6: AI Reward (Optional)

1. **Enable OpenAI in integration options:**
   - Go to integration settings → **Configure**
   - Enable "Enable AI-generated rewards"
   - Click **Submit**

2. **Complete all activities for one child:**
   ```yaml
   service: morning_routine.complete_activity
   data:
     child: duarte
     activity: dressed
   ---
   service: morning_routine.complete_activity
   data:
     child: duarte
     activity: breakfast
   # ... repeat for all 7 activities
   ```

3. **Check sensor:**
   - `sensor.duarte_morning_status` should show `all_complete: true`
   - `reward_image` attribute should have a path
   - Image saved to `/config/morning_routine_photos/duarte_reward_YYYYMMDD_HHMMSS.png`

4. **View in card:**
   - Refresh card
   - Should show reward image with celebration message

## Troubleshooting

### Entities not appearing

```bash
# Check logs
grep morning_routine /config/home-assistant.log

# Reload integration
# Settings → Devices & Services → Morning Routine → Reload
```

### Card not loading

1. **Check browser console for errors:** F12 → Console tab
2. **Verify resource added:** Settings → Dashboards → Resources
3. **Hard refresh:** Ctrl+Shift+R
4. **Check file exists:** `/config/www/morning-routine-card.js`

### NFC tags not working

1. **Check Companion app permissions:** Allow NFC in app settings
2. **List mappings:** Call `morning_routine.list_nfc_mappings` service
3. **Check logs:** Look for "NFC tag scanned" messages
4. **Re-map tag:** Remove old mapping, add new one

### Calendar not syncing

1. **Verify calendar entity exists:** Developer Tools → States → search for `calendar.`
2. **Check event format:** Title must contain child name (case-insensitive)
3. **Trigger manual reset:** This forces a calendar sync
4. **Check logs:** Look for "Found X calendar events" message

### Camera not working

1. **Verify HTTPS:** Camera requires secure connection
   - Use Nabu Casa Cloud, or
   - Set up Let's Encrypt certificate, or
   - Access via localhost for testing
2. **Check browser permissions:** Allow camera in browser settings
3. **Try different browser:** Chrome/Safari recommended

### Photos not saving

1. **Check directory:** `/config/morning_routine_photos/` should exist
2. **Check permissions:** Directory must be writable
3. **Check disk space:** Ensure sufficient space available
4. **Check logs:** Look for "Saved photo" or error messages

## Daily Usage Flow

### Morning Routine

1. **Kids wake up** → Check tablet for their morning routine card
2. **Get dressed** → Click activity, take photo with camera
3. **Eat breakfast** → Parent clicks activity or kid clicks on card
4. **Pack bags:**
   - Grab schoolbag → Scan NFC tag → Activity auto-completes
   - Grab lunch bag → Scan NFC tag → Activity auto-completes
   - Check calendar for today's activities (swimming/taekwondo/music)
   - Grab relevant bag → Scan NFC tag → Activity completes
5. **Brush teeth** → Click activity when done
6. **All complete!** → AI reward image appears with celebration 🎉
7. **Next morning** → Activities auto-reset at 6:00 AM (weekdays only)

## Advanced Configuration

### Change Reset Time

1. Go to integration settings → **Configure**
2. Change **Reset Time** to desired time (e.g., 07:00:00)
3. Click **Submit**

### Customize AI Prompt

1. Go to integration settings → **Configure**
2. Edit **Custom prompt template**
3. Use `{child}` as placeholder for child's name
4. Example: "A superhero version of {child} celebrating morning routine victory"

### Add More Calendar Keywords

Currently supported keywords in calendar events:
- taekwondo, karate → Sports Bag (karate icon)
- swimming, swim, pool → Sports Bag (swim icon)
- music, piano, guitar, violin → Music Instruments
- dance → Dance Bag
- football, soccer, basketball → Sports Gear

To add more, edit the `keywords` dictionary in `__init__.py` → `_parse_calendar_events` method.

## Support

- Check logs: Settings → System → Logs
- GitHub Issues: https://github.com/jo4santos/hass-repo-integration-morning-routine/issues
- Community Forum: https://community.home-assistant.io/

## Next Steps

1. **Label NFC tags:** Use a label maker to identify which tag is for which bag
2. **Create automations:** Send notifications when kids complete their routines
3. **Track progress:** Use history graphs to see completion trends over time
4. **Reward system:** Link routine completion to real-world rewards (screen time, treats, etc.)

Happy morning routines! 🌅
