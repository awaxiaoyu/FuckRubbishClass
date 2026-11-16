import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import desktop_bridge
from app.adapters.zhihuishu import ZhihuishuAdapter
from app.zhihuishu_exam import AnswerCache, HomeworkRunner, HomeworkTarget, Option, Question
from tests.test_zhihuishu_answer_bank import FakeClient, image, question


class HomeworkAnswerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.target = HomeworkTarget("recruit", "exam", "student", "school", "course")
        self.question = Question(
            eid="question", stem="题目", options=[Option("1", "A", "甲"), Option("2", "B", "乙")],
        )

    def test_verified_bank_takes_priority_over_cached_and_ai_answers(self):
        class Client:
            def __init__(self, question):
                self.question = question
                self.saved = []

            def paper(self, target):
                return {"questions": [self.question], "examBase": {"name": "测试"}}

            def readback(self, target, questions):
                return {}

            def save_answer(self, target, question, answer):
                self.saved.append(answer)

        client = Client(self.question)
        cache = AnswerCache()
        from app.zhihuishu_exam import _cache_key
        cache.put(_cache_key(self.target, self.question), {"answer": "1", "labels": ["A"]})
        runner = HomeworkRunner(client, answerer=lambda _: self.fail("不应调用 AI"),
                                verified_answerer=lambda _: ["B"], answer_cache=cache, delay=0)

        result = runner.run(self.target)

        self.assertEqual(client.saved, ["2"])
        self.assertEqual(result.saved, {"question": "2"})

    def test_bank_miss_without_ai_leaves_question_pending(self):
        class Client:
            def paper(self, target):
                return {"questions": [self_question], "examBase": {}}

            def readback(self, target, questions):
                return {}

            def submit(self, *args, **kwargs):
                raise AssertionError("未作答不能交卷")

        self_question = self.question
        runner = HomeworkRunner(Client(), verified_answerer=lambda _: None, delay=0)
        result = runner.run(self.target, submit=True)
        self.assertEqual(result.pending, ["question"])
        self.assertFalse(result.submitted)

    def test_verified_answer_overwrites_different_existing_answer(self):
        class Client:
            def __init__(self):
                self.saved = []

            def paper(self, _target):
                return {"questions": [self_question], "examBase": {}}

            def readback(self, _target, _questions):
                return {"question": {"answer": "1"}}

            def save_answer(self, _target, question, answer):
                self.saved.append((question.eid, answer))

        self_question = self.question
        client = Client()
        runner = HomeworkRunner(client, verified_answerer=lambda _: ["B"], delay=0)

        result = runner.run(self.target)

        self.assertEqual(client.saved, [("question", "2")])
        self.assertEqual(result.saved, {"question": "2"})
        self.assertEqual(result.skipped, {})

    def test_verified_answer_matching_existing_answer_is_skipped(self):
        class Client:
            def paper(self, _target):
                return {"questions": [self_question], "examBase": {}}

            def readback(self, _target, _questions):
                return {"question": {"answer": "2"}}

            def save_answer(self, *_args):
                self.fail("一致答案不应重复保存")

        self_question = self.question
        runner = HomeworkRunner(Client(), verified_answerer=lambda _: ["B"], delay=0)

        result = runner.run(self.target)

        self.assertEqual(result.saved, {})
        self.assertEqual(result.skipped, {"question": "2"})

    def test_verified_overwrite_can_be_disabled(self):
        class Client:
            def paper(self, _target):
                return {"questions": [self_question], "examBase": {}}

            def readback(self, _target, _questions):
                return {"question": {"answer": "1"}}

            def save_answer(self, *_args):
                self.fail("关闭覆盖后不应重写已有答案")

        self_question = self.question
        runner = HomeworkRunner(
            Client(),
            verified_answerer=lambda _: ["B"],
            overwrite_existing_with_verified=False,
            delay=0,
        )

        result = runner.run(self.target)

        self.assertEqual(result.saved, {})
        self.assertEqual(result.skipped, {"question": "1"})

    def test_ai_only_answers_are_counted_and_require_confirmation_before_submit(self):
        class Client:
            def __init__(self):
                self.calls = []
                self.reads = 0

            def paper(self, _target):
                return {"questions": [self_question], "examBase": {}}

            def readback(self, _target, _questions):
                self.reads += 1
                if self.reads == 1:
                    return {}
                return {"question": {"answer": "2"}}

            def save_answer(self, _target, question, answer):
                self.calls.append(("save", question.eid, answer))

            def submit(self, _target, achieve_count=0, *, confirm=False):
                self.calls.append(("submit", achieve_count, confirm))
                return {"rt": {"statu": "0", "score": "2"}}

        self_question = self.question
        client = Client()
        confirmations = []

        def confirm(ai_only):
            confirmations.append(tuple(ai_only))
            client.calls.append(("confirm", tuple(ai_only)))
            return False

        runner = HomeworkRunner(
            client,
            answerer=lambda _: ["B"],
            verified_answerer=lambda _: None,
            confirm_ai_only_submit=confirm,
            delay=0,
        )

        result = runner.run(self.target, submit=True)

        self.assertEqual(result.ai_only, ["question"])
        self.assertEqual(result.as_dict()["aiOnly"], ["question"])
        self.assertEqual(confirmations, [("question",)])
        self.assertEqual(client.calls, [("save", "question", "2"), ("confirm", ("question",))])
        self.assertTrue(result.submit_confirmation_denied)
        self.assertFalse(result.submitted)

    def test_ai_only_confirmation_accepts_submission_after_prompt(self):
        class Client:
            def __init__(self):
                self.calls = []
                self.reads = 0

            def paper(self, _target):
                return {"questions": [self_question], "examBase": {}}

            def readback(self, _target, _questions):
                self.reads += 1
                return {} if self.reads == 1 else {"question": {"answer": "2"}}

            def save_answer(self, _target, _question, answer):
                self.calls.append(("save", answer))

            def submit(self, _target, achieve_count=0, *, confirm=False):
                self.calls.append(("submit", achieve_count, confirm))
                return {"rt": {"statu": "0"}}

        self_question = self.question
        client = Client()

        def confirm(ai_only):
            client.calls.append(("confirm", tuple(ai_only)))
            return True

        runner = HomeworkRunner(
            client,
            answerer=lambda _: ["B"],
            verified_answerer=lambda _: None,
            confirm_ai_only_submit=confirm,
            delay=0,
        )

        result = runner.run(self.target, submit=True)

        self.assertTrue(result.submitted)
        self.assertEqual(client.calls, [
            ("save", "2"),
            ("confirm", ("question",)),
            ("submit", 1, True),
        ])

    def test_live_answer_image_is_used_before_ai(self):
        from app.zhihuishu_exam import parse_questions

        raw = question()

        class Client:
            def __init__(self):
                self.calls = []
                self.saved = []

            def post(self, path, params, *, label):
                self.calls.append(path)
                if path == "/student/lookHomework":
                    return {"rt": {"examBase": {"workExamParts": [{"questionDtos": [raw]}]}}}
                if path == "/answer/getAnswerImgInfo":
                    return {"rt": {"qid": image()}}
                raise AssertionError(f"unexpected request: {path}")

            def paper(self, _target):
                self.calls.append("paper")
                questions = parse_questions({"examBase": {"workExamParts": [
                    {"questionDtos": [raw]},
                ]}})
                return {"questions": questions, "examBase": {}}

            def readback(self, _target, _questions):
                return {}

            def save_answer(self, _target, _question, answer):
                self.calls.append("save")
                self.saved.append(answer)

        client = Client()
        adapter = ZhihuishuAdapter({"save_cookies": False, "zhihuishu": {"homework": {"answer_delay": 0}}})
        target = {"recruitId": "r", "examId": "e", "studentExamId": "s", "schoolId": "h", "courseId": "c"}
        answerer = lambda _question: self.fail("实时答案图命中时不应调用 AI")
        with TemporaryDirectory() as directory, \
                patch.object(adapter, "_exam_client", return_value=client), \
                patch.object(adapter, "_answer_bank_path", return_value=Path(directory) / "bank.json"), \
                patch("app.zhihuishu_exam.openai_answerer", return_value=answerer):
            result = adapter.run_homework(
                target,
                ai={"enabled": True, "api_base": "https://ai.invalid", "api_key": "test", "model_name": "test"},
            )

        self.assertEqual(client.saved, ["1"])
        self.assertEqual(result["saved"], {"eid": "1"})
        self.assertLess(client.calls.index("/answer/getAnswerImgInfo"), client.calls.index("paper"))
        self.assertEqual(client.calls[-1], "save")

    def test_bridge_fetches_images_saves_bank_and_runner_reuses_answers(self):
        from app.zhihuishu_exam import parse_questions

        adapter = ZhihuishuAdapter({"save_cookies": False})
        target = {"recruitId": "r", "examId": "e", "studentExamId": "s", "schoolId": "h", "courseId": "c"}
        client = FakeClient(question(), image())
        with TemporaryDirectory() as directory, \
                patch.object(adapter, "_exam_client", return_value=client), \
                patch.object(adapter, "_answer_bank_path", return_value=Path(directory) / "bank.json"):
            fetched = desktop_bridge.handle(
                {"operation": "fetch_homework_answers", "platform": "zhihuishu", "homework": target},
                {"zhihuishu": adapter},
            )
            saved = desktop_bridge.handle(
                {"operation": "harvest_homework_answers", "platform": "zhihuishu", "homework": target},
                {"zhihuishu": adapter},
            )
            self.assertTrue(fetched["ok"])
            self.assertEqual(fetched["result"]["items"][0]["letters"], "A")
            self.assertNotIn("raw", fetched["result"]["items"][0])
            self.assertEqual(saved["result"]["saved"], 1)

            class RunClient:
                def paper(self, _target):
                    return {"questions": parse_questions({"examBase": {"workExamParts": [
                        {"questionDtos": [question(ids=("new1", "new2"))]},
                    ]}}), "examBase": {}}

                def readback(self, _target, _questions):
                    return {}

                def save_answer(self, _target, _question, answer):
                    self.answer = answer

            run_client = RunClient()
            with patch.object(adapter, "_exam_client", return_value=run_client):
                result = adapter.run_homework(target, ai={"enabled": False})
            self.assertEqual(result["saved"], {"eid": "new1"})
            self.assertEqual(run_client.answer, "new1")
