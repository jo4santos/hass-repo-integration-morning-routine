"""Unit tests for the pure logic (no Home Assistant needed).

Run from the repository root with:  python3 -m unittest discover -s tests -v
"""
import copy
import json
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _loader import load_package  # noqa: E402

load_package()

from morning_routine import activities as act  # noqa: E402
from morning_routine import announce as ann  # noqa: E402
from morning_routine import const  # noqa: E402


def _ids(items):
    return [item["id"] for item in items]


class TestDefaults(unittest.TestCase):
    """Without options the behaviour is exactly the one of v2.11.8."""

    def test_pure_modules_import_without_home_assistant(self):
        """activities.py / announce.py / const.py must not import Home Assistant."""
        import subprocess

        code = (
            "import sys; sys.path.insert(0, %r); from _loader import load_package; load_package();"
            "from morning_routine import activities, announce, const;"
            "bad = [m for m in sys.modules if m.split('.')[0] in ('homeassistant', 'voluptuous')];"
            "sys.exit(1 if bad else 0)"
        ) % os.path.dirname(os.path.abspath(__file__))
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)

    def test_duarte_defaults(self):
        config = act.effective_child_config({}, "duarte")
        self.assertEqual(_ids(config["fixed"]), ["dressed", "breakfast", "teeth", "schoolbag", "lunchbox"])
        self.assertEqual(
            [item["name"] for item in config["fixed"]],
            ["Vestir", "Pequeno-Almoço", "Lavar Dentes e Cara", "Coisas", "Lancheira"],
        )
        self.assertEqual(
            [(i["pattern"], i["id"], i["name"], i["icon"], i["nfc_required"]) for i in config["calendar"]],
            [
                ("D-Natação", "swimming", "Natação", "mdi:swim", True),
                ("D-Taekwondo", "taekwondo", "Taekwondo", "mdi:karate", True),
            ],
        )

    def test_leonor_defaults(self):
        config = act.effective_child_config(None, "leonor")
        self.assertEqual(
            [item["name"] for item in config["fixed"]],
            ["Vestir", "Pequeno-Almoço", "Lavar Dentes e Cara", "Mochila", "Lancheira"],
        )
        self.assertEqual(
            [(i["pattern"], i["id"], i["name"], i["icon"]) for i in config["calendar"]],
            [("L-Música*", "music", "Música", "mdi:music"), ("L-Taekwondo", "taekwondo", "Taekwondo", "mdi:karate")],
        )

    def test_fixed_state_matches_old_constants(self):
        """The fresh daily list equals what _get_default_activities built from the constants."""
        for child in const.CHILDREN:
            expected = []
            for fixed in const.FIXED_ACTIVITIES:
                merged = {**fixed, **const.get_activity_config(child, fixed["id"])}
                expected.append(
                    {
                        "id": merged["id"],
                        "name": merged["name"],
                        "icon": merged["icon"],
                        "completed": False,
                        "completed_at": None,
                        "camera_required": merged.get("camera_required", False),
                        "nfc_required": merged.get("nfc_required", False),
                        "source": "default",
                    }
                )
            self.assertEqual(act.build_fixed_activities(act.effective_child_config({}, child)), expected)

    def test_known_ids_cover_legacy_activity_types(self):
        all_ids = set()
        for child in const.CHILDREN:
            all_ids.update(act.activity_ids(act.default_child_config(child)))
        self.assertEqual(all_ids, set(const.ACTIVITY_TYPES))

    def test_defaults_are_valid_and_stable(self):
        for child in const.CHILDREN:
            default = act.default_child_config(child)
            self.assertEqual(act.parse_child_config(default), default)
            # the compact form shown in the UI parses back to the same configuration
            self.assertEqual(act.parse_child_config(act.display_config(default)), default)

    def test_empty_option_means_defaults(self):
        for empty in (None, "", "   ", {}, []):
            self.assertTrue(act.is_empty_config(empty))
            options = {act.conf_activities_key("duarte"): empty}
            self.assertEqual(act.effective_child_config(options, "duarte"), act.default_child_config("duarte"))

    def test_invalid_stored_option_falls_back_to_defaults(self):
        options = {"activities_duarte": {"fixed": "nope"}}
        self.assertEqual(act.effective_child_config(options, "duarte"), act.default_child_config("duarte"))

    def test_options_override_defaults_per_child(self):
        options = {"activities_duarte": {"fixed": [{"id": "bed", "name": "Fazer a cama"}]}}
        self.assertEqual(_ids(act.effective_child_config(options, "duarte")["fixed"]), ["bed"])
        self.assertEqual(act.effective_child_config(options, "leonor"), act.default_child_config("leonor"))


