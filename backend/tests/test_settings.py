"""Settings persistence and the desktop AI execution boundary, without network."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.adapters import ZhihuishuAdapter
from app.models import AdapterError, Course
from app.settings import DEFAULT_AI, DEFAULT_ZHS_HOMEWORK, SettingsStore, validate_zhihuishu
from desktop_bridge import handle


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.store = SettingsStore(self.path)

    def test_save_preserves_cli_fields_and_unknown_ai_options(self):
        self.path.write_text(json.dumps({
            "cookies": {"session": "existing"}, "speed": 1.5,
            "ai": {"future_option": 42, "openai": {"api_key": "retained-secret"}},
        }), encoding="utf-8")
        self.store.save({"ai": {"use_stream": False}})
        config = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(config["cookies"], {"session": "existing"})
        self.assertEqual(config["speed"], 1.5)
        self.assertEqual(config["ai"]["future_option"], 42)
        self.assertEqual(config["ai"]["openai"]["api_key"], "retained-secret")
        self.assertFalse(self.store.load()["ai"]["use_stream"])
        self.assertEqual(list(self.path.parent.glob(".settings-*.tmp")), [])

    def test_invalid_active_provider_does_not_replace_config_or_echo_secrets(self):
        self.store.save({"ai": {}})
        before = self.path.read_bytes()
        with self.assertRaises(ValueError) as error:
            self.store.save({"ai": {"use_zhidao_ai": False, "openai": {
                "api_base": "invalid-secret-url", "api_key": "secret-key",
            }}})
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(self.path.read_bytes(), before)

    def test_zhihuishu_verified_overwrite_setting_must_be_boolean(self):
        validate_zhihuishu({"homework": {**DEFAULT_ZHS_HOMEWORK, "overwrite_with_verified": True}})
        with self.assertRaisesRegex(ValueError, "已验证答案覆盖"):
            validate_zhihuishu({"homework": {**DEFAULT_ZHS_HOMEWORK, "overwrite_with_verified": "yes"}})

    def test_corrupt_config_cannot_be_silently_overwritten(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "格式错误"):
            self.store.save({"ai": {}})
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_builtin_and_disabled_ai_do_not_require_external_credentials(self):
        self.store.save({"ai": {"use_zhidao_ai": True}})
        self.store.save({"ai": {"enabled": False, "use_zhidao_ai": False}})
        self.assertFalse(self.store.load()["ai"]["enabled"])

    def test_ppt_provider_requires_credentials_only_when_active(self):
        with self.assertRaisesRegex(ValueError, "Moonshot"):
            self.store.save({"ai": {"ppt_processing": {"provide_to_ai": True}}})
        result = self.store.save({"ai": {"ppt_processing": {
            "provide_to_ai": True, "moonShot": {"api_key": "test-only-key"},
        }}})
        self.assertTrue(result["ai"]["ppt_processing"]["moonShot"]["delete_after_convert"])

    def test_bridge_save_applies_to_existing_session_and_ai_route(self):
        adapter = ZhihuishuAdapter({"ai": deepcopy(DEFAULT_AI), "save_cookies": False})
        engine = Mock()
        engine.cookies = {"session": "retained"}
        adapter._fucker = engine
        adapters = {"zhihuishu": adapter}
        response = handle({"operation": "save_settings", "settings": {"ai": {
            "use_zhidao_ai": False,
            "openai": {"api_base": "https://example.com/v1", "api_key": "test-only-key", "model_name": "test-model"},
        }}}, adapters, settings_store=self.store)
        self.assertTrue(response["ok"])
        self.assertIs(adapter.fucker, engine)
        self.assertNotIn("cookies", response["settings"])
        course = {"courseId": "zhidao_ai:123:456", "name": "测试 AI 课程", "metadata": {
            "kind": "zhidao_ai", "native_id": "123", "class_id": "456",
        }}
        handle({"operation": "run", "platform": "zhihuishu", "course": course}, adapters)
        engine.fuckAiCourse.assert_called_once_with(123, 456, aiConfig=adapter.config["ai"], no_exam=False)
        engine.fuckCourse.assert_not_called()
        handle({"operation": "save_settings", "settings": {"ai": {"enabled": False}}}, adapters, settings_store=self.store)
        handle({"operation": "run", "platform": "zhihuishu", "course": course}, adapters)
        self.assertTrue(engine.fuckAiCourse.call_args.kwargs["no_exam"])
        loaded = handle({"operation": "get_settings"}, adapters, settings_store=self.store)
        self.assertFalse(loaded["settings"]["ai"]["enabled"])

    def test_ai_course_rejects_missing_class_id_before_starting_engine(self):
        adapter = ZhihuishuAdapter({})
        with self.assertRaisesRegex(AdapterError, "Class ID"):
            adapter.run_course(Course("zhihuishu", "123", "测试", {"kind": "zhidao_ai", "native_id": "123"}))
        self.assertIsNone(adapter._fucker)

    def test_all_ui_sections_roundtrip_and_keep_unknown_fields(self):
        self.path.write_text(json.dumps({"cookies": {"keep": True}, "future": 42,
            "ketangpai": {"future": "keep"}}), encoding="utf-8")
        settings = self.store.load()
        self.assertTrue(settings["ketangpai"]["WEBSOCKET_SERVER_PATH"].startswith("/"))
        settings["general"].update({"speed": 1.25, "threshold": 0.95, "limit": 20})
        result = self.store.save(settings)
        self.assertEqual(result, self.store.load())
        config = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(config["future"], 42)
        self.assertEqual(config["cookies"], {"keep": True})
        self.assertEqual(config["ketangpai"]["future"], "keep")
        self.assertEqual(config["speed"], 1.25)

    def test_invalid_schedule_cannot_replace_any_section(self):
        settings = self.store.load()
        self.store.save(settings)
        before = self.path.read_bytes()
        settings["general"]["limit"] = 30
        settings["ketangpai"]["CLASSES"] = [
            {"friendlyName": "a", "classId": "1", "dayOfWeek": 1, "startTime": "08:00", "endTime": "09:00"},
            {"friendlyName": "b", "classId": "2", "dayOfWeek": 1, "startTime": "08:30", "endTime": "10:00"},
        ]
        with self.assertRaisesRegex(ValueError, "重叠"):
            self.store.save(settings)
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_location_is_allowed_without_inventing_coordinates(self):
        profile = self.store.load()["ketangpai"]
        self.assertEqual((profile["DEFAULT_LATITUDE"], profile["DEFAULT_LONGITUDE"]), ("", ""))
        self.assertFalse(profile['ENABLE_IGNORE_QRCODE_EXPIRE'])
        self.store.save({"ketangpai": profile})
        with self.assertRaisesRegex(ValueError, "GPS"):
            self.store.save({"ketangpai": {"DEFAULT_LATITUDE": "31.2", "DEFAULT_LONGITUDE": ""}})

    def test_general_settings_apply_to_existing_engine_without_losing_session(self):
        adapter = ZhihuishuAdapter({"pushplus_token": "legacy-token"})
        engine = Mock(cookies={"session": "keep"})
        adapter._fucker = engine
        response = handle({"operation": "save_settings", "settings": {"general": {
            "speed": 1.25, "threshold": 0.95, "limit": 20,
            "proxies": {"https": "http://127.0.0.1:7890"},
            "pushplus": {"enable": False},
        }}}, {"zhihuishu": adapter}, settings_store=self.store)
        self.assertTrue(response["ok"])
        self.assertIs(adapter.fucker, engine)
        self.assertEqual(engine.cookies, {"session": "keep"})
        self.assertEqual((engine.speed, engine.end_thre, engine.limit), (1.25, 0.95, 20))
        self.assertEqual(engine.proxies, {"https": "http://127.0.0.1:7890"})
        self.assertEqual(adapter._push_token("pushplus"), "")


if __name__ == "__main__":
    unittest.main()
