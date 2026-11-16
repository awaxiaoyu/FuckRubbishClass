"""Focused tests for the platform boundary, independent of network services."""

import sys
import types
import unittest
import logging
from unittest.mock import patch
from types import SimpleNamespace

from app.adapters import ChaoxingAdapter, ZhihuishuAdapter
from app.models import AdapterError, Course


class FakeFucker:
    instances = []

    def __init__(self, **_kwargs):
        self.cookies = {"session": "test"}
        self.course_calls = []
        self.ai_calls = []
        self.instances.append(self)

    def fuckCourse(self, course_id):
        self.course_calls.append(course_id)

    def fuckAiCourse(self, course_id, class_id, aiConfig=None, no_exam=False):
        self.ai_calls.append((course_id, class_id, no_exam, aiConfig))

    def getZhidaoList(self):
        return [SimpleNamespace(secret="shared-1", courseName="共享课程")]

    def getHikeList(self):
        return [SimpleNamespace(courseId=2, courseName="校内课程")]

    def getPolymasList(self):
        return [
            # 与共享课重复的镜像条目，应被去重
            {
                "courseId": "1000006196",
                "courseName": "共享课程",
                "courseType": "shareCourse",
                "linkUrl": "https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=shared-1",
                "origenParam": {"secret": "shared-1", "courseId": "1000006196"},
            },
            # 真正的 Polymas 智课
            SimpleNamespace(
                courseId=3, courseName="Polymas 课程",
                courseType="aiCourse", linkUrl="https://hike-teaching-center.polymas.com/x",
                origenParam=None, secret=None,
            ),
        ]

    def getZhidaoAiList(self):
        return [SimpleNamespace(courseId=123, classId=456, courseName="知识点 AI 课")]

    @staticmethod
    def normalizeZhidaoAiCourse(item):
        return item


def _install_fake_fucker():
    FakeFucker.instances.clear()
    fake_module = types.ModuleType("fucker")
    fake_module.Fucker = FakeFucker
    fake_logger_module = types.ModuleType("logger")
    fake_logger_module.logger = SimpleNamespace(
        addHandler=lambda _handler: None,
        removeHandler=lambda _handler: None,
    )
    original = sys.modules.get("fucker")
    original_logger = sys.modules.get("logger")
    sys.modules["fucker"] = fake_module
    sys.modules["logger"] = fake_logger_module
    return original, original_logger


def _restore_modules(original, original_logger):
    if original is None:
        sys.modules.pop("fucker", None)
    else:
        sys.modules["fucker"] = original
    if original_logger is None:
        sys.modules.pop("logger", None)
    else:
        sys.modules["logger"] = original_logger