class TestValidation(unittest.TestCase):
    def assertCode(self, value, code):
        with self.assertRaises(act.ActivityConfigError) as ctx:
            act.parse_child_config(value)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_not_mapping(self):
        self.assertCode([1, 2], act.ERR_NOT_MAPPING)
        self.assertCode(42, act.ERR_NOT_MAPPING)
        self.assertCode("just text", act.ERR_NOT_MAPPING)

    def test_unknown_section(self):
        self.assertCode({"fixed": [{"id": "a", "name": "A"}], "extra": []}, act.ERR_UNKNOWN_SECTION)

    def test_lists_required(self):
        self.assertCode({"fixed": {"id": "a", "name": "A"}}, act.ERR_NOT_LIST)
        self.assertCode({"fixed": [{"id": "a", "name": "A"}], "calendar": "x"}, act.ERR_NOT_LIST)

    def test_at_least_one_fixed(self):
        self.assertCode({"fixed": []}, act.ERR_NO_FIXED)
        self.assertCode({"calendar": []}, act.ERR_NO_FIXED)

    def test_item_must_be_mapping(self):
        self.assertCode({"fixed": ["dressed"]}, act.ERR_ITEM_NOT_MAPPING)

    def test_id_required_and_slug(self):
        self.assertCode({"fixed": [{"name": "A"}]}, act.ERR_MISSING_ID)
        for bad in ("Vestir", "com espaço", "natação", "a-b", 12):
            self.assertCode({"fixed": [{"id": bad, "name": "A"}]}, act.ERR_INVALID_ID)
        act.parse_child_config({"fixed": [{"id": "saco_2", "name": "A"}]})

    def test_name_required(self):
        self.assertCode({"fixed": [{"id": "a"}]}, act.ERR_MISSING_NAME)
        self.assertCode({"fixed": [{"id": "a", "name": "  "}]}, act.ERR_MISSING_NAME)

    def test_pattern_required_for_calendar(self):
        err = self.assertCode(
            {"fixed": [{"id": "a", "name": "A"}], "calendar": [{"id": "b", "name": "B"}]},
            act.ERR_MISSING_PATTERN,
        )
        self.assertIn("calendar[1]", err.detail)

    def test_pattern_not_allowed_in_fixed(self):
        self.assertCode({"fixed": [{"id": "a", "name": "A", "pattern": "X"}]}, act.ERR_UNKNOWN_KEY)

    def test_unknown_key(self):
        self.assertCode({"fixed": [{"id": "a", "name": "A", "nfc_requried": True}]}, act.ERR_UNKNOWN_KEY)

    def test_ids_unique_within_child(self):
        self.assertCode({"fixed": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}, act.ERR_DUPLICATE_ID)
        self.assertCode(
            {"fixed": [{"id": "a", "name": "A"}], "calendar": [{"pattern": "X", "id": "a", "name": "B"}]},
            act.ERR_DUPLICATE_ID,
        )

    def test_icon(self):
        self.assertCode({"fixed": [{"id": "a", "name": "A", "icon": "swim"}]}, act.ERR_INVALID_ICON)
        self.assertCode({"fixed": [{"id": "a", "name": "A", "icon": "mdi:"}]}, act.ERR_INVALID_ICON)
        parsed = act.parse_child_config({"fixed": [{"id": "a", "name": "A"}]})
        self.assertEqual(parsed["fixed"][0]["icon"], "mdi:checkbox-marked-circle-outline")

    def test_booleans(self):
        self.assertCode({"fixed": [{"id": "a", "name": "A", "nfc_required": "sim"}]}, act.ERR_INVALID_BOOL)
        parsed = act.parse_child_config({"fixed": [{"id": "a", "name": "A"}]})
        self.assertIs(parsed["fixed"][0]["nfc_required"], False)
        self.assertIs(parsed["fixed"][0]["camera_required"], False)
        self.assertEqual(parsed["calendar"], [])

    def test_error_detail_locates_the_item(self):
        err = self.assertCode(
            {"fixed": [{"id": "a", "name": "A"}, {"id": "b", "name": "B", "icon": "x"}]},
            act.ERR_INVALID_ICON,
        )
        self.assertIn("fixed[2]", err.detail)
        self.assertIn("b", err.detail)

    def test_yaml_string(self):
        text = """
fixed:
  - {id: dressed, name: Vestir, icon: "mdi:tshirt-crew", camera_required: true}
  - id: schoolbag
    name: Coisas
    nfc_required: true
calendar:
  - {pattern: "D-Natação", id: swimming, name: Natação, icon: "mdi:swim", nfc_required: true}
"""
        parsed = act.parse_child_config(text)
        self.assertEqual(_ids(parsed["fixed"]), ["dressed", "schoolbag"])
        self.assertTrue(parsed["fixed"][0]["camera_required"])
        self.assertEqual(parsed["calendar"][0]["pattern"], "D-Natação")
        # same result as the equivalent dict
        import yaml

        self.assertEqual(parsed, act.parse_child_config(yaml.safe_load(text)))

    def test_yaml_example_from_the_spec(self):
        """Unquoted mdi: icons inside flow mappings (as in the documented example)."""
        text = (
            "fixed:\n"
            "  - {id: dressed, name: Vestir, icon: mdi:tshirt-crew, camera_required: true}\n"
            "  - {id: breakfast, name: Pequeno-Almoço, icon: mdi:food-variant}\n"
            "calendar:\n"
            "  - {pattern: D-Natação, id: swimming, name: Natação, icon: mdi:swim, nfc_required: true}\n"
        )
        parsed = act.parse_child_config(text)
        self.assertEqual(parsed["fixed"][1]["icon"], "mdi:food-variant")
        self.assertEqual(parsed["calendar"][0]["icon"], "mdi:swim")

    def test_invalid_yaml_string(self):
        err = self.assertCode("fixed: [\n  - id: a", act.ERR_INVALID_YAML)
        self.assertIn("linha", err.detail)

    def test_result_is_json_serialisable(self):
        json.dumps(act.parse_child_config(act.default_child_config("duarte")))


