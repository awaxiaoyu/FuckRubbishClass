"""Exercise playback -> adapter -> JSONL progress without network or sleeps."""

from contextlib import redirect_stdout
import io
import sys
import types
import unittest
from unittest.mock import Mock, patch

from ObjDict import ObjDict
from requests.cookies import cookiejar_from_dict

from app.adapters.zhihuishu import ZhihuishuAdapter
from app.models import AdapterError
from desktop_bridge import handle
from fucker import Fucker


class ZhihuishuProgressTests(unittest.TestCase):
    def setUp(self):
        videos = [
            {"videoId": 1, "name": "已完成视频", "videoSec": 4,
             "studyTotalTime": 0, "watchState": 1},
            {"videoId": 2, "name": "当前视频", "videoSec": 4,
             "studyTotalTime": 2, "watchState": 0},
        ]
        ctx = ObjDict({
            "course": {"courseInfo": {"name": "测试课程"}},
            "chapters": {"videoChapterDtos": [{"name": "章节", "videoLessons": [
                {"name": "课时", "videoSmallLessons": videos},
            ]}]},
            "videos": {video["videoId"]: video for video in videos},
            "fucked_time": 0,
        })
        # Bypass constructor side effects; keep the real course and playback loop.
        engine = Fucker.__new__(Fucker)
        engine._cookies = cookiejar_from_dict({"session": "test"})
        engine.tree_view = engine.progressbar_view = False
        engine.end_thre = engine.speed = 1
        engine.prefix = "  |"
        engine._pushplus = engine._bark = Mock()
        engine.getZhidaoContext = Mock(return_value=ctx)
        for method in ("_checkCookies", "_checkTimeLimit", "_sessionReady",
                       "watchVideo", "threeDimensionalCourseWare",
                       "saveDatabaseIntervalTimeV2"):
            setattr(engine, method, Mock())
        engine.prelearningNote = Mock(return_value=ObjDict({"studiedLessonDto": {"id": 1}}))
        engine.loadVideoPointerInfo = Mock(return_value=ObjDict({"questionPoint": []}))
        self.engine = engine
        self.adapter = ZhihuishuAdapter({"save_cookies": False})
        self.adapter._fucker = engine
        self.events = []

    def run_course(self):
        with patch("fucker.time.sleep"), patch("fucker.random", return_value=1), \
                redirect_stdout(io.StringIO()):
            return handle({"operation": "run", "platform": "zhihuishu", "course": {
                "courseId": "zhidao:shared-test", "name": "测试课程",
                "metadata": {"kind": "zhidao", "native_id": "shared-test"},
            }}, {"zhihuishu": self.adapter}, on_progress=self.events.append)

    def test_existing_and_partial_video_progress_streams_each_tick(self):
        response = self.run_course()
        playback = [event for event in self.events if event.get("total") == 2]

        self.assertTrue(response["ok"])
        self.assertEqual([event["current"] for event in playback], [1.5, 1.5, 1.75, 2])
        self.assertIn("已完成 1/2 个视频", playback[0]["message"])
        self.assertIn("当前视频", playback[2]["message"])
        self.assertIn("0:00:03 / 0:00:04", playback[2]["message"])
        self.assertEqual(self.events[-1]["current"], self.events[-1]["total"])
        self.engine.saveDatabaseIntervalTimeV2.assert_called_once()

    def test_failed_progress_report_does_not_mark_video_complete(self):
        self.engine.saveDatabaseIntervalTimeV2.side_effect = RuntimeError("report failed")

        with self.assertRaisesRegex(AdapterError, "出现错误"):
            self.run_course()

        playback = [event for event in self.events if event.get("total") == 2]
        # 上报全部失败 ⇒ 对外只能报“服务端已确认”的进度：v1=1 个完成 + v2=2s/4s=0.5 ⇒ 1.5，不得到 2
        self.assertEqual(playback[-1]["current"], 1.5)
        self.assertFalse(any(event["current"] == event["total"] for event in playback))

    def test_zhidao_query_retries_same_request_after_captcha_callback(self):
        from ObjDict import ObjDict

        self.engine.captcha_handler = Mock(return_value=True)
        self.engine._apiQuery = Mock(side_effect=[
            ObjDict({"code": -12}),
            ObjDict({"code": 0, "data": "saved"}),
        ])

        result = self.engine.zhidaoQuery(
            "https://studyservice-api.zhihuishu.com/progress",
            {"playedTime": 11}, encrypt=False, setTimeStamp=False,
        )

        self.assertEqual(result.data, "saved")
        self.assertEqual(self.engine._apiQuery.call_count, 2)
        self.engine.captcha_handler.assert_called_once_with()

    def test_captcha_browser_imports_and_returns_session_cookies(self):
        calls = {}

        class Page:
            def set_default_timeout(self, _timeout):
                pass

            def expose_function(self, name, callback):
                calls["continue_name"] = name
                calls["continue"] = callback

            def goto(self, url, **_kwargs):
                calls["url"] = url

            def bring_to_front(self):
                pass

            def is_closed(self):
                return False

            def evaluate(self, _script):
                if "userAgentData" in _script:
                    return {"userAgent": "Mozilla/5.0 Edge", "brands": [
                        {"brand": "Microsoft Edge", "version": "140"},
                    ], "mobile": False, "platform": "Windows"}
                calls["continue"]()

            def wait_for_timeout(self, _timeout):
                pass

        class Context:
            def add_cookies(self, cookies):
                calls["cookies_in"] = cookies

            def new_page(self):
                return Page()

            def cookies(self):
                return [{"name": "session", "value": "verified", "domain": ".zhihuishu.com",
                         "path": "/", "secure": True, "httpOnly": True}]

        class Browser:
            def new_context(self, **_kwargs):
                return Context()

            def is_connected(self):
                return True

            def close(self):
                pass

        class Chromium:
            def launch(self, **kwargs):
                calls["launch"] = kwargs
                return Browser()

        fake_playwright = types.ModuleType("playwright.sync_api")
        fake_playwright.sync_playwright = lambda: types.SimpleNamespace(
            start=lambda: types.SimpleNamespace(chromium=Chromium(), stop=lambda: None)
        )
        engine = self.engine
        engine.session = types.SimpleNamespace(cookies=engine.cookies.copy())
        engine.session.headers = {}
        engine.headers = {}
        engine.proxies = {}
        engine.context = ObjDict({"shared-test": ObjDict({"cookies": engine.cookies.copy()})})
        with patch.dict(sys.modules, {"playwright.sync_api": fake_playwright}), \
                patch.object(self.adapter, "_save_cookies") as save:
            completed = self.adapter._open_captcha_browser("shared-test", self.events.append, None)

        self.assertTrue(completed)
        self.assertFalse(calls["launch"]["headless"])
        self.assertIn("recruitAndCourseId=shared-test", calls["url"])
        self.assertEqual(calls["cookies_in"][0]["name"], "session")
        self.assertEqual(engine.session.cookies.get("session"), "verified")
        self.assertEqual(engine.context["shared-test"].cookies.get("session"), "verified")
        self.assertEqual(engine.headers["User-Agent"], "Mozilla/5.0 Edge")
        self.assertEqual(engine.context["shared-test"].headers["sec-ch-ua-platform"], '"Windows"')
        self.assertEqual(calls["continue_name"], "courseHubCaptchaContinue")
        save.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
