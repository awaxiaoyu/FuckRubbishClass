"""Read-only exam-assist contract tests.

These tests intentionally use page snapshots and fake LLM transports.  They do
not access a live platform and they assert that the browser-facing path stays
read-only.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.exam_assist import (
    ConsentRequiredError,
    ExamAssistService,
    ExamPageContext,
    ExplanationProtocolError,
    ExplanationRequest,
    ExtractionError,
    OpenAICompatibleExplanationClient,
    PageKind,
    PageRecognizer,
    QuestionExtractor,
    QuestionItem,
    QuestionOption,
)


EXAM_NOTICE = {
    "url": "https://mooc1-api.chaoxing.com/exam-ans/exam/test/examcode/examnotes?examId=42",
    "title": "考试须知",
    "text": "考试须知 进入考试",
    "html": """
        <input type="hidden" id="examId" value="42">
        <input type="hidden" id="answerId" value="99">
        <input type="hidden" id="examCheck" value="1">
        <a id="startBtn">进入考试</a>
    """,
    "questions": [],
}

QUESTION_SNAPSHOT = {
    "url": "https://mooc1-api.chaoxing.com/exam-ans/exam/test/reVersionTestStartNew",
    "title": "考试",
    "text": "考试剩余 30 分钟",
    "questions": [
        {
            "id": "q-1",
            "type": "1",
            "stem": "下列哪项属于可见题面？",
            "options": [
                {"id": "private-option-id", "label": "A", "text": "第一项", "isCorrect": 1},
                {"id": "option-2", "label": "B", "text": "第二项", "result": "1"},
            ],
            # These fields model the accidental data exposed by some pages.
            "correctAnswer": "A",
            "answer": "private-answer",
        },
    ],
}


class FakePage:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []
        self.mutations = []

    def evaluate(self, script, *args):
        self.calls.append((script, args))
        lowered = script.lower()
        for forbidden in (".click(", ".fill(", ".goto(", "fetch(", "xmlhttprequest", "submit("):
            if forbidden in lowered:
                self.mutations.append(forbidden)
        return self.snapshot


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def response_text(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def question():
    return QuestionItem(
        stable_key="chaoxing:q-1",
        source="chaoxing",
        question_id="q-1",
        question_type="single",
        stem="解释这个概念。",
        options=(
            QuestionOption("option-1", "A", "第一项"),
            QuestionOption("option-2", "B", "第二项"),
        ),
        fingerprint="fingerprint",
    )


class PageRecognizerTests(unittest.TestCase):
    def test_exam_notice_uses_multiple_structural_signals(self):
        context = PageRecognizer().detect(EXAM_NOTICE)
        self.assertEqual(context.platform, "chaoxing")
        self.assertEqual(context.kind, PageKind.EXAM_NOTICE)
        self.assertTrue(context.read_only)
        self.assertGreaterEqual(context.confidence, 0.6)
        self.assertIn("url:exam-notice", context.evidence)
        self.assertIn("#examId", context.evidence)
        self.assertIn("#answerId", context.evidence)
        self.assertIn("#examCheck", context.evidence)
        self.assertEqual(context.url, "https://mooc1-api.chaoxing.com")

    def test_question_page_is_restricted_exam_and_not_course(self):
        context = PageRecognizer().detect(QUESTION_SNAPSHOT)
        self.assertEqual(context.kind, PageKind.EXAM)
        self.assertIn("question-container", context.evidence)
        self.assertIn("exam-language", context.evidence)

    def test_unknown_page_falls_back_without_mutation_capability(self):
        context = PageRecognizer().detect({"url": "https://example.test/", "title": "", "text": ""})
        self.assertEqual(context.kind, PageKind.UNKNOWN)
        self.assertTrue(context.read_only)
        self.assertLessEqual(context.confidence, 0.1)


class QuestionExtractorTests(unittest.TestCase):
    def test_extracts_visible_fields_and_drops_answer_fields(self):
        context = PageRecognizer().detect(QUESTION_SNAPSHOT)
        items = QuestionExtractor().extract(QUESTION_SNAPSHOT, context)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.question_id, "q-1")
        self.assertEqual(item.question_type, "single")
        self.assertEqual([option.label for option in item.options], ["A", "B"])
        self.assertEqual(item.options[0].id, "private-option-id")
        serialized = json.dumps(item.to_dict(), ensure_ascii=False)
        for forbidden in ("answer", "correct", "result", "isCorrect", "submit"):
            self.assertNotIn(forbidden.lower(), serialized.lower())

    def test_extracts_fixture_html_without_using_hidden_correctness(self):
        snapshot = {
            "url": "https://example.test/quiz",
            "title": "练习",
            "text": "练习题",
            "html": """
                <div class="questionLi" data-question-id="html-q" data-question-type="2">
                  <p class="question-title">下列哪些选项可见？</p>
                  <label class="option" data-label="A" data-option-id="a">甲</label>
                  <label class="option" data-label="B" data-option-id="b">乙</label>
                  <input type="hidden" name="correctAnswer" value="A" />
                </div>
            """,
        }
        context = PageRecognizer().detect(snapshot)
        # The generic URL has no exam marker; force a quiz context to exercise
        # the HTML extractor in isolation.
        context = ExamPageContext(
            platform="generic", kind=PageKind.QUIZ, url=context.url,
            confidence=0.8, evidence=("fixture",),
        )
        items = QuestionExtractor().extract(snapshot, context)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].question_id, "html-q")
        self.assertEqual(items[0].question_type, "multiple")
        self.assertEqual([option.text for option in items[0].options], ["甲", "乙"])
        self.assertNotIn("correctAnswer", json.dumps(items[0].to_dict()))

    def test_exam_without_extractable_question_is_reported(self):
        context = PageRecognizer().detect(QUESTION_SNAPSHOT)
        empty = dict(QUESTION_SNAPSHOT, questions=[])
        with self.assertRaises(ExtractionError):
            QuestionExtractor().extract(empty, context)

    def test_stable_key_deduplicates_same_question(self):
        context = PageRecognizer().detect(QUESTION_SNAPSHOT)
        duplicate = dict(QUESTION_SNAPSHOT, questions=QUESTION_SNAPSHOT["questions"] * 2)
        items = QuestionExtractor().extract(duplicate, context)
        self.assertEqual(len(items), 1)


class ReadOnlyBrowserTests(unittest.TestCase):
    def test_page_adapter_uses_only_fixed_read_evaluate(self):
        page = FakePage(QUESTION_SNAPSHOT)
        recognizer = PageRecognizer()
        context = recognizer.detect(page)
        QuestionExtractor().extract(page, context)
        self.assertGreaterEqual(len(page.calls), 2)
        self.assertEqual(page.mutations, [])
        self.assertTrue(all(args == () for _, args in page.calls))

    def test_service_never_exposes_browser_mutation_methods(self):
        page = FakePage(QUESTION_SNAPSHOT)
        service = ExamAssistService(page)
        service.detect_page()
        service.extract_questions()
        self.assertEqual(page.mutations, [])
        self.assertFalse(any(name in dir(service) for name in ("click", "fill", "goto", "submit")))


class ExplanationClientTests(unittest.TestCase):
    def test_requires_explicit_read_only_consent(self):
        fake = FakeClient(response_text("{}"))
        client = OpenAICompatibleExplanationClient("https://example.test", "test-key", "test-model", client=fake)
        with self.assertRaises(ConsentRequiredError):
            client.explain(ExplanationRequest(question()))
        self.assertEqual(fake.chat.completions.calls, [])

    def test_structured_response_omits_option_ids_from_prompt(self):
        payload = json.dumps({
            "explanation": "说明概念和判断步骤。",
            "concepts": ["概念"],
            "steps": ["先定义，再比较。"],
            "option_analysis": [{"label": "A", "analysis": "依据充分。"}],
            "candidate_evaluation": None,
            "confidence": 0.8,
            "ambiguities": [],
            "citations": [],
        }, ensure_ascii=False)
        fake = FakeClient(response_text(payload))
        client = OpenAICompatibleExplanationClient("https://example.test", "test-key", "test-model", client=fake)
        result = client.explain(ExplanationRequest(question(), consent=True))
        self.assertEqual(result.explanation, "说明概念和判断步骤。")
        request_payload = fake.chat.completions.calls[0]
        self.assertFalse(any("option-1" in str(message) for message in request_payload["messages"]))
        self.assertEqual(request_payload["stream"], False)

    def test_streaming_response_reports_deltas(self):
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"explanation":"'))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='流式解释'))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='","concepts":[],"steps":[],"option_analysis":[],"candidate_evaluation":null,"confidence":0.7,"ambiguities":[],"citations":[]}'))]),
        ]
        fake = FakeClient(chunks)
        client = OpenAICompatibleExplanationClient("https://example.test/v1", "test-key", "test-model", client=fake)
        deltas = []
        result = client.explain(ExplanationRequest(question(), consent=True), on_delta=deltas.append)
        self.assertEqual(result.explanation, "流式解释")
        self.assertTrue(deltas)
        self.assertEqual(fake.chat.completions.calls[0]["stream"], True)

    def test_response_with_answer_fields_is_rejected(self):
        payload = json.dumps({"explanation": "x", "answer_ids": ["A"]})
        fake = FakeClient(response_text(payload))
        client = OpenAICompatibleExplanationClient("https://example.test", "test-key", "test-model", client=fake)
        with self.assertRaises(ExplanationProtocolError):
            client.explain(ExplanationRequest(question(), consent=True))

    def test_malformed_response_is_rejected(self):
        fake = FakeClient(response_text("not-json"))
        client = OpenAICompatibleExplanationClient("https://example.test", "test-key", "test-model", client=fake)
        with self.assertRaises(ExplanationProtocolError):
            client.explain(ExplanationRequest(question(), consent=True))

    def test_critique_requires_user_candidate(self):
        with self.assertRaises(ValueError):
            ExplanationRequest(question(), mode="critique", consent=True)


class ServiceTests(unittest.TestCase):
    def test_service_coordinates_detect_extract_and_explain(self):
        class StubClient:
            def explain(self, request, on_delta=None):
                self.request = request
                if on_delta:
                    on_delta("说明")
                return SimpleNamespace(to_dict=lambda: {"explanation": "说明"})

        page = FakePage(QUESTION_SNAPSHOT)
        client = StubClient()
        service = ExamAssistService(page, explanation_client=client)
        context = service.detect_page()
        items = service.extract_questions(context)
        result = service.explain(items[0], consent=True)
        self.assertEqual(context.kind, PageKind.EXAM)
        self.assertEqual(items[0].question_id, "q-1")
        self.assertEqual(result.to_dict()["explanation"], "说明")
        self.assertTrue(client.request.consent)
        self.assertEqual(page.mutations, [])


if __name__ == "__main__":
    unittest.main()