class TestCalendarMatching(unittest.TestCase):
    def test_wildcard_first_match_and_no_duplicates(self):
        config = act.default_child_config("leonor")
        found = act.match_calendar_activities(
            config, ["L-Música Formação", "L-Música Piano", "D-Natação", "L-Taekwondo", ""], "now"
        )
        self.assertEqual(_ids(found), ["music", "taekwondo"])
        music = found[0]
        self.assertEqual(music["event_summary"], "L-Música Formação")
        self.assertEqual(music["source"], "calendar")
        self.assertFalse(music["completed"])
        self.assertTrue(music["nfc_required"])
        self.assertFalse(music["camera_required"])

    def test_no_events(self):
        self.assertEqual(act.match_calendar_activities(act.default_child_config("duarte"), []), [])


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.config = act.default_child_config("duarte")
        self.state = act.build_fixed_activities(self.config)
        self.state += act.match_calendar_activities(self.config, ["D-Natação"], "t0")
        # dressed and swimming are done
        for activity in self.state:
            if activity["id"] in ("dressed", "swimming"):
                activity["completed"] = True
                activity["completed_at"] = "2026-11-13T07:10:00+00:00"
                activity["device_id"] = "tablet"

    def _by_id(self, activities):
        return {a["id"]: a for a in activities}

    def test_unchanged_config_is_a_no_op(self):
        before = copy.deepcopy(self.state)
        self.assertEqual(act.reconcile_activities(self.state, self.config), before)
        self.assertEqual(self.state, before)  # input not mutated

    def test_keeps_completion_and_renames(self):
        config = copy.deepcopy(self.config)
        config["fixed"][0].update(name="Vestir a roupa", icon="mdi:hanger", camera_required=False)
        config["calendar"][0].update(name="Piscina")
        result = self._by_id(act.reconcile_activities(self.state, config))
        self.assertTrue(result["dressed"]["completed"])
        self.assertEqual(result["dressed"]["completed_at"], "2026-11-13T07:10:00+00:00")
        self.assertEqual(result["dressed"]["device_id"], "tablet")
        self.assertEqual(result["dressed"]["name"], "Vestir a roupa")
        self.assertEqual(result["dressed"]["icon"], "mdi:hanger")
        self.assertFalse(result["dressed"]["camera_required"])
        self.assertTrue(result["swimming"]["completed"])
        self.assertEqual(result["swimming"]["name"], "Piscina")
        self.assertFalse(result["breakfast"]["completed"])

    def test_adds_new_fixed_not_completed(self):
        config = copy.deepcopy(self.config)
        config["fixed"].insert(1, {"id": "bed", "name": "Fazer a cama", "icon": "mdi:bed", "camera_required": False, "nfc_required": False})
        result = act.reconcile_activities(self.state, config)
        self.assertEqual(_ids(result), ["dressed", "bed", "breakfast", "teeth", "schoolbag", "lunchbox", "swimming"])
        bed = self._by_id(result)["bed"]
        self.assertFalse(bed["completed"])
        self.assertIsNone(bed["completed_at"])
        self.assertEqual(bed["source"], "default")

    def test_drops_removed_fixed_and_unmapped_calendar(self):
        config = copy.deepcopy(self.config)
        config["fixed"] = [f for f in config["fixed"] if f["id"] != "teeth"]
        config["calendar"] = [c for c in config["calendar"] if c["id"] != "swimming"]
        result = act.reconcile_activities(self.state, config)
        self.assertEqual(_ids(result), ["dressed", "breakfast", "schoolbag", "lunchbox"])

    def test_calendar_dropped_when_pattern_no_longer_matches_event(self):
        config = copy.deepcopy(self.config)
        config["calendar"][0]["pattern"] = "D-Piscina"
        self.assertNotIn("swimming", _ids(act.reconcile_activities(self.state, config)))
        config["calendar"][0]["pattern"] = "D-Nat*"
        self.assertIn("swimming", _ids(act.reconcile_activities(self.state, config)))

    def test_calendar_not_added_without_event(self):
        """Calendar activities only appear through a calendar sync, never by reconciling."""
        result = act.reconcile_activities(self.state, self.config)
        self.assertNotIn("taekwondo", _ids(result))

    def test_order_follows_config_fixed_then_calendar(self):
        state = self.state + act.match_calendar_activities(self.config, ["D-Taekwondo"], "t0")
        config = copy.deepcopy(self.config)
        config["fixed"].reverse()
        config["calendar"].reverse()
        result = act.reconcile_activities(list(reversed(state)), config)
        self.assertEqual(
            _ids(result),
            ["lunchbox", "schoolbag", "teeth", "breakfast", "dressed", "taekwondo", "swimming"],
        )

    def test_calendar_activity_promoted_to_fixed_keeps_progress(self):
        config = copy.deepcopy(self.config)
        swimming = config["calendar"].pop(0)
        swimming.pop("pattern")
        config["fixed"].append(swimming)
        result = self._by_id(act.reconcile_activities(self.state, config))
        self.assertTrue(result["swimming"]["completed"])
        self.assertEqual(result["swimming"]["source"], "default")
        self.assertNotIn("event_summary", result["swimming"])

    def test_legacy_state_migration(self):
        """State stored by an old version (old names) is refreshed like _migrate_activity_names did."""
        legacy = [
            {"id": "schoolbag", "name": "Mochila", "icon": "mdi:bag-personal", "completed": True,
             "completed_at": "x", "camera_required": False, "nfc_required": True, "source": "default"},
        ]
        result = self._by_id(act.reconcile_activities(legacy, self.config))
        self.assertEqual(result["schoolbag"]["name"], "Coisas")
        self.assertTrue(result["schoolbag"]["completed"])
        self.assertEqual(len(result), 5)

    def test_empty_or_missing_state(self):
        self.assertEqual(act.reconcile_activities(None, self.config), act.build_fixed_activities(self.config))


