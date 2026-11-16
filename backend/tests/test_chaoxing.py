"""Core Chaoxing session, parsing and cancellation tests; no external services."""

import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from app.adapters.chaoxing import ChaoxingAdapter, CourseHTMLParser
from app.models import AdapterError, Course
from app.settings import SettingsStore
from desktop_bridge import handle


CARD = '''<div class="course"><div><a href="javascript:void(0)"><span
class="course-name">Test &amp; Course</span></a></div>
<input class="courseId" value="123"><input class="clazzId" value="456">
<input class="curPersonId" value="789"><p class="color3" title="Teacher"></p></div>'''


def response(body, status=200, url="https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"):
    return Mock(status=status, url=url, text=Mock(return_value=body))


class ChaoxingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SettingsStore(Path(self.temp.name) / "config.json")
        self.adapter = ChaoxingAdapter(self.store)
        self.adapter._context = Mock()
        self.adapter._context.cookies.return_value = [{"name": "_uid", "value": "test-only"}]

    def test_course_cards_keep_class_identity_and_ignore_untrusted_hrefs(self):
        course = self.adapter._courses_from_html(CARD)[0]
        self.assertEqual(course.course_id, "123:456:789")
        self.assertEqual(course.name, "Test & Course")
        self.assertEqual(course.metadata["teacher"], "Teacher")
        self.assertTrue(course.metadata["url"].startswith(self.adapter.course_base))
        self.assertNotIn("javascript", course.metadata["url"])
        self.assertEqual(self.adapter._courses_from_html(CARD.replace('value="123"', 'value="broken"')), [])

    def test_duplicate_courses_and_folders(self):
        self.adapter._context.request.get.return_value = response('<div class="course-list"></div><li fileid="2"></li>')
        with patch.object(self.adapter, "_course_html", return_value=CARD) as fetch:
            courses = self.adapter._fetch_courses()
        self.assertEqual(len(courses), 1)
        self.assertEqual(fetch.call_count, 2)

    def test_403_and_login_redirects_are_never_success(self):
        for r in (response("Forbidden", 403), response("login", url="https://passport2.chaoxing.com/login"),
                  response('<title>用户登录</title>')):
            with self.subTest(status=r.status, url=r.url), self.assertRaises(AdapterError):
                self.adapter._response_html(r)

    def test_unknown_200_is_not_an_empty_course_list(self):
        self.adapter._context.request.get.return_value = response("<h1>Proxy Error</h1>")
        with self.assertRaisesRegex(AdapterError, "课程页"):
            self.adapter._fetch_courses()

    def test_empty_courses_require_recognized_authenticated_page(self):
        self.adapter._context.request.get.return_value = response('<div class="course-list"></div>')
        with patch.object(self.adapter, "_course_html", return_value=""):
            self.assertEqual(self.adapter._fetch_courses(), [])
        with patch.object(self.adapter, "_course_html", return_value="<h1>Gateway Error</h1>"):
            with self.assertRaisesRegex(AdapterError, "数据格式"):
                self.adapter._fetch_courses()

    def test_atomic_storage_and_disabling_persistence(self):
        self.adapter._context.storage_state.return_value = {"cookies": [{"name": "test-only"}], "origins": []}
        self.adapter._save_session()
        self.assertEqual(json.loads(self.adapter.session_path.read_text())["origins"], [])
        self.assertEqual(list(self.adapter.session_path.parent.glob("*.tmp")), [])
        settings = self.store.load()
        settings["chaoxing"]["save_session"] = False
        self.adapter.apply_settings(settings)
        self.assertFalse(self.adapter.session_path.exists())
        self.adapter._save_session()
        self.assertFalse(self.adapter.session_path.exists())

    def test_cookie_alone_cannot_claim_login_success(self):
        adapter = self.adapter
        adapter._page = Mock()
        adapter._page.is_closed.return_value = True
        with patch.object(adapter, "_ensure_browser"), \
                patch.object(adapter, "_fetch_courses", side_effect=AdapterError("HTTP 403")), \
                patch.object(adapter, "_save_session") as save:
            with self.assertRaisesRegex(AdapterError, "未完成登录"):
                adapter.login()
            save.assert_not_called()

    def test_cancel_before_any_login_or_course_network(self):
        event = Event()
        event.set()
        with patch.object(self.adapter, "_ensure_browser") as browser:
            with self.assertRaisesRegex(AdapterError, "已停止"):
                self.adapter.login(cancel_event=event)
            with self.assertRaisesRegex(AdapterError, "已停止"):
                self.adapter.run_course(Course("chaoxing", "1", "Test"), cancel_event=event)
            browser.assert_not_called()

    def test_unknown_course_never_navigates_ui_supplied_url(self):
        with patch.object(self.adapter, "list_courses", return_value=[]):
            with self.assertRaisesRegex(AdapterError, "没有该课程"):
                self.adapter.run_course(Course("chaoxing", "1", "Test", {"url": "https://example.com"}))

    def test_bridge_carries_cancellation_and_real_auth_result(self):
        adapter = Mock()
        adapter.login.return_value = False
        event = Event()
        result = handle({"operation": "login", "platform": "chaoxing"}, {"chaoxing": adapter}, cancel_event=event)
        self.assertFalse(result["result"])
        self.assertIs(adapter.login.call_args.args[1], event)
        handle({"operation": "run", "platform": "chaoxing", "course": {"courseId": "123"}},
               {"chaoxing": adapter}, cancel_event=event)
        self.assertIs(adapter.run_course.call_args.args[2], event)
        self.assertTrue(handle({"operation": "logout", "platform": "chaoxing"}, {"chaoxing": adapter})["ok"])
        adapter.logout.assert_called_once()

    def test_settings_validation_and_preservation(self):
        values = self.store.load()
        values["chaoxing"]["future"] = "retained"
        saved = self.store.save(values)
        self.assertEqual(saved["chaoxing"]["future"], "retained")
        saved = self.store.save({"chaoxing": {"playback_rate": 99}})
        self.assertEqual(saved["chaoxing"]["playback_rate"], 99)
        before = self.store.path.read_bytes()
        for changes in ({"browser": "bad"}, {"playback_rate": 0.4}, {"login_timeout": 0}, {"muted": "true"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.save({"chaoxing": changes})
            self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
