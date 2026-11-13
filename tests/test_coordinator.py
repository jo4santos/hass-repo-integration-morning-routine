"""Tests of the coordinator's announcement / options logic with Home Assistant stubbed.

Home Assistant is not needed: the few modules the integration imports are
replaced by minimal fakes, so this only checks the integration's own logic
(which service is called with what), not Home Assistant itself.

Run from the repository root with:  python3 -m unittest discover -s tests -v
"""
import asyncio
import copy
import importlib.util
import logging
import os
import sys
import types
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _loader import PACKAGE, PACKAGE_DIR, load_package  # noqa: E402


class _State:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class _States:
    def __init__(self):
        self.data = {}

    def get(self, entity_id):
        return self.data.get(entity_id)

    def async_entity_ids(self, domain=None):
        return [e for e in self.data if domain is None or e.startswith(f"{domain}.")]


class _Services:
    def __init__(self):
        self.calls = []
        self.handlers = {}

    async def async_call(self, domain, service, data=None, blocking=False, return_response=False):
        self.calls.append((domain, service, copy.deepcopy(data), blocking))
        handler = self.handlers.get((domain, service))
        if handler is None:
            return None
        result = handler(data)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def of(self, domain, service):
        return [call for call in self.calls if call[:2] == (domain, service)]


class _Bus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data=None):
        self.events.append((event_type, data))

    def async_listen(self, event_type, callback):
        return lambda: None


class _Hass:
    def __init__(self):
        self.states = _States()
        self.services = _Services()
        self.bus = _Bus()
        self.data = {}
        self.tasks = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


class _Entry:
    entry_id = "test"

    def __init__(self, options=None, data=None):
        self.options = options or {}
        self.data = data or {}


def _install_stubs():
    """Register minimal fakes for the Home Assistant modules the integration imports."""
    if "homeassistant" in sys.modules and not getattr(sys.modules["homeassistant"], "_is_stub", False):
        return  # a real Home Assistant is installed: use it

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    class ServiceValidationError(Exception):
        pass

    class Store:
        def __init__(self, hass, version, key):
            self.saved = None

        async def async_load(self):
            return self.saved

        async def async_save(self, data):
            self.saved = copy.deepcopy(data)

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name=None, update_interval=None, **kwargs):
            self.hass = hass
            self.data = None
            self.updates = 0

        def async_set_updated_data(self, data):
            self.data = data
            self.updates += 1

        async def async_config_entry_first_refresh(self):
            return None

    class _Schema:
        def __init__(self, schema=None, **kwargs):
            self.schema = schema

        def __call__(self, value):
            return value

    def _marker(key, default=None, **kwargs):
        return key

    dt = module(
        "homeassistant.util.dt",
        now=lambda: datetime(2026, 11, 13, 8, 20, tzinfo=timezone.utc),
        utcnow=lambda: datetime(2026, 11, 13, 8, 20, tzinfo=timezone.utc),
        parse_datetime=datetime.fromisoformat,
        as_local=lambda value: value,
        start_of_local_day=lambda: datetime(2026, 11, 13, tzinfo=timezone.utc),
    )
    module("homeassistant", _is_stub=True)
    module("homeassistant.config_entries", ConfigEntry=_Entry)
    module("homeassistant.core", HomeAssistant=_Hass, Event=object, ServiceCall=object)
    module("homeassistant.exceptions", ServiceValidationError=ServiceValidationError)
    module("homeassistant.helpers")
    module("homeassistant.helpers.storage", Store=Store)
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator)
    module(
        "homeassistant.helpers.event",
        async_track_time_change=lambda hass, action, **kwargs: (lambda: None),
        async_call_later=lambda hass, delay, action: (lambda: None),
    )
    module("homeassistant.helpers.config_validation", string=str, boolean=bool, positive_int=int)
    module("homeassistant.util", dt=dt)
    module("homeassistant.components", persistent_notification=module(
        "homeassistant.components.persistent_notification",
        async_create=lambda *args, **kwargs: None,
    ))
    if "voluptuous" not in sys.modules:
        try:
            import voluptuous  # noqa: F401
        except ImportError:
            module("voluptuous", Schema=_Schema, Required=_marker, Optional=_marker)