class TestSpecialActivities(unittest.TestCase):
    def test_special_is_calendar_source(self):
        config = act.default_child_config("duarte")
        state = act.build_fixed_activities(config) + act.match_calendar_activities(config, ["D-Taekwondo"], "t")
        self.assertEqual(act.special_activity_names(state), ["Taekwondo"])

    def test_custom_fixed_activity_is_not_special(self):
        """Before v2.12.0 any id outside the hard-coded basic list counted as special."""
        config = act.parse_child_config({"fixed": [{"id": "bed", "name": "Fazer a cama"}]})
        self.assertEqual(act.special_activity_names(act.build_fixed_activities(config)), [])
        self.assertEqual(act.special_activity_names(None), [])


class TestSanitiser(unittest.TestCase):
    def test_strips_markdown(self):
        text = "# Bom dia\n**Faltam** _dez_ minutos, `meninos`!\n- Duarte: [mochila](http://x)\n* Leonor: ~~nada~~ lancheira"
        self.assertEqual(
            ann.sanitize_answer(text),
            "Bom dia Faltam dez minutos, meninos! Duarte: mochila Leonor: nada lancheira",
        )

    def test_strips_code_fences_and_numbered_lists(self):
        self.assertEqual(ann.sanitize_answer("```text\nOlá!\n```"), "Olá!")
        self.assertEqual(ann.sanitize_answer("1. Vestir\n2) Comer"), "Vestir Comer")

    def test_strips_emoji(self):
        self.assertEqual(ann.sanitize_answer("Bom dia! ☀️ Faltam dez minutos 🎒👍🏽 🇵🇹."), "Bom dia! Faltam dez minutos.")

    def test_strips_quotes_but_keeps_apostrophes_inside_words(self):
        self.assertEqual(ann.sanitize_answer('"Bom dia!"'), "Bom dia!")
        self.assertEqual(ann.sanitize_answer("«Vamos lá» “já” 'agora'"), "Vamos lá já agora")
        self.assertEqual(ann.sanitize_answer("um copo d'água"), "um copo d'água")

    def test_keeps_portuguese_text_untouched(self):
        text = "Bom dia! Faltam trinta minutos para a escola; hoje há sol, com máxima de dezoito graus. Não se esqueçam da lancheira!"
        self.assertEqual(ann.sanitize_answer(text), text)

    def test_non_string(self):
        self.assertEqual(ann.sanitize_answer(None), "")
        self.assertEqual(ann.sanitize_answer({"a": 1}), "")


