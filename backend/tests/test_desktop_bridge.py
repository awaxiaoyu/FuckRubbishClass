import base64
import io
import json
import unittest
from unittest.mock import patch

from app.models import ProgressEvent
import desktop_bridge
from desktop_bridge import _event


class DesktopBridgeTests(unittest.TestCase):
    def test_qr_image_is_encoded_for_the_wpf_client(self):
        image = b"test-png-bytes"

        event = _event(ProgressEvent("二维码已显示", level="qr", qr_image=image))

        self.assertEqual(event["qrImageBase64"], base64.b64encode(image).decode("ascii"))

    def test_system_exit_from_adapter_is_returned_over_jsonl(self):
        class Adapter:
            display_name = "测试平台"
            supports_course_listing = True
            supports_course_execution = True

            def run_course(self, *_args):
                raise SystemExit(17)

        request = json.dumps({
            "operation": "run", "platform": "fake",
            "course": {"courseId": "1", "name": "测试课程"},
        })
        stdout = io.StringIO()
        with patch.object(desktop_bridge, "_adapters", return_value={"fake": Adapter()}), \
                patch("sys.stdin", io.StringIO(request + "\n")), \
                patch("sys.stdout", stdout):
            exit_code = desktop_bridge.main()

        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(exit_code, 0)
        self.assertEqual(frames, [{"ok": False, "error": "adapter unexpectedly raised SystemExit (17)"}])


    def test_zhihuishu_homework_operations_are_dispatched(self):
        class HomeworkAdapter:
            display_name = "智慧树 / 知到"
            supports_course_listing = True
            supports_course_execution = True
            calls = []

            def list_homeworks(self, course, *, progress=None):
                self.calls.append(("list_homeworks", course.course_id, course.metadata))
                if progress:
                    progress(ProgressEvent(f"读取《{course.name}》章节", current=0, total=1))
                return [{
                    "platform": "zhihuishu",
                    "correlationId": "1000006196:5jOVWnR8",
                    "name": "第一章单元测试",
                    "chapterName": "传承弘扬伟大长征精神，走好新时代长征路",
                    "deadline": "2026-12-11 23:59:59",
                    "recruitId": "473252",
                    "examId": "ezr92lGe",
                    "studentExamId": "5jOVWnR8",
                    "schoolId": "2351",
                    "courseId": "1000006196",
                }]

            def run_homework(self, task, *, auto_submit=None, progress=None, cancel_event=None):
                self.calls.append(("run_homework", task["examId"], auto_submit, cancel_event))
                return {"saved": {"eid": "1"}, "skipped": {}, "failed": {}, "pending": [], "submitted": False, "achieved": 1}

        adapter = HomeworkAdapter()
        streamed = []
        listed = desktop_bridge.handle(
            {
                "operation": "list_homeworks",
                "platform": "zhihuishu",
                "course": {"courseId": "1000006196", "name": "形势与政策", "metadata": {"native_id": "473252"}},
            },
            {"zhihuishu": adapter},
            on_progress=streamed.append,
        )

        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["homeworks"]), 1)
        self.assertEqual(listed["homeworks"][0]["examId"], "ezr92lGe")
        self.assertEqual(listed["events"][0]["message"], "读取《形势与政策》章节")
        self.assertEqual(streamed[0]["type"], "progress")
        # 每份作业都带着前端 HomeWorkRow 需要的定位字段，run_homework 能直接吃回去。
        for key in ("correlationId", "examId", "studentExamId", "schoolId", "recruitId", "courseId", "deadline"):
            self.assertIn(key, listed["homeworks"][0])

        ran = desktop_bridge.handle(
            {
                "operation": "run_homework",
                "platform": "zhihuishu",
                "homework": listed["homeworks"][0],
                "autoSubmit": True,
            },
            {"zhihuishu": adapter},
        )
        self.assertTrue(ran["ok"])
        self.assertEqual(ran["result"]["saved"], {"eid": "1"})
        self.assertFalse(ran["result"]["submitted"])
        self.assertEqual(adapter.calls[1][1], "ezr92lGe")
        self.assertTrue(adapter.calls[1][2])          # autoSubmit 透传
        self.assertIsNone(adapter.calls[1][3])        # 未传 cancel 信号时保持 None

    def test_homework_operations_need_adapter_support(self):
        class VideoOnlyAdapter:
            display_name = "智慧树 / 知到"
            supports_course_listing = True
            supports_course_execution = True

        response = desktop_bridge.handle(
            {"operation": "list_homeworks", "platform": "zhihuishu", "course": {"courseId": "1"}},
            {"zhihuishu": VideoOnlyAdapter()},
        )

        self.assertFalse(response["ok"])
        self.assertIn("平时测试", response["error"])

    def test_homework_confirmation_callback_is_forwarded_to_adapter(self):
        class ConfirmingAdapter:
            display_name = "智慧树 / 知到"
            supports_course_listing = True
            supports_course_execution = True

            def run_homework(self, task, *, auto_submit=None, progress=None, cancel_event=None,
                             confirm_ai_only_submit=None, overwrite_existing_with_verified=None):
                return {
                    "confirmed": confirm_ai_only_submit(["question"]),
                    "overwrite": overwrite_existing_with_verified,
                }

        response = desktop_bridge.handle(
            {"operation": "run_homework", "platform": "zhihuishu", "homework": {"examId": "exam"},
             "overwriteVerifiedAnswers": False},
            {"zhihuishu": ConfirmingAdapter()},
            confirm_ai_only_submit=lambda question_ids: tuple(question_ids) == ("question",),
        )

        self.assertTrue(response["ok"])
        self.assertTrue(response["result"]["confirmed"])
        self.assertFalse(response["result"]["overwrite"])

    def test_course_homework_operation_dispatches_without_selecting_one_task(self):
        class BatchHomeworkAdapter:
            display_name = "智慧树 / 知到"
            supports_course_listing = True
            supports_course_execution = True
            calls = []

            def run_homeworks(self, course, *, auto_submit=None, progress=None, cancel_event=None):
                self.calls.append((course.course_id, course.name, course.metadata, auto_submit, cancel_event))
                if progress:
                    progress(ProgressEvent("开始整门课", current=0, total=2))
                return {"courseName": course.name, "total": 2, "completed": 2, "failed": 0,
                        "saved": 4, "skipped": 1, "pending": 0, "submitted": 0,
                        "cancelled": False, "items": []}

        adapter = BatchHomeworkAdapter()
        streamed = []
        response = desktop_bridge.handle(
            {
                "operation": "run_homeworks",
                "platform": "zhihuishu",
                "course": {
                    "courseId": "zhidao:course-1",
                    "name": "整门课",
                    "metadata": {"kind": "zhidao", "native_id": "course-1"},
                },
                "autoSubmit": False,
            },
            {"zhihuishu": adapter},
            on_progress=streamed.append,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["total"], 2)
        self.assertEqual(adapter.calls[0][:4], (
            "zhidao:course-1", "整门课", {"kind": "zhidao", "native_id": "course-1"}, False,
        ))
        self.assertEqual(streamed[0]["message"], "开始整门课")

    def test_answer_image_and_bank_operations_pass_task_and_stream_progress(self):
        class Adapter:
            def fetch_homework_answers(self, homework, *, progress=None, cancel_event=None):
                self.received = (homework, cancel_event)
                progress(ProgressEvent("读取答案图", current=1, total=2))
                return {"items": [{"question": "题目", "letters": "A", "imageBase64": "", "error": ""}]}

            def harvest_homework_answers(self, homework, *, progress=None, cancel_event=None):
                self.received = (homework, cancel_event)
                progress(ProgressEvent("保存题库"))
                return {"saved": 1, "skipped": 0, "bankPath": "bank.json", "items": []}

        adapter = Adapter()
        for operation in ("fetch_homework_answers", "harvest_homework_answers"):
            with self.subTest(operation=operation):
                streamed = []
                response = desktop_bridge.handle(
                    {"operation": operation, "platform": "zhihuishu", "homework": {"examId": "exam"}},
                    {"zhihuishu": adapter}, on_progress=streamed.append,
                )
                self.assertTrue(response["ok"])
                self.assertEqual(adapter.received[0]["examId"], "exam")
                self.assertEqual(streamed, response["events"])
                self.assertEqual(len(streamed), 1)

                missing = desktop_bridge.handle(
                    {"operation": operation, "platform": "zhihuishu"}, {"zhihuishu": adapter},
                )
                self.assertFalse(missing["ok"])

    def test_list_homeworks_requires_a_course(self):
        class HomeworkAdapter:
            display_name = "智慧树 / 知到"
            supports_course_listing = True
            supports_course_execution = True

            def list_homeworks(self, course, *, progress=None):  # pragma: no cover - 不应被调用
                raise AssertionError("缺少课程时不应调用 adapter")

        response = desktop_bridge.handle(
            {"operation": "list_homeworks", "platform": "zhihuishu"},
            {"zhihuishu": HomeworkAdapter()},
        )

        self.assertFalse(response["ok"])
        self.assertIn("课程", response["error"])


if __name__ == "__main__":
    unittest.main()