def _load_integration():
    """Import the integration's __init__ (with stubs) next to the pure modules."""
    _install_stubs()
    load_package()
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}._integration", os.path.join(PACKAGE_DIR, "__init__.py")
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = PACKAGE
    spec.loader.exec_module(mod)
    return mod


integration = _load_integration()
const = sys.modules[f"{PACKAGE}.const"]

def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


AGENT = "conversation.test_agent"
SCRIPT = "script.notificar_audio"
PLAYER = "media_player.cozinha"


def _answer(text, response_type="action_done"):
    return {
        "response": {
            "speech": {"plain": {"speech": text, "extra_data": None}},
            "card": {},
            "language": "pt",
            "response_type": response_type,
            "data": {},
        },
        "conversation_id": "01ABC",
        "continue_conversation": False,
    }


class CoordinatorTestCase(unittest.IsolatedAsyncioTestCase):
    BASE_OPTIONS = {
        const.CONF_ANNOUNCEMENTS_ENABLED: True,
        const.CONF_MEDIA_PLAYER_ENTITY: PLAYER,
        const.CONF_SCHOOL_TIME: "08:50:00",
        const.CONF_DAILY_PHRASE_ENABLED: True,
        # must never reach the agent or the logs
        const.CONF_GDRIVE_CLIENT_ID: "CLIENT-ID-VALUE",
        const.CONF_GDRIVE_CLIENT_SECRET: "CLIENT-SECRET-VALUE",
    }

    async def make(self, **options):
        hass = _Hass()
        hass.states.data[SCRIPT] = _State("off")
        entry = _Entry({**self.BASE_OPTIONS, **options})
        coordinator = integration.MorningRoutineCoordinator(hass, entry)
        await coordinator._load_data()
        return hass, entry, coordinator

    @staticmethod
    def spoken(hass):
        """Return [(how, message)] for every delivered announcement."""
        result = []
        for domain, service, data, _ in hass.services.calls:
            if (domain, service) == ("tts", "speak"):
                result.append(("tts", data["message"]))
            elif (domain, service) == ("script", "turn_on"):
                result.append(("script", data["variables"]["message"]))
        return result


class TestUpgradeChangesNothing(CoordinatorTestCase):
    async def test_fixed_text_through_tts_speak_as_before(self):
        hass, _, coordinator = await self.make()
        now = datetime(2026, 11, 13, 8, 40)
        await coordinator._announce_10_minutes(now)
        await coordinator._announce_school_time(now)
        await coordinator._announce_30_minutes(now)
        self.assertEqual(
            hass.services.of("tts", "speak")[0][2],
            {
                "entity_id": "tts.home_assistant_cloud",
                "media_player_entity_id": PLAYER,
                "message": "Faltam 10 minutos para ir para a escola. Vamos terminando de nos preparar!",
                "cache": False,
            },
        )
        self.assertEqual(
            [message for _, message in self.spoken(hass)],
            [
                "Faltam 10 minutos para ir para a escola. Vamos terminando de nos preparar!",
                "Está na hora de ir para a escola! Vamos lá!",
                "Bom dia! Faltam 30 minutos para ir para a escola.",
            ],
        )
        self.assertEqual(hass.services.of("conversation", "process"), [])
        self.assertEqual(hass.services.of("script", "turn_on"), [])

    async def test_weekend_is_skipped(self):
        hass, _, coordinator = await self.make()
        await coordinator._announce_10_minutes(datetime(2026, 11, 14, 8, 40))  # Saturday
        self.assertEqual(self.spoken(hass), [])

    async def test_nothing_configured_to_speak(self):
        hass, _, coordinator = await self.make(**{const.CONF_MEDIA_PLAYER_ENTITY: None})
        await coordinator._announce_10_minutes(datetime(2026, 11, 13, 8, 40))
        self.assertEqual(hass.services.calls, [])

    async def test_default_activities_and_weather_text(self):
        hass, _, coordinator = await self.make(**{const.CONF_WEATHER_ENTITY: "weather.casa"})
        self.assertEqual(
            [a["name"] for a in coordinator.data["duarte"]["activities"]],
            ["Vestir", "Pequeno-Almoço", "Lavar Dentes e Cara", "Coisas", "Lancheira"],
        )
        hass.states.data["weather.casa"] = _State("rainy", {"temperature": 14})
        hass.services.handlers[("weather", "get_forecasts")] = lambda data: {
            "weather.casa": {"forecast": [{"temperature": 17, "precipitation": 2}]}
        }
        await coordinator._announce_30_minutes(datetime(2026, 11, 13, 8, 20))
        self.assertEqual(
            self.spoken(hass),
            [("tts", "Bom dia! Faltam 30 minutos para ir para a escola. A previsão para hoje é chuvoso, "
                     "com máxima de 17 graus e está ameno. Há possibilidade de chuva. Não se esqueçam do guarda-chuva!")],
        )