class TestChooseMessage(unittest.TestCase):
    FALLBACK = "Faltam 10 minutos para ir para a escola. Vamos terminando de nos preparar!"

    def test_good_answer_is_used(self):
        self.assertEqual(
            ann.choose_message("**Faltam dez minutos!** 🙂", self.FALLBACK),
            ("Faltam dez minutos!", "ai", "ok"),
        )

    def test_empty_answers_fall_back(self):
        for answer in (None, "", "   ", "🙂🙂", '""', "**"):
            message, path, _ = ann.choose_message(answer, self.FALLBACK)
            self.assertEqual((message, path), (self.FALLBACK, "fallback"), answer)

    def test_overlong_answer_falls_back(self):
        message, path, reason = ann.choose_message("palavra " * 60, self.FALLBACK)
        self.assertEqual((message, path), (self.FALLBACK, "fallback"))
        self.assertIn("too long", reason)
        ok = "a" * 400
        self.assertEqual(ann.choose_message(ok, self.FALLBACK)[1], "ai")
        self.assertEqual(ann.choose_message(ok + "a", self.FALLBACK)[1], "fallback")

    def test_extract_speech(self):
        good = {"response": {"response_type": "action_done", "speech": {"plain": {"speech": "Olá", "extra_data": None}}}, "conversation_id": "x"}
        self.assertEqual(ann.extract_speech(good), "Olá")
        error = {"response": {"response_type": "error", "speech": {"plain": {"speech": "Sorry, there was a problem"}}, "data": {"code": "unknown"}}}
        self.assertIsNone(ann.extract_speech(error))
        for bad in (None, {}, {"response": None}, {"response": {"speech": {}}}, {"response": {"speech": {"plain": {"speech": "  "}}}}, "text"):
            self.assertIsNone(ann.extract_speech(bad))


