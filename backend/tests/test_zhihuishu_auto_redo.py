# -*- coding: utf-8 -*-
"""已交卷未满分的卷子：开刷前自动用 1 次重做机会（saveCourseTran）换新卷面。

覆盖三层：
  * app.zhihuishu_exam.HomeworkRunner._maybe_redo 的判定（已交卷 / 满分 / 次数 / 失败降级）
  * app.adapters.zhihuishu 把 auto_redo 接线给 HomeworkRunner
  * desktop_bridge 把 C# 请求里的 autoRedo 透传给 adapter
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.adapters.zhihuishu import ZhihuishuAdapter
from app.models import Course
from app.zhihuishu_exam import HomeworkResult, HomeworkRunner, HomeworkTarget
from desktop_bridge import handle


def _target() -> HomeworkTarget:
    return HomeworkTarget(
        recruit_id="473151",
        exam_id="ezr92lGe",
        stu_exam_id="5jOVWnR8",
        school_id="1",
        course_id="1000006252",
        name="第一章 单元测试",
    )


class FakeClient:
    """只实现 _maybe_redo 用到的三个只读/写接口。"""

    def __init__(
        self,
        *,
        submitted: bool = True,
        score: float | None = 8,
        total: float | None = 10,
        used: int = 0,
        apply_error: Exception | None = None,
        status_error: Exception | None = None,
    ) -> None:
        self.submitted = submitted
        self.score = score
        self.total = total
        self.used = used
        self.apply_error = apply_error
        self.status_error = status_error
        self.calls: list[str] = []

    def homework_status(self, target):
        self.calls.append("homework_status")
        if self.status_error:
            raise self.status_error
        return {
            "submitted": self.submitted,
            "score": self.score,
            "totalScore": self.total,
            "source": "lookHomework",
        }

    def paper(self, target):
        self.calls.append("paper")
        raise RuntimeError("paper is not used in this unit test")

    def redo_used(self, target):
        self.calls.append("redo_used")
        return self.used

    def apply_redo(self, target, *, confirm: bool = False):
        self.calls.append("apply_redo")
        assert confirm is True, "写操作必须显式确认"
        if self.apply_error:
            raise self.apply_error
        self.used += 1
        return {"rt": 1}


def _runner(client: FakeClient, **kwargs) -> HomeworkRunner:
    kwargs.setdefault("auto_redo", True)
    kwargs.setdefault("delay", 0.0)
    return HomeworkRunner(client, **kwargs)


class MaybeRedoTest(unittest.TestCase):
    def _run(self, client: FakeClient, **kwargs) -> HomeworkResult:
        runner = _runner(client, **kwargs)
        result = HomeworkResult(target=_target())
        runner._maybe_redo(_target(), result)
        return result

    def test_submitted_not_full_consumes_one_chance(self):
        client = FakeClient(submitted=True, score=8, total=10, used=0)
        result = self._run(client)
        self.assertEqual(client.calls, ["homework_status", "redo_used", "apply_redo"])
        self.assertTrue(result.redo["applied"])
        self.assertEqual(result.redo["remainBefore"], 3)
        self.assertEqual(result.redo["remainAfter"], 2)

    def test_full_score_does_not_waste_chance(self):
        client = FakeClient(submitted=True, score=10, total=10)
        result = self._run(client)
        self.assertNotIn("apply_redo", client.calls)
        self.assertTrue(result.redo["full"])
        self.assertFalse(result.redo["applied"])

    def test_unsubmitted_paper_is_left_alone(self):
        client = FakeClient(submitted=False, score=None, total=10)
        result = self._run(client)
        self.assertEqual(client.calls, ["homework_status"])
        self.assertFalse(result.redo["applied"])
        self.assertFalse(result.redo["submitted"])

    def test_no_chance_left_keeps_original_paper(self):
        client = FakeClient(submitted=True, score=6, total=10, used=3)
        result = self._run(client)
        self.assertNotIn("apply_redo", client.calls)
        self.assertFalse(result.redo["applied"])
        self.assertEqual(result.redo["remainBefore"], 0)

    def test_unknown_score_does_not_waste_chance(self):
        """已交卷但服务端不给分（见面课/视频课作业）：判不了满分就不动，避免覆盖满分卷。"""
        client = FakeClient(submitted=True, score=None, total=None)
        messages: list[str] = []
        result = self._run(client, progress=messages.append)
        self.assertNotIn("apply_redo", client.calls)
        self.assertFalse(result.redo["applied"])
        self.assertEqual(result.redo["skippedReason"], "score_unknown")
        joined = "\n".join(getattr(m, "message", str(m)) for m in messages)
        self.assertIn("拿不到卷面分", joined)

    def test_always_redoes_when_score_unknown(self):
        client = FakeClient(submitted=True, score=None, total=None)
        result = self._run(client, redo_when="always")
        self.assertIn("apply_redo", client.calls)
        self.assertTrue(result.redo["applied"])

    def test_redo_when_off_disables_write(self):
        client = FakeClient(submitted=True, score=6, total=10)
        result = self._run(client, redo_when="off")
        self.assertNotIn("apply_redo", client.calls)
        self.assertFalse(result.redo["applied"])

    def test_always_redoes_even_when_full(self):
        client = FakeClient(submitted=True, score=10, total=10)
        result = self._run(client, redo_when="always")
        self.assertIn("apply_redo", client.calls)
        self.assertTrue(result.redo["applied"])

    def test_apply_failure_degrades_to_warning_only(self):
        from app.zhihuishu_exam import ZhihuishuExamError

        client = FakeClient(
            submitted=True, score=6, total=10, apply_error=ZhihuishuExamError("申请重做失败：rt=0")
        )
        messages: list[str] = []
        result = self._run(client, progress=messages.append)
        self.assertFalse(result.redo["applied"])
        joined = "\n".join(getattr(m, "message", str(m)) for m in messages)
        self.assertIn("申请重做失败", joined)

    def test_status_failure_skips_redo(self):
        from app.zhihuishu_exam import ZhihuishuExamError

        client = FakeClient(status_error=ZhihuishuExamError("boom"))
        messages: list[str] = []
        result = self._run(client, progress=messages.append)
        self.assertNotIn("apply_redo", client.calls)
        self.assertFalse(result.redo["applied"])
        joined = "\n".join(getattr(m, "message", str(m)) for m in messages)
        self.assertIn("跳过自动重做", joined)

    def test_runner_run_checks_redo_before_paper(self):
        """run() 必须在取卷之前就重做，否则旧卷的已交卷状态会挡住写答案。"""
        client = FakeClient(submitted=True, score=8, total=10)
        runner = _runner(client)
        with self.assertRaises(RuntimeError):
            runner.run(_target(), submit=False)
        self.assertEqual(client.calls, ["homework_status", "redo_used", "apply_redo", "paper"])


class AdapterWiringTest(unittest.TestCase):
    def _patch(self, adapter, runner_cls):
        return [
            mock.patch.object(adapter, "_exam_client", return_value=object()),
            mock.patch("app.zhihuishu_answer_bank.AnswerBank", return_value=mock.Mock(answer_labels=lambda q: None)),
            mock.patch(
                "app.zhihuishu_answer_bank.AnswerImageLookup",
                return_value=mock.Mock(count=0, answer_labels=lambda q: None),
            ),
            mock.patch("app.zhihuishu_answer_bank.fetch_answer_images", return_value={"items": []}),
            mock.patch("app.zhihuishu_exam.HomeworkRunner", runner_cls),
        ]

    def _run(self, **request):
        adapter = ZhihuishuAdapter({"cookie_path": "cookies.json"})
        captured: dict = {}

        class FakeRunner:
            def __init__(self, client, **kwargs):
                captured.update(kwargs)
                self.auto_redo = bool(kwargs.get("auto_redo"))

            def run(self, target, *, submit=False):
                result = HomeworkResult(target=target)
                result.redo = {"checked": True, "applied": self.auto_redo and request.get("applied", True)}
                return result

        patches = self._patch(adapter, FakeRunner)
        for p in patches:
            p.start()
        try:
            payload = adapter.run_homework(
                {
                    "recruitId": "473151",
                    "examId": "ezr92lGe",
                    "studentExamId": "5jOVWnR8",
                    "schoolId": "1",
                    "courseId": "1000006252",
                    "name": "第一章",
                },
                ai={"enabled": False},
                **request,
            )
        finally:
            for p in patches:
                p.stop()
        return captured, payload

    def test_default_is_auto_redo_enabled(self):
        captured, payload = self._run()
        self.assertTrue(captured["auto_redo"])
        self.assertEqual(captured["redo_when"], "not_full")
        self.assertEqual(captured["redo_max"], 3)
        self.assertTrue(payload["autoRedo"])

    def test_explicit_false_disables(self):
        captured, payload = self._run(auto_redo=False)
        self.assertFalse(captured["auto_redo"])
        self.assertFalse(payload["autoRedo"])

    def test_explicit_window_overrides_settings(self):
        captured, _ = self._run(auto_redo=True, redo_when="always", redo_max=2)
        self.assertEqual(captured["redo_when"], "always")
        self.assertEqual(captured["redo_max"], 2)


class RunHomeworksRedoSummaryTest(unittest.TestCase):
    def test_summary_counts_applied_redo(self):
        adapter = ZhihuishuAdapter({"cookie_path": "cookies.json"})
        tasks = [{"name": "第1章", "examId": "e1"}, {"name": "第2章", "examId": "e2"}]
        results = [
            {"submitted": True, "saved": {}, "skipped": {}, "pending": [], "failed": {},
             "redo": {"applied": True, "score": 8, "totalScore": 10}},
            {"submitted": True, "saved": {}, "skipped": {}, "pending": [], "failed": {},
             "redo": {"applied": False}},
        ]
        events = []
        with mock.patch.object(adapter, "list_homeworks", return_value=tasks), mock.patch.object(
            adapter, "run_homework", side_effect=results
        ):
            summary = adapter.run_homeworks(
                Course(platform="zhihuishu", course_id="1000006252", name="示范课程"),
                progress=events.append,
            )
        self.assertEqual(summary["redoApplied"], 1)
        self.assertEqual(summary["redoItems"][0]["name"], "第1章")
        self.assertEqual(summary["redoItems"][0]["score"], 8)
        joined = "\n".join(getattr(e, "message", str(e)) for e in events)
        self.assertIn("自动重做 1 份", joined)
        self.assertIn("已用 1 次重做机会换新卷", joined)


class BridgeForwardingTest(unittest.TestCase):
    def test_bridge_passes_auto_redo_to_single_homework(self):
        captured: dict = {}

        class FakeAdapter:
            display_name = "智慧树"
            supports_course_listing = True
            supports_course_execution = True

            def run_homework(self, homework, **kwargs):
                captured.update(kwargs)
                return {"submitted": True}

        response = handle(
            {
                "operation": "run_homework",
                "platform": "zhihuishu",
                "homework": {"recruitId": "1"},
                "autoSubmit": False,
                "autoRedo": False,
                "redoWhen": "always",
                "redoMax": 2,
            },
            {"zhihuishu": FakeAdapter()},
        )
        self.assertTrue(response["ok"])
        self.assertIs(captured["auto_redo"], False)
        self.assertEqual(captured["redo_when"], "always")
        self.assertEqual(captured["redo_max"], 2)

    def test_bridge_passes_auto_redo_to_whole_course(self):
        captured: dict = {}

        class FakeAdapter:
            display_name = "智慧树"
            supports_course_listing = True
            supports_course_execution = True

            def run_homeworks(self, course, **kwargs):
                captured.update(kwargs)
                return {"completed": 0}

        response = handle(
            {
                "operation": "run_homeworks",
                "platform": "zhihuishu",
                "course": {"courseId": "1000006252", "name": "示范课程"},
                "autoSubmit": False,
                "autoRedo": True,
            },
            {"zhihuishu": FakeAdapter()},
        )
        self.assertTrue(response["ok"])
        self.assertIs(captured["auto_redo"], True)

    def test_bridge_default_leaves_adapter_default(self):
        captured: dict = {}

        class FakeAdapter:
            display_name = "智慧树"
            supports_course_listing = True
            supports_course_execution = True

            def run_homework(self, homework, **kwargs):
                captured.update(kwargs)
                return {}

        handle({"operation": "run_homework", "platform": "zhihuishu", "homework": {}}, {"zhihuishu": FakeAdapter()})
        self.assertNotIn("auto_redo", captured)


if __name__ == "__main__":
    unittest.main()
