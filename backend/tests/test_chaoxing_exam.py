"""Unit tests for Chaoxing auto-exam helpers (no live network)."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from pathlib import Path

from app.chaoxing_exam import (
    ExamAnswerClient,
    ExamTarget,
    exam_list_url,
    map_display_answers_to_data,
    parse_exam_list_html,
)
from app.models import AdapterError
from app.settings import DEFAULT_EXAM_AUTO, validate_exam_auto


SAMPLE_LIST_HTML = """
<input type="hidden" id="courseId" value="222464396">
<input type="hidden" id="classId" value="154517186">
<input type="hidden" id="cpi" value="581487760">
<div tabindex="0" role="option"
  onclick="goTest('222464396',11066473,171644839,'2026-11-08 18:13:54.0',580314274,false,'6be3abd9578dd23b6892128dfa26bd44', '0');">
  <div class="right-content">
    <p class="overHidden2 fl">样例课程期末考试</p>
    <span>进入</span>
  </div>
</div>
"""


class ExamListParserTests(unittest.TestCase):
    def test_parse_go_test_entries(self):
        items = parse_exam_list_html(
            SAMPLE_LIST_HTML,
            course_name="样例课",
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.exam_id, "11066473")
        self.assertEqual(item.course_id, "222464396")
        self.assertEqual(item.class_id, "154517186")
        self.assertEqual(item.cpi, "581487760")
        self.assertEqual(item.answer_id, "171644839")
        self.assertEqual(item.title, "样例课程期末考试")
        self.assertIn("examId=11066473", item.to_dict()["notesUrl"])
        self.assertIn("样例课", item.display_label())

    def test_parse_live_sample_if_present(self):
        path = Path("work/exam_list_sample.html")
        if not path.exists():
            self.skipTest("no live sample")
        items = parse_exam_list_html(path.read_text(encoding="utf-8"), course_name="测试课")
        self.assertGreaterEqual(len(items), 1)
        self.assertTrue(items[0].exam_id.isdigit())

    def test_exam_list_url(self):
        url = exam_list_url("222464396", "154517186", "581487760", status="0")
        self.assertIn("/mooc2/exam/exam-list", url)
        self.assertIn("courseid=222464396", url)
        self.assertIn("clazzid=154517186", url)
        self.assertIn("status=0", url)


class ExamTargetTests(unittest.TestCase):
    def test_from_fields(self):
        target = ExamTarget.from_inputs(
            exam_id="11066473",
            course_id="222464396",
            class_id="154517186",
            cpi="581487760",
        )
        self.assertIn("examId=11066473", target.notes_url)
        self.assertIn("courseId=222464396", target.notes_url)
        self.assertIn("classId=154517186", target.notes_url)
        self.assertIn("cpi=581487760", target.notes_url)

    def test_from_notes_url(self):
        url = (
            "https://mooc1-api.chaoxing.com/exam-ans/exam/test/examcode/examnotes"
            "?examId=11066473&courseId=222464396&classId=154517186&cpi=581487760"
        )
        target = ExamTarget.from_inputs(notes_url=url)
        self.assertEqual(target.exam_id, "11066473")
        self.assertEqual(target.course_id, "222464396")
        self.assertEqual(target.class_id, "154517186")
        self.assertEqual(target.cpi, "581487760")

    def test_rejects_incomplete(self):
        with self.assertRaisesRegex(AdapterError, "有效"):
            ExamTarget.from_inputs(exam_id="1", course_id="2")


class ExamAnswerClientTests(unittest.TestCase):
    def test_parse_choice_json(self):
        answers = ExamAnswerClient._parse('```answer\n["A"]\n```', "single")
        self.assertEqual(answers, ["A"])

    def test_parse_multiple_and_fill(self):
        self.assertEqual(ExamAnswerClient._parse('```answer\n["A","C"]\n```', "multiple"), ["A", "C"])
        self.assertEqual(ExamAnswerClient._parse("```answer\n苹果\n香蕉\n```", "fill"), ["苹果", "香蕉"])

    def test_answer_calls_model(self):
        response = Mock()
        response.choices = [Mock(message=Mock(content='```answer\n["B"]\n```'))]
        client = Mock()
        client.chat.completions.create.return_value = response
        helper = ExamAnswerClient(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model_name="gpt-test",
            client=client,
        )
        answers = helper.answer({
            "type": "0",
            "stem": "1+1=?",
            "options": [{"label": "A", "text": "1"}, {"label": "B", "text": "2"}],
        })
        self.assertEqual(answers, ["B"])
        self.assertTrue(client.chat.completions.create.called)

    def test_retries_retryable_api_errors(self):
        response = Mock()
        response.choices = [Mock(message=Mock(content='```answer\n["A"]\n```'))]
        client = Mock()
        client.chat.completions.create.side_effect = [
            RuntimeError("429 rate limit"),
            RuntimeError("502 bad gateway"),
            response,
        ]
        retries = []
        helper = ExamAnswerClient(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model_name="gpt-test",
            client=client,
            max_retries=3,
            retry_backoff=0.01,
            on_retry=retries.append,
        )
        answers = helper.answer({
            "type": "0",
            "stem": "重试题",
            "options": [{"label": "A", "text": "ok"}],
        })
        self.assertEqual(answers, ["A"])
        self.assertEqual(client.chat.completions.create.call_count, 3)
        self.assertEqual(len(retries), 2)

    def test_auth_errors_do_not_retry(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("401 unauthorized invalid_api_key")
        helper = ExamAnswerClient(
            api_base="https://api.openai.com/v1",
            api_key="sk-bad",
            model_name="gpt-test",
            client=client,
            max_retries=4,
            retry_backoff=0.01,
        )
        with self.assertRaisesRegex(AdapterError, "AI 请求失败"):
            helper.answer({"type": "0", "stem": "auth", "options": []})
        self.assertEqual(client.chat.completions.create.call_count, 1)


class ExamAutoSettingsTests(unittest.TestCase):
    def test_default_valid(self):
        validate_exam_auto(dict(DEFAULT_EXAM_AUTO))

    def test_enabled_requires_key(self):
        value = dict(DEFAULT_EXAM_AUTO)
        value["enabled"] = True
        with self.assertRaisesRegex(ValueError, "API Key"):
            validate_exam_auto(value)


class _FakePage:
    """Minimal Playwright-like page for one-by-one exam flow tests."""

    def __init__(self, script_map):
        self.script_map = script_map
        self.url = "https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew?start=0"
        self.waits = 0

    def evaluate(self, script, arg=None):
        for key, handler in self.script_map.items():
            if key in script:
                return handler(arg) if callable(handler) else handler
        raise AssertionError(f"unexpected script: {script[:80]}")

    def wait_for_timeout(self, _ms):
        self.waits += 1

    def bring_to_front(self):
        return None

    def goto(self, *_args, **_kwargs):
        return None

    def locator(self, *_args, **_kwargs):
        raise AssertionError("locator should not be used in this unit test")


class OneByOneExamFlowTests(unittest.TestCase):
    def test_answers_each_question_then_submits_once(self):
        from app.chaoxing_exam import ChaoxingExamRunner, ExamTarget

        sequence = [
            {"id": "q1", "type": "0", "stem": "第一题", "options": [{"label": "A", "text": "1"}]},
            {"id": "q2", "type": "0", "stem": "第二题", "options": [{"label": "B", "text": "2"}]},
            {"id": "q3", "type": "0", "stem": "第三题", "options": [{"label": "C", "text": "3"}]},
        ]
        state = {"index": 0, "submit_calls": 0, "next_calls": 0, "temp_saves": 0}

        def questions(_arg=None):
            idx = state["index"]
            return [sequence[idx]] if 0 <= idx < len(sequence) else []

        def sheet(_arg=None):
            return [
                {"start": i, "groupId": "0", "questionId": item["id"], "label": str(i + 1), "current": i == 0}
                for i, item in enumerate(sequence)
            ]

        def page_state(_arg=None):
            idx = state["index"]
            return {
                "hasNext": idx < len(sequence) - 1,
                "hasPrev": idx > 0,
                "hasSubmit": True,
                "currentStart": idx,
                "visibleQuestions": 1,
                "oneByOne": True,
                "url": f"https://example.test/exam?start={idx}",
            }

        def apply_answer(arg):
            self.assertEqual(arg["questionId"], sequence[state["index"]]["id"])
            return {"ok": True, "applied": arg["answers"]}

        def click_next(_arg=None):
            state["next_calls"] += 1
            if state["index"] >= len(sequence) - 1:
                return {"ok": False, "reason": "already-last"}
            state["index"] += 1
            return {"ok": True, "via": "getTheNextQuestion"}

        def submit(_arg=None):
            state["submit_calls"] += 1
            # Must only happen after all questions answered.
            self.assertEqual(state["index"], len(sequence) - 1)
            self.assertEqual(state["next_calls"], len(sequence) - 1)
            return {"ok": True, "via": "button"}

        def confirm(_arg=None):
            return {"ok": True, "via": "confirm"}

        def temp_save(_arg=None):
            state["temp_saves"] += 1
            return True

        page = _FakePage({
            "topicNumber_list li": sheet,
            "hasNext": page_state,
            "questionLi, [id^=\"sigleQuestionDiv_\"]": questions,
            "questionId, type, answers": apply_answer,
            "getTheNextQuestion": click_next,
            "answerDirectSubmit": submit,
            "submitConfirmPop": confirm,
            "submitForm(true, false)": temp_save,
        })
        # More precise routing for evaluate keys used by the runner.
        page.script_map = {
            "EXTRACT_SHEET_MARKER": sheet,  # placeholder unused
        }

        # Route by distinctive snippets present in the real JS constants.
        def evaluate(script, arg=None):
            if "topicNumber_list li[onclick]" in script:
                return sheet()
            if "hasNext: Boolean(nextBtn)" in script or "oneByOne: Boolean(nextBtn)" in script:
                return page_state()
            if "return roots.map((root, index)" in script:
                return questions()
            if "question-not-found" in script or "answers${questionId}" in script or "empty-answer" in script:
                return apply_answer(arg)
            if "getTheNextQuestion" in script and "下一题" in script:
                return click_next()
            if "answerDirectSubmit" in script or "submit-api-missing" in script:
                return submit()
            if "submitConfirmPop" in script and "confirm-missing" in script:
                return confirm()
            if "submitForm(true, false)" in script:
                return temp_save()
            raise AssertionError(script[:120])

        page.evaluate = evaluate

        client = Mock()
        helper = ExamAnswerClient(
            api_base="https://api.openai.com/v1",
            api_key="sk-test",
            model_name="gpt-test",
            client=client,
        )
        helper.answer = Mock(side_effect=lambda q: ["A"])  # type: ignore[method-assign]

        runner = ChaoxingExamRunner(page, helper, answer_delay=0, auto_submit=True)
        # Bypass enter/captcha path: call the answering methods directly through run()
        # by stubbing the prelude methods.
        runner._agree_and_enter = lambda *a, **k: None  # type: ignore[method-assign]
        runner._wait_captcha_and_answer_page = lambda *a, **k: None  # type: ignore[method-assign]
        target = ExamTarget.from_inputs(
            exam_id="11066473",
            course_id="222464396",
            class_id="154517186",
            cpi="581487760",
        )
        events = []
        result = runner.run(target, progress=lambda e: events.append(e.message))

        self.assertEqual(result["mode"], "one_by_one")
        self.assertEqual(len(result["answered"]), 3)
        self.assertEqual(state["next_calls"], 2)
        self.assertEqual(state["submit_calls"], 1)
        self.assertTrue(result["submitted"])
        self.assertTrue(any("下一题" not in msg for msg in events))  # smoke
        self.assertTrue(any("全部题目已作答，正在交卷" in msg for msg in events))
        # Ensure we never claimed submit before finishing all answers.
        submit_idx = next(i for i, msg in enumerate(events) if "正在交卷" in msg)
        answer_msgs = [i for i, msg in enumerate(events) if msg.startswith("作答第")]
        self.assertEqual(len(answer_msgs), 3)
        self.assertTrue(all(i < submit_idx for i in answer_msgs))


class MapDisplayAnswersTests(unittest.TestCase):
    def test_random_options_maps_display_letter_to_data(self):
        options = [
            {"label": "A", "text": "包容谦和、求真务实", "data": "D"},
            {"label": "B", "text": "诚实守信、待人真诚", "data": "B"},
            {"label": "C", "text": "心怀赤诚、忠于家国", "data": "C"},
            {"label": "D", "text": "刚毅坚韧、持之以恒", "data": "A"},
        ]
        # AI 按显示字母选 D（文案是刚毅…），真实 data 是 A
        self.assertEqual(
            map_display_answers_to_data(["D"], options, question_type="0"),
            ["A"],
        )
        # 按文案也能命中
        self.assertEqual(
            map_display_answers_to_data(["刚毅坚韧、持之以恒"], options, question_type="0"),
            ["A"],
        )

    def test_multiple_flattens_and_maps(self):
        options = [
            {"label": "A", "text": "生活简朴", "data": "A"},
            {"label": "B", "text": "少花一分钱", "data": "C"},
            {"label": "C", "text": "错误项", "data": "B"},
            {"label": "D", "text": "克己奉公", "data": "D"},
        ]
        self.assertEqual(
            map_display_answers_to_data(["ABD"], options, question_type="1"),
            ["A", "C", "D"],
        )

    def test_fill_passthrough(self):
        self.assertEqual(
            map_display_answers_to_data(["不少于300字的论述"], [], question_type="4"),
            ["不少于300字的论述"],
        )


class ApplyAnswerJsContractTests(unittest.TestCase):
    def test_apply_answer_js_requires_native_addchoice_path(self):
        from app import chaoxing_exam as mod

        js = mod.APPLY_ANSWER_JS
        self.assertIn("answerBg", js)
        self.assertIn("addChoice", js)
        self.assertIn("addMultipleChoice", js)
        self.assertIn("check_answer", js)
        # Playwright click 不一定触发内联 onclick；必须直接调原生函数/手工复刻写 #answer
        self.assertIn("manual-addChoice-replica", js)
        self.assertIn("answer-not-written", js)
        self.assertIn("writeAnswerValue", js)

    def test_extract_questions_js_splits_label_and_data(self):
        from app import chaoxing_exam as mod

        js = mod.EXTRACT_QUESTIONS_JS
        self.assertIn("answer_p", js)
        self.assertIn("data: dataVal || label", js)
        self.assertIn("randomOptions", js)

    def test_click_next_prefers_official_temp_save_nav(self):
        from app import chaoxing_exam as mod

        js = mod.CLICK_NEXT_JS
        self.assertIn("getTheNextQuestion", js)
        self.assertIn("getTheQuestionByStart", js)
        self.assertIn("tempSave", js)


if __name__ == "__main__":
    unittest.main()