class TestFallbackSentences(unittest.TestCase):
    """The fixed texts are the same as before v2.12.0."""

    def test_weather_with_forecast_no_rain(self):
        weather = ann.build_weather_info("sol", 18.5, True, 0)
        self.assertEqual(
            ann.weather_sentence(weather),
            " A previsão para hoje é sol, com máxima de 18.5 graus e está ameno. Tenham um ótimo dia!",
        )
        self.assertEqual(
            ann.weather_sentence(weather, closing=False),
            " A previsão para hoje é sol, com máxima de 18.5 graus e está ameno.",
        )

    def test_weather_with_rain(self):
        weather = ann.build_weather_info("chuvoso", 12, True, 3.2)
        self.assertEqual(
            ann.weather_sentence(weather, closing=False),
            " A previsão para hoje é chuvoso, com máxima de 12 graus e está fresco."
            " Há possibilidade de chuva. Não se esqueçam do guarda-chuva!",
        )

    def test_weather_without_forecast_or_temperature(self):
        self.assertEqual(
            ann.weather_sentence(ann.build_weather_info("nublado", None, False)),
            " A previsão para hoje é nublado.",
        )
        self.assertEqual(
            ann.weather_sentence(ann.build_weather_info("nublado", 9, False)),
            " A previsão para hoje é nublado, com máxima de 9 graus e está frio.",
        )
        self.assertEqual(ann.weather_sentence(None), "")

    def test_weather_helpers(self):
        self.assertEqual(ann.translate_weather_condition("partlycloudy"), "parcialmente nublado")
        self.assertEqual(ann.translate_weather_condition("unknown-thing"), "unknown-thing")
        self.assertEqual(
            [ann.temperature_description(t) for t in (5, 10, 15, 20, 25)],
            ["está frio", "está fresco", "está ameno", "está agradável", "está quente"],
        )

    def test_special_activities_sentence(self):
        children = [
            {"article": "O", "name": "Duarte", "special": ["Natação"]},
            {"article": "A", "name": "Leonor", "special": ["Música", "Taekwondo"]},
        ]
        self.assertEqual(
            ann.special_activities_sentence(children),
            " O Duarte tem Natação hoje. A Leonor tem Música e Taekwondo hoje.",
        )
        children[0]["special"] = ["A", "B", "C"]
        children[1]["special"] = []
        self.assertEqual(ann.special_activities_sentence(children), " O Duarte tem A, B e C hoje.")

    def test_completion_fallback(self):
        duarte = const.CHILDREN_CONFIG["duarte"]
        leonor = const.CHILDREN_CONFIG["leonor"]
        # time unknown: only the congratulation
        self.assertEqual(ann.completion_fallback(duarte, None), "Parabéns, Duarte! Completaste todas as tarefas.")
        self.assertEqual(ann.completion_fallback(leonor, ""), "Parabéns, Leonor! Completaste todas as tarefas.")
        self.assertEqual(
            ann.completion_fallback(leonor, "Hoje vai ser um grande dia!"),
            "Parabéns, Leonor! Completaste todas as tarefas. A frase do dia para a Leonor é: Hoje vai ser um grande dia!",
        )

    def test_completion_fallback_with_time_left(self):
        duarte = const.CHILDREN_CONFIG["duarte"]
        leonor = const.CHILDREN_CONFIG["leonor"]
        base = "Parabéns, Duarte! Completaste todas as tarefas."
        # plenty of time: play
        self.assertEqual(ann.completion_fallback(duarte, None, 25), f"{base} Ainda tens 25 minutos para brincar.")
        self.assertEqual(ann.completion_fallback(duarte, None, 4), f"{base} Ainda tens 4 minutos para brincar.")
        self.assertEqual(ann.completion_fallback(duarte, None, 140), f"{base} Ainda tens mais de uma hora para brincar.")
        # few minutes: just in time
        for minutes in (3, 2, 1):
            self.assertEqual(ann.completion_fallback(duarte, None, minutes), f"{base} Mesmo a tempo de sair!")
        # school time reached or passed
        for minutes in (0, -5):
            self.assertEqual(ann.completion_fallback(duarte, None, minutes), f"{base} Está na hora de sair!")
        # with the daily phrase, after the time
        self.assertEqual(
            ann.completion_fallback(leonor, "Força!", 12),
            "Parabéns, Leonor! Completaste todas as tarefas. Ainda tens 12 minutos para brincar."
            " A frase do dia para a Leonor é: Força!",
        )

    def test_completion_time_hint(self):
        self.assertIsNone(ann.completion_time_hint(None))
        self.assertIsNone(ann.completion_time_hint("10"))
        self.assertEqual(ann.completion_time_hint(30), ann.TIME_HINT_PLAY)
        self.assertEqual(ann.completion_time_hint(4), ann.TIME_HINT_PLAY)
        self.assertEqual(ann.completion_time_hint(3), ann.TIME_HINT_JUST_IN_TIME)
        self.assertEqual(ann.completion_time_hint(1), ann.TIME_HINT_JUST_IN_TIME)
        self.assertEqual(ann.completion_time_hint(0), ann.TIME_HINT_LEAVE_NOW)
        self.assertEqual(ann.completion_time_hint(-3), ann.TIME_HINT_LEAVE_NOW)


