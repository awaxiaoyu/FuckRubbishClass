# -*- coding: utf-8 -*-
"""「桌面端显示已提交、实际没提交」回归测试 —— 全离线 stub。

实测证据（work/probe_submit_response.py，真实账号，对已交卷的第四章）：
    POST /studentExam/gateway/t/v1/answer/submit
    → 200 {"status":"200","msg":"请求成功","rt":{"msg":"不能重复提交","statu":"-1"}}
    旧 submit() 直接 return，run() 随即 submitted=True → UI 显示「交卷 已提交」。
"""
from __future__ import annotations

import json
import unittest

from app.zhihuishu_exam import (
    EP_PAPER,
    EP_READBACK,
    EP_SUBMIT,
    HomeworkRunner,
    HomeworkTarget,
    Question,
    ZhihuishuExamClient,
    ZhihuishuExamError,
    ensure_submit_ok,
)

TARGET = HomeworkTarget.from_mapping(
    {
        "recruitId": "473252",
        "examId": "56rOmYQ5",
        "studentExamId": "exQvazmE",
        "schoolId": "1000006196",
        "courseId": "2351",
    }
)
Q = Question(eid="eidA", stem="题干", type_id=1, score="2", options=[])


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode()
        self.headers = {"Content-Type": "application/json"}
        self.url = ""


class _StubClient(ZhihuishuExamClient):
    """paper/readback/submit 全部离线；submit 行为由测试指定。"""

    def __init__(self, submit_result):
        super().__init__(cookies={"jt-cas": "x", "o_session_id": "y"}, session=_NoSession())
        self._submit_result = submit_result
        self.submit_calls = []

    def paper(self, target):
        return {
            "rt": {"examBase": {"name": "第四章单元测试", "totalScore": 10}},
            "questions": [Q],
            "examBase": {"name": "第四章单元测试", "totalScore": 10},
            "deadline": "",
            "state": 1,
        }

    def readback(self, target, questions=None):
        return {"eidA": {"answer": "1", "datas": "", "questionId": "1"}}

    def save_answer(self, target, question, answer, *, question_type=None):
        self.saved_answers = getattr(self, "saved_answers", [])
        self.saved_answers.append((question.eid, answer))
        return {"status": "200", "rt": {"statu": "1"}}

    def submit(self, target, achieve_count=0, *, confirm=False):
        assert confirm is True
        self.submit_calls.append(achieve_count)
        if isinstance(self._submit_result, Exception):
            raise self._submit_result
        return self._submit_result


class _NoSession:
    headers = {}

    def post(self, *a, **k):  # pragma: no cover - 不应被调用
        raise AssertionError("离线 stub 不应发请求")


class EnsureSubmitOkTest(unittest.TestCase):
    def test_studentexam_success(self):
        rt = ensure_submit_ok(
            {"status": "200", "msg": "请求成功", "rt": {"msg": "提交成功", "score": "10", "statu": "0"}}
        )
        self.assertEqual(rt["score"], "10")

    def test_duplicate_submit_is_rejected(self):
        # 线上真实响应：HTTP 200 / status 200，但业务失败
        with self.assertRaises(ZhihuishuExamError) as ctx:
            ensure_submit_ok(
                {"status": "200", "msg": "请求成功", "rt": {"msg": "不能重复提交", "statu": "-1"}}
            )
        self.assertIn("不能重复提交", str(ctx.exception))
        self.assertIn("没有生效", str(ctx.exception))

    def test_statu_1_accepted(self):
        self.assertEqual(ensure_submit_ok({"rt": {"statu": "1"}})["statu"], "1")

    def test_taurus_style_rt_zero(self):
        # taurus 微服务：rt 不是对象，结果在外层 msg
        self.assertEqual(ensure_submit_ok({"status": "200", "msg": "交卷成功", "rt": 0})["msg"], "交卷成功")

    def test_missing_rt_is_rejected(self):
        with self.assertRaises(ZhihuishuExamError):
            ensure_submit_ok({"status": "200", "msg": "请求成功"})

    def test_negative_statu_without_msg_rejected(self):
        with self.assertRaises(ZhihuishuExamError):
            ensure_submit_ok({"rt": {"statu": "-2"}})


class RunnerSubmitHonestyTest(unittest.TestCase):
    def test_failed_submit_is_not_marked_submitted(self):
        client = _StubClient(ZhihuishuExamError("交卷被服务端拒绝（statu=-1，msg=不能重复提交）"))
        events = []
        runner = HomeworkRunner(
            client, verified_answerer=lambda q: ["A"], progress=events.append, only_missing=False
        )
        result = runner.run(TARGET, submit=True)
        self.assertFalse(result.submitted, "交卷失败时绝不能标记 submitted=True")
        self.assertIn("__submit__", result.failed)
        payload = result.as_dict()
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["submitMsg"], "")
        self.assertTrue(any("交卷失败" in getattr(e, "message", str(e)) for e in events))

    def test_successful_submit_records_score(self):
        client = _StubClient({"status": "200", "rt": {"msg": "提交成功", "score": "10", "statu": "0"}})
        runner = HomeworkRunner(
            client, verified_answerer=lambda q: ["A"], progress=None, only_missing=False
        )
        result = runner.run(TARGET, submit=True)
        self.assertTrue(result.submitted)
        self.assertEqual(result.submit_response.get("score"), "10")
        self.assertEqual(result.as_dict()["submitScore"], "10")
        self.assertEqual(client.submit_calls, [1])


if __name__ == "__main__":
    unittest.main()