class TestAiAnnouncements(CoordinatorTestCase):
    AI = {const.CONF_AI_ANNOUNCEMENTS_ENABLED: True, const.CONF_AI_AGENT: AGENT}

    async def test_ai_text_is_sanitised_and_used(self):
        hass, _, coordinator = await self.make(**self.AI)
        hass.services.handlers[("conversation", "process")] = lambda data: _answer('**Faltam dez minutos!** 🎒 "Vamos lá"')
        await coordinator._announce_10_minutes(datetime(2026, 11, 13, 8, 40))
        self.assertEqual(self.spoken(hass), [("tts", "Faltam dez minutos! Vamos lá")])

        (_, _, data, blocking), = hass.services.of("conversation", "process")
        self.assertTrue(blocking)
        self.assertEqual(set(data), {"agent_id", "text"})  # fresh conversation: no conversation_id
        self.assertEqual(data["agent_id"], AGENT)
        self.assertIn('"minutos_para_escola":10', data["text"])
        self.assertIn('"em_falta":["Vestir"', data["text"])
        self.assertTrue(data["text"].startswith(const.DEFAULT_AI_ANNOUNCEMENT_PROMPT))
        for secret in ("CLIENT-ID-VALUE", "CLIENT-SECRET-VALUE", "gdrive"):
            self.assertNotIn(secret, data["text"])

    async def test_custom_prompt(self):
        hass, _, coordinator = await self.make(**self.AI, **{const.CONF_AI_ANNOUNCEMENT_PROMPT: "Fala como um pirata."})
        hass.services.handlers[("conversation", "process")] = lambda data: _answer("Arr, dez minutos!")
        await coordinator._announce_10_minutes(datetime(2026, 11, 13, 8, 40))
        self.assertTrue(hass.services.of("conversation", "process")[0][2]["text"].startswith("Fala como um pirata."))

    async def _fallback_case(self, handler, **options):
        hass, _, coordinator = await self.make(**self.AI, **options)
        hass.services.handlers[("conversation", "process")] = handler
        await coordinator._announce_school_time(datetime(2026, 11, 13, 8, 50))
        self.assertEqual(self.spoken(hass), [("tts", "Está na hora de ir para a escola! Vamos lá!")])

    async def test_timeout_falls_back(self):
        async def slow(data):
            await asyncio.sleep(5)
            return _answer("tarde demais")

        started = asyncio.get_running_loop().time()
        await self._fallback_case(slow, **{const.CONF_AI_TIMEOUT: 1})
        self.assertLess(asyncio.get_running_loop().time() - started, 3)

    async def test_exception_falls_back(self):
        def boom(data):
            raise RuntimeError("agent exploded")

        await self._fallback_case(boom)

    async def test_error_response_falls_back(self):
        await self._fallback_case(lambda data: _answer("Sorry, I had a problem talking to the API", "error"))

    async def test_empty_and_overlong_fall_back(self):
        await self._fallback_case(lambda data: _answer("  🙂 "))
        await self._fallback_case(lambda data: _answer("muito texto " * 50))
        await self._fallback_case(lambda data: None)

    async def test_enabled_without_agent_falls_back(self):
        hass, _, coordinator = await self.make(**{const.CONF_AI_ANNOUNCEMENTS_ENABLED: True})
        await coordinator._announce_school_time(datetime(2026, 11, 13, 8, 50))
        self.assertEqual(self.spoken(hass), [("tts", "Está na hora de ir para a escola! Vamos lá!")])
        self.assertEqual(hass.services.of("conversation", "process"), [])

    async def test_service_triggered_announcements_use_ai(self):
        hass, _, coordinator = await self.make(**self.AI)
        hass.services.handlers[("conversation", "process")] = lambda data: _answer("Texto da IA.")
        await coordinator._announce_time_remaining()
        await coordinator._announce_time_with_weather()
        await coordinator._announce_time_with_activities()
        self.assertEqual(self.spoken(hass), [("tts", "Texto da IA.")] * 3)
        kinds = [call[2]["text"] for call in hass.services.of("conversation", "process")]
        self.assertIn('"tipo":"tempo_restante"', kinds[0])
        self.assertIn('"tipo":"tempo_restante_e_meteorologia"', kinds[1])
        self.assertIn('"tipo":"tempo_restante_meteorologia_e_atividades"', kinds[2])
        self.assertIn('"minutos_para_escola":30', kinds[0])