class TestFacts(unittest.TestCase):
    NOW = datetime(2026, 11, 13, 8, 20)  # a Friday

    def _children(self):
        facts = []
        for child in const.CHILDREN:
            config = act.default_child_config(child)
            state = act.build_fixed_activities(config)
            if child == "duarte":
                state += act.match_calendar_activities(config, ["D-Natação"], "t")
                state[0]["completed"] = True
            else:
                for activity in state:
                    activity["completed"] = True
            # things that must never reach the agent
            for activity in state:
                activity["device_id"] = "secret-device"
            facts.append(ann.build_child_facts(const.CHILDREN_CONFIG[child], state))
        return facts

    def _facts(self, kind=ann.KIND_30_MINUTES, **kwargs):
        params = dict(
            now=self.NOW,
            school_time="08:50:00",
            minutes_to_school=30,
            children=self._children(),
            weather=ann.build_weather_info("chuvoso", 17, True, 1.5),
        )
        params.update(kwargs)
        return ann.build_facts(kind, **params)

    def test_content(self):
        facts = self._facts()
        self.assertEqual(facts["tipo"], "aviso_30_minutos")
        self.assertEqual(facts["minutos_para_escola"], 30)
        self.assertEqual(facts["hora_da_escola"], "08:50")
        self.assertEqual(facts["dia_da_semana"], "sexta-feira")
        self.assertEqual(facts["data"], "13 de novembro")
        self.assertEqual(
            facts["tempo"],
            {"condicao": "chuvoso", "temperatura_maxima": 17, "chuva_prevista": True, "roupa": "está ameno"},
        )
        duarte, leonor = facts["criancas"]
        self.assertEqual(duarte["nome"], "Duarte")
        self.assertEqual(duarte["genero"], "masculino")
        self.assertEqual(duarte["feitas"], ["Vestir"])
        self.assertEqual(duarte["em_falta"], ["Pequeno-Almoço", "Lavar Dentes e Cara", "Coisas", "Lancheira", "Natação"])
        self.assertEqual(duarte["atividades_especiais_hoje"], ["Natação"])
        self.assertFalse(duarte["rotina_completa"])
        self.assertEqual(leonor["genero"], "feminino")
        self.assertEqual(leonor["em_falta"], [])
        self.assertTrue(leonor["rotina_completa"])
        self.assertNotIn("conclusao", facts)

    def test_only_expected_keys_for_every_kind(self):
        for kind in ann.ANNOUNCEMENT_KINDS:
            facts = self._facts(kind, completion={"child": "Leonor", "daily_phrase": "Força!"})
            self.assertLessEqual(set(facts), ann.FACT_KEYS, kind)
            self.assertLessEqual(set(facts["tempo"]), ann.WEATHER_FACT_KEYS)
            self.assertLessEqual(set(facts["conclusao"]), ann.COMPLETION_FACT_KEYS)
            for child in facts["criancas"]:
                self.assertEqual(set(child), ann.CHILD_FACT_KEYS)

    def test_no_secrets_or_internal_data(self):
        children = self._children()
        children[0]["gdrive_client_secret"] = "SHOULD-NOT-LEAK"
        children[0]["photo_path"] = "/config/secret.jpg"
        prompt = ann.build_prompt("Regras.", self._facts(children=children))
        for forbidden in ("SHOULD-NOT-LEAK", "secret", "client_id", "client_secret", "gdrive", "device_id", "photo_path", "token", "password"):
            self.assertNotIn(forbidden, prompt)

    def test_optional_parts(self):
        facts = self._facts(ann.KIND_10_MINUTES, weather=None, minutes_to_school=10)
        self.assertNotIn("tempo", facts)
        self.assertEqual(facts["minutos_para_escola"], 10)

        facts = self._facts(ann.KIND_COMPLETION, weather=None, minutes_to_school=None,
                            completion={"child": "Duarte", "daily_phrase": None})
        self.assertNotIn("minutos_para_escola", facts)
        self.assertEqual(facts["conclusao"], {"crianca": "Duarte"})

        # minutes known: the completion carries them and the matching time hint
        facts = self._facts(ann.KIND_COMPLETION, completion={"child": "Duarte", "daily_phrase": "Tu consegues!"})
        self.assertEqual(facts["minutos_para_escola"], 30)
        self.assertEqual(
            facts["conclusao"],
            {
                "crianca": "Duarte",
                "frase_do_dia": "Tu consegues!",
                "minutos_para_escola": 30,
                "dica_de_tempo": "ainda tem tempo para brincar",
            },
        )
        for minutes, hint in ((2, "mesmo a tempo de sair"), (0, "está na hora de sair")):
            facts = self._facts(ann.KIND_COMPLETION, minutes_to_school=minutes,
                                completion={"child": "Duarte", "daily_phrase": None})
            self.assertEqual(
                facts["conclusao"], {"crianca": "Duarte", "minutos_para_escola": minutes, "dica_de_tempo": hint}
            )
        self.assertEqual(
            ann.COMPLETION_FACT_KEYS, {"crianca", "frase_do_dia", "minutos_para_escola", "dica_de_tempo"}
        )
        self.assertIn("dica_de_tempo", ann.ANNOUNCEMENT_KINDS[ann.KIND_COMPLETION][1])

        facts = self._facts(weather=ann.build_weather_info("sol", 21, False))
        self.assertEqual(facts["tempo"], {"condicao": "sol", "temperatura_atual": 21, "roupa": "está agradável"})

    def test_prompt(self):
        facts = self._facts()
        prompt = ann.build_prompt(const.DEFAULT_AI_ANNOUNCEMENT_PROMPT, facts)
        self.assertTrue(prompt.startswith(const.DEFAULT_AI_ANNOUNCEMENT_PROMPT))
        payload = prompt.split("FACTOS (JSON): ", 1)[1]
        self.assertEqual(json.loads(payload), facts)
        self.assertNotIn("\n", payload)  # compact, single line
        self.assertIn("Natação", payload)  # not \\u-escaped
        self.assertIn("aviso_30_minutos", prompt)

    def test_default_prompt_covers_the_rules(self):
        prompt = const.DEFAULT_AI_ANNOUNCEMENT_PROMPT.lower()
        for needle in ("português de portugal", "emojis", "markdown", "aspas", "listas", "nunca inventes", "1 frase", "35 palavras",
                       "dica_de_tempo", "brincar", "mesmo a tempo de sair", "não sugiras mais nada"):
            self.assertIn(needle, prompt)


