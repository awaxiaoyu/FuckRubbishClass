"""智慧树答题/交卷的滑块门禁旁路（taurusExam）回归测试 —— 全离线 stub。

背景：studentExam 的 /answer/saveStudentAnswer、/answer/submit 会被风控拦：
      {"msg":"滑块验证未通过，不能答题","status":"-1"}
旁路：同 secretStr body 改发 taurusExam 微服务，服务端同库落盘（200 / statu=1）。
"""
from __future__ import annotations

import json
import unittest

from app.zhihuishu_exam import (
    EP_SAVE_ANSWER,
    EP_SUBMIT,
    TAURUS_GATEWAY,
    HomeworkTarget,
    Question,
    ZhihuishuExamClient,
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
CHOICE = Question(
    eid="HqtZwEMF6Xc8VLATpTBgMw==",
    stem="题干",
    type_id=2,
    type_name="多选题",
    options=[],
    score="2",
)


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode()
        self.headers = {"Content-Type": "application/json"}
        self.url = ""


class _StubSession:
    """studentExam → 滑块拒绝；taurus → 成功。记录每次调用的 URL。"""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def post(self, url, **_kwargs):
        self.calls.append(url)
        if url.startswith(TAURUS_GATEWAY):
            if url.endswith(EP_SUBMIT):
                return _Resp({"currentTime": 1, "msg": "交卷成功", "status": "200", "rt": 0})
            return _Resp({"currentTime": 1, "rt": {"msg": "", "statu": "1"}, "status": "200"})
        return _Resp({"currentTime": 1, "msg": "滑块验证未通过，不能答题", "status": "-1"})


def _client(session):
    return ZhihuishuExamClient(cookies={"jt-cas": "x", "o_session_id": "y"}, session=session)


class SliderBlockClassifyTest(unittest.TestCase):
    def test_slider_blocked_matches_only_slider(self):
        client = _client(_StubSession())
        self.assertTrue(client.slider_blocked({"status": "-1", "msg": "滑块验证未通过，不能答题"}))
        self.assertFalse(client.slider_blocked({"status": "-1", "msg": "试卷已提交,不能继续答题"}))
        self.assertFalse(client.slider_blocked({"status": "200", "rt": {"statu": "1"}}))
        self.assertFalse(client.slider_blocked(None))

    def test_slider_error_from_decode_message(self):
        client = _client(_StubSession())
        self.assertTrue(client.slider_error(RuntimeError("保存答案 业务失败：status=-1，滑块验证未通过，不能答题")))
        self.assertFalse(client.slider_error(RuntimeError("交卷业务失败：status=-1，试卷已提交")))


class TaurusFallbackTest(unittest.TestCase):
    def test_save_answer_falls_back_to_taurus(self):
        session = _StubSession()
        data = _client(session).save_answer(TARGET, CHOICE, "1,2,3,4")
        self.assertEqual(str((data.get("rt") or {}).get("statu")), "1")
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[0].startswith("https://studentexam-api.zhihuishu.com"))
        self.assertTrue(session.calls[1].startswith(TAURUS_GATEWAY))
        self.assertTrue(session.calls[1].endswith(EP_SAVE_ANSWER))

    def test_submit_falls_back_to_taurus(self):
        session = _StubSession()
        data = _client(session).submit(TARGET, 5, confirm=True)
        self.assertEqual(str(data.get("status")), "200")
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[1].startswith(TAURUS_GATEWAY))
        self.assertTrue(session.calls[1].endswith(EP_SUBMIT))

    def test_submit_requires_confirm(self):
        with self.assertRaises(Exception):
            _client(_StubSession()).submit(TARGET, 5)


if __name__ == "__main__":
    unittest.main()