class TestScriptDelivery(CoordinatorTestCase):
    async def test_script_is_called_non_blocking_with_message(self):
        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCE_SCRIPT: SCRIPT})
        await coordinator._announce_school_time(datetime(2026, 11, 13, 8, 50))
        self.assertEqual(
            hass.services.calls,
            [(
                "script",
                "turn_on",
                {"entity_id": SCRIPT, "variables": {"message": "Está na hora de ir para a escola! Vamos lá!"}},
                False,
            )],
        )

    async def test_script_alone_is_enough(self):
        hass, _, coordinator = await self.make(
            **{const.CONF_ANNOUNCE_SCRIPT: SCRIPT, const.CONF_MEDIA_PLAYER_ENTITY: None}
        )
        await coordinator._announce_10_minutes(datetime(2026, 11, 13, 8, 40))
        self.assertEqual([how for how, _ in self.spoken(hass)], ["script"])

    async def test_missing_or_failing_script_falls_back_to_tts(self):
        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCE_SCRIPT: "script.gone"})
        await coordinator._announce_school_time(datetime(2026, 11, 13, 8, 50))
        self.assertEqual([how for how, _ in self.spoken(hass)], ["tts"])

        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCE_SCRIPT: SCRIPT})
        hass.states.data[SCRIPT] = _State("unavailable")
        await coordinator._announce_school_time(datetime(2026, 11, 13, 8, 50))
        self.assertEqual([how for how, _ in self.spoken(hass)], ["tts"])

        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCE_SCRIPT: SCRIPT})

        def boom(data):
            raise RuntimeError("service not found")

        hass.services.handlers[("script", "turn_on")] = boom
        await coordinator._announce_school_time(datetime(2026, 11, 13, 8, 50))
        self.assertEqual([how for how, _ in self.spoken(hass)], ["script", "tts"])