class AdapterTests(unittest.TestCase):
    def test_zhihuishu_courses_are_normalized(self):
        original, original_logger = _install_fake_fucker()
        try:
            adapter = ZhihuishuAdapter({
                "cookie_path": "missing-test-cookies.json",
                "save_cookies": False,
                "ai": {"enabled": True, "use_zhidao_ai": True, "use_stream": True,
                       "openai": {"api_base": "", "api_key": "", "model_name": ""},
                       "ppt_processing": {"provide_to_ai": False, "moonShot": {
                           "base_url": "", "api_key": "", "delete_after_convert": True}}},
            })
            courses = adapter.list_courses()
            adapter.run_course(courses[2])
            adapter.run_course(courses[3])
        finally:
            _restore_modules(original, original_logger)

        self.assertEqual([course.course_id for course in courses], [
            "zhidao:shared-1", "hike:2", "polymas:3", "zhidao_ai:123:456"
        ])
        self.assertEqual([course.metadata["kind"] for course in courses], [
            "zhidao", "hike", "polymas", "zhidao_ai"
        ])
        self.assertEqual(courses[3].metadata["class_id"], "456")
        self.assertEqual(FakeFucker.instances[0].course_calls, ["polymas:3"])
        self.assertEqual(FakeFucker.instances[0].ai_calls[0][:3], (123, 456, False))

    def test_polymas_share_mirrors_are_deduped(self):
        original, original_logger = _install_fake_fucker()
        try:
            adapter = ZhihuishuAdapter({"cookie_path": "missing-test-cookies.json", "save_cookies": False})
            courses = adapter.list_courses()
        finally:
            _restore_modules(original, original_logger)
        names = [c.name for c in courses]
        self.assertEqual(names.count("共享课程"), 1)
        self.assertTrue(any(c.course_id.startswith("polymas:") for c in courses))

    def test_slider_captcha_is_reported_as_actionable_warning(self):
        handlers = []

        class CaptchaFucker(FakeFucker):
            def fuckCourse(self, _course_id):
                record = logging.LogRecord(
                    "test", logging.ERROR, "fucker.py", 1, "captcha required", (), None,
                )
                for handler in handlers:
                    handler.handle(record)

        fake_module = types.ModuleType("fucker")
        fake_module.Fucker = CaptchaFucker
        fake_logger_module = types.ModuleType("logger")
        fake_logger_module.logger = SimpleNamespace(
            addHandler=handlers.append,
            removeHandler=handlers.remove,
        )
        original = sys.modules.get("fucker")
        original_logger = sys.modules.get("logger")
        sys.modules["fucker"] = fake_module
        sys.modules["logger"] = fake_logger_module
        try:
            adapter = ZhihuishuAdapter({"save_cookies": False})
            events = []
            with self.assertRaisesRegex(AdapterError, "要求滑块验证"):
                adapter.run_course(
                    Course("zhihuishu", "zhidao:shared-1", "共享课程",
                           {"kind": "zhidao", "native_id": "shared-1"}),
                    events.append,
                )
        finally:
            if original is None:
                sys.modules.pop("fucker", None)
            else:
                sys.modules["fucker"] = original
            if original_logger is None:
                sys.modules.pop("logger", None)
            else:
                sys.modules["logger"] = original_logger

        self.assertTrue(any("Playwright 窗口" in event.message
                            and event.level == "warning" for event in events))

    def test_zhihuishu_multi_window_error_is_preserved_for_the_ui(self):
        handlers = []

        class MultiWindowFucker(FakeFucker):
            def fuckCourse(self, _course_id):
                record = logging.LogRecord(
                    "test", logging.ERROR, "fucker.py", 1,
                    "code: -10 msg: 疑是多窗口观看", (), None,
                )
                for handler in handlers:
                    handler.handle(record)

        fake_module = types.ModuleType("fucker")
        fake_module.Fucker = MultiWindowFucker
        fake_logger_module = types.ModuleType("logger")
        fake_logger_module.logger = SimpleNamespace(
            addHandler=handlers.append,
            removeHandler=handlers.remove,
        )
        original = sys.modules.get("fucker")
        original_logger = sys.modules.get("logger")
        sys.modules["fucker"] = fake_module
        sys.modules["logger"] = fake_logger_module
        try:
            adapter = ZhihuishuAdapter({"save_cookies": False})
            with self.assertRaisesRegex(AdapterError, "疑是多窗口观看") as caught:
                adapter.run_course(
                    Course("zhihuishu", "zhidao:shared-1", "共享课程",
                           {"kind": "zhidao", "native_id": "shared-1"}),
                )
        finally:
            if original is None:
                sys.modules.pop("fucker", None)
            else:
                sys.modules["fucker"] = original
            if original_logger is None:
                sys.modules.pop("logger", None)
            else:
                sys.modules["logger"] = original_logger

        # 错误文案随后续迭代演进了，这里只断言「可操作的根因 + 提示」都在，避免锁死措辞。
        message = str(caught.exception)
        self.assertIn("code -10", message)
        self.assertIn("多窗口", message)
        self.assertIn("重试", message)
        self.assertNotIn("请查看上方日志", str(caught.exception))

    def test_chaoxing_lists_courses_only_with_a_session(self):
        adapter = ChaoxingAdapter()
        self.assertTrue(adapter.supports_course_listing)
        with patch.object(adapter, "_ensure_browser"), patch.object(adapter, "_has_identity", return_value=False):
            with self.assertRaisesRegex(AdapterError, "登录"):
                adapter.list_courses()

    def test_chaoxing_login_uses_managed_session_not_default_browser(self):
        adapter = ChaoxingAdapter()
        with patch("app.adapters.chaoxing.webbrowser.open") as browser, \
                patch.object(adapter, "_ensure_browser"), \
                patch.object(adapter, "_has_identity", return_value=True), \
                patch.object(adapter, "_fetch_courses", return_value=[]), \
                patch.object(adapter, "_save_session") as save:
            self.assertTrue(adapter.login())
            save.assert_called_once()
            browser.assert_not_called()

    def test_saved_session_respects_cookie_persistence_setting(self):
        adapter = ZhihuishuAdapter({"save_cookies": False, "cookie_path": "cookies.json"})
        self.assertFalse(adapter.has_saved_session)

    def test_zhihuishu_homework_batch_continues_after_one_test_fails(self):
        adapter = ZhihuishuAdapter({"save_cookies": False})
        course = Course("zhihuishu", "zhidao:course-1", "整门课", {"kind": "zhidao"})
        tasks = [
            {"studentExamId": "one", "name": "第一份"},
            {"studentExamId": "two", "name": "第二份"},
            {"studentExamId": "three", "name": "第三份"},
        ]
        results = [
            {"examName": "第一份", "saved": {"a": "A"}, "skipped": {}, "pending": [],
             "failed": {}, "submitted": False, "cancelled": False},
            AdapterError("服务端暂时拒绝"),
            {"examName": "第三份", "saved": {}, "skipped": {"b": "B"}, "pending": [],
             "failed": {}, "submitted": False, "cancelled": False},
        ]
        events = []
        with patch.object(adapter, "list_homeworks", return_value=tasks), \
                patch.object(adapter, "run_homework", side_effect=results):
            summary = adapter.run_homeworks(course, progress=events.append)

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["saved"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["items"][1]["error"], "服务端暂时拒绝")
        self.assertTrue(any("继续下一份" in event.message for event in events))


if __name__ == "__main__":
    unittest.main()