class TestTranslations(unittest.TestCase):
    """Every validation error code has a pt-PT message; files are valid."""

    def _load(self, *parts):
        from _loader import PACKAGE_DIR

        with open(os.path.join(PACKAGE_DIR, *parts), encoding="utf-8") as handle:
            return json.load(handle)

    def test_error_codes_translated(self):
        codes = [value for name, value in vars(act).items() if name.startswith("ERR_")]
        self.assertGreaterEqual(len(codes), 14)
        for filename in (("strings.json",), ("translations", "pt.json")):
            errors = self._load(*filename)["options"]["error"]
            for code in codes + ["ai_agent_required", "no_calendar", "auth_failed"]:
                self.assertIn(code, errors)

    def test_menu_and_fields_translated(self):
        for filename in (("strings.json",), ("translations", "pt.json")):
            steps = self._load(*filename)["options"]["step"]
            self.assertEqual(
                list(steps["init"]["menu_options"]),
                ["general", "activities", "announcements", "rewards", "gdrive"],
            )
            for child in const.CHILDREN:
                self.assertIn(act.conf_activities_key(child), steps["activities"]["data"])
            for key in (const.CONF_AI_AGENT, const.CONF_AI_ANNOUNCEMENTS_ENABLED, const.CONF_AI_ANNOUNCEMENT_PROMPT,
                        const.CONF_AI_TIMEOUT, const.CONF_ANNOUNCE_SCRIPT):
                self.assertIn(key, steps["announcements"]["data"])

    def test_manifest_version(self):
        self.assertEqual(self._load("manifest.json")["version"], "2.13.1")


if __name__ == "__main__":
    unittest.main()