class TestCompletion(CoordinatorTestCase):
    async def test_announced_without_daily_phrase(self):
        hass, _, coordinator = await self.make()
        await coordinator._announce_completion("duarte")
        await coordinator._announce_completion("leonor")
        self.assertEqual(
            self.spoken(hass),
            [("tts", "Parabéns! O Duarte está pronto!"), ("tts", "Parabéns! A Leonor está pronta!")],
        )

    async def test_announced_with_daily_phrase(self):
        hass, _, coordinator = await self.make()
        coordinator.data["leonor"]["daily_phrase"] = "Hoje vai correr bem!"
        await coordinator._announce_completion("leonor")
        self.assertEqual(
            self.spoken(hass),
            [("tts", "Parabéns! A Leonor está pronta! A frase do dia para a Leonor é: Hoje vai correr bem!")],
        )

    async def test_gating(self):
        hass, _, coordinator = await self.make(**{const.CONF_ANNOUNCEMENTS_ENABLED: False})
        await coordinator._announce_completion("duarte")
        self.assertEqual(self.spoken(hass), [])

        # phrases off and AI off: as before, no completion announcement
        hass, _, coordinator = await self.make(**{const.CONF_DAILY_PHRASE_ENABLED: False})
        await coordinator._announce_completion("duarte")
        self.assertEqual(self.spoken(hass), [])

        # phrases off but AI on: announced
        hass, _, coordinator = await self.make(
            **{const.CONF_DAILY_PHRASE_ENABLED: False, const.CONF_AI_ANNOUNCEMENTS_ENABLED: True, const.CONF_AI_AGENT: AGENT}
        )
        hass.services.handlers[("conversation", "process")] = lambda data: _answer("Boa, Duarte, já está!")
        await coordinator._announce_completion("duarte")
        self.assertEqual(self.spoken(hass), [("tts", "Boa, Duarte, já está!")])
        text = hass.services.of("conversation", "process")[0][2]["text"]
        self.assertIn('"tipo":"rotina_completa"', text)
        self.assertIn('"conclusao":{"crianca":"Duarte"}', text)

    async def test_completing_last_activity_announces_in_background(self):
        hass, _, coordinator = await self.make()
        for activity in coordinator.data["duarte"]["activities"]:
            await coordinator.complete_activity("duarte", activity["id"])
        await asyncio.gather(*hass.tasks)
        self.assertEqual(self.spoken(hass), [("tts", "Parabéns! O Duarte está pronto!")])

    async def test_daily_phrase_uses_ai_agent_when_openai_is_empty(self):
        hass, _, coordinator = await self.make(**{const.CONF_OPENAI_CONFIG_ENTRY: "", const.CONF_AI_AGENT: AGENT})
        coordinator.data["duarte"]["daily_phrase"] = "frase de ontem"
        hass.services.handlers[("conversation", "process")] = lambda data: _answer('"Hoje és um campeão!" 🏆')
        await coordinator._generate_daily_phrases()
        self.assertEqual(coordinator.data["duarte"]["daily_phrase"], "Hoje és um campeão!")
        self.assertEqual({call[2]["agent_id"] for call in hass.services.of("conversation", "process")}, {AGENT})

        # legacy option still wins when set
        hass, _, coordinator = await self.make(**{const.CONF_OPENAI_CONFIG_ENTRY: "openai-entry", const.CONF_AI_AGENT: AGENT})
        hass.services.handlers[("conversation", "process")] = lambda data: _answer("Olá")
        await coordinator._generate_daily_phrases()
        self.assertEqual({call[2]["agent_id"] for call in hass.services.of("conversation", "process")}, {"openai-entry"})

    async def test_failed_phrase_generation_leaves_no_stale_phrase(self):
        hass, _, coordinator = await self.make(**{const.CONF_AI_AGENT: AGENT})
        coordinator.data["duarte"]["daily_phrase"] = "frase de ontem"
        hass.services.handlers[("conversation", "process")] = lambda data: _answer("erro", "error")
        await coordinator._generate_daily_phrases()
        self.assertIsNone(coordinator.data["duarte"]["daily_phrase"])


class TestActivitiesOptions(CoordinatorTestCase):
    NEW_DUARTE = {
        "fixed": [
            {"id": "dressed", "name": "Vestir", "icon": "mdi:tshirt-crew", "camera_required": True},
            {"id": "bed", "name": "Fazer a cama", "icon": "mdi:bed"},
            {"id": "schoolbag", "name": "Mochila do Duarte", "nfc_required": True},
        ],
        "calendar": [{"pattern": "D-Xadrez*", "id": "chess", "name": "Xadrez"}],
    }

    async def test_options_change_reconciles_today_without_losing_progress(self):
        hass, entry, coordinator = await self.make()
        await coordinator.complete_activity("duarte", "dressed")
        await coordinator.complete_activity("duarte", "schoolbag")
        leonor_before = copy.deepcopy(coordinator.data["leonor"]["activities"])

        entry.options = {**entry.options, "activities_duarte": self.NEW_DUARTE}
        await coordinator.async_options_updated()

        activities = coordinator.data["duarte"]["activities"]
        self.assertEqual([a["id"] for a in activities], ["dressed", "bed", "schoolbag"])
        self.assertEqual([a["completed"] for a in activities], [True, False, True])
        self.assertEqual(activities[2]["name"], "Mochila do Duarte")
        self.assertEqual(coordinator.data["leonor"]["activities"], leonor_before)
        self.assertEqual(coordinator.store.saved["duarte"]["activities"], activities)

        # ids are validated against the child's own configuration
        coordinator.validate_activity("duarte", "chess")
        coordinator.validate_activity("duarte", "bed")
        coordinator.validate_activity("leonor", "music")
        for child, activity in (("duarte", "swimming"), ("leonor", "bed"), ("maria", "dressed")):
            with self.assertRaises(sys.modules["homeassistant.exceptions"].ServiceValidationError):
                coordinator.validate_activity(child, activity)
        with self.assertRaises(sys.modules["homeassistant.exceptions"].ServiceValidationError):
            await coordinator.add_nfc_mapping("duarte", "swimming")

    async def test_unrelated_entry_update_does_not_touch_activities(self):
        hass, entry, coordinator = await self.make()
        await coordinator.complete_activity("duarte", "dressed")
        updates = coordinator.updates
        entry.data = {"nfc_mappings": {"04:AA": {"child": "duarte", "activity": "schoolbag"}}}
        await coordinator.async_options_updated()
        self.assertEqual(coordinator.updates, updates)
        self.assertEqual(hass.services.of("calendar", "get_events"), [])

    async def test_startup_reconciles_stored_state(self):
        hass, entry, coordinator = await self.make()
        stored = copy.deepcopy(coordinator.data)
        stored["duarte"]["activities"][3]["name"] = "Mochila"  # stored by an older version
        stored["duarte"]["activities"][3]["completed"] = True
        coordinator.store.saved = stored
        await coordinator._load_data()
        schoolbag = coordinator.data["duarte"]["activities"][3]
        self.assertEqual((schoolbag["name"], schoolbag["completed"]), ("Coisas", True))

    async def test_calendar_sync_and_special_activities_use_configuration(self):
        hass, entry, coordinator = await self.make(
            **{"activities_duarte": self.NEW_DUARTE, const.CONF_CALENDAR_ENTITY: "calendar.familia"}
        )
        hass.states.data["calendar.familia"] = _State("off")
        hass.services.handlers[("calendar", "get_events")] = lambda data: {
            "calendar.familia": {"events": [{"summary": "D-Xadrez Clube"}, {"summary": "D-Natação"}, {"summary": "L-Música Piano"}]}
        }
        await coordinator._sync_calendar()
        self.assertEqual(
            [a["id"] for a in coordinator.data["duarte"]["activities"]], ["dressed", "bed", "schoolbag", "chess"]
        )
        self.assertEqual(coordinator._get_special_activities(), {"duarte": ["Xadrez"], "leonor": ["Música"]})

        # NFC scan of a stored mapping still completes the activity by id
        entry.data = {"nfc_mappings": {"04:AA": {"child": "duarte", "activity": "chess"}}}
        event = types.SimpleNamespace(data={"tag_id": "04:AA", "device_id": "phone"})
        await coordinator._handle_nfc_tag(event)
        chess = coordinator.data["duarte"]["activities"][-1]
        self.assertTrue(chess["completed"])
        self.assertEqual(coordinator.list_nfc_mappings()["mappings"], {"04:AA": "Duarte - Xadrez"})


if __name__ == "__main__":
    unittest.main()
