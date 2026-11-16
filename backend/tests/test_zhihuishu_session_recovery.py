from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.zhihuishu_exam import (
    EP_PAPER,
    EP_SAVE_ANSWER,
    ZhihuishuExamError,
    ZhihuishuExamClient,
    ZhihuishuSessionError,
)
from app.zhihuishu_homework import exam_cookies


class FakeResponse:
    def __init__(self, body, status=200):
        self.status_code = status
        self.text = body
        self.content = body.encode()


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs, self.headers.get("Cookie")))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, self.headers.get("Cookie")))
        return self.responses.pop(0)


class ZhihuishuSessionRecoveryTests(unittest.TestCase):
    def make_client(self, responses, refresh):
        session = FakeSession(responses)
        client = ZhihuishuExamClient(
            {"jt-cas": "cas-old", "o_session_id": "exam-old"},
            session=session,
            refresh_session=refresh,
        )
        return client, session

    def test_read_only_request_refreshes_and_retries_once(self):
        refreshed = {"jt-cas": "cas-new", "o_session_id": "exam-new"}
        refresh = Mock(return_value=refreshed)
        client, session = self.make_client(
            [FakeResponse("<html><body>home</body></html>"), FakeResponse('{"rt":{}}')],
            refresh,
        )

        result = client.post(EP_PAPER, {"examId": "exam"}, label="取卷")

        self.assertEqual(result, {"rt": {}})
        self.assertEqual(len(session.calls), 2)
        refresh.assert_called_once_with()
        self.assertIn("o_session_id=exam-new", session.calls[1][2])

    def test_look_homework_msg2_is_business_error_not_session(self):
        # 实测：见面课作业 lookHomework 恒回 -1/msg=2，而同参数 doHomework 200 有题
        # （work/zhs_look_msg2_matrix.js）。所以这里必须直接报业务错误：不刷新会话、不重发。
        refresh = Mock(return_value={"jt-cas": "cas-new", "o_session_id": "exam-new"})
        client, session = self.make_client([FakeResponse('{"status":"-1","msg":"2"}')], refresh)

        with self.assertRaisesRegex(ZhihuishuExamError, "msg=2") as ctx:
            client.post("/student/lookHomework", {"examId": "exam"}, label="查看作业")

        self.assertNotIsInstance(ctx.exception, ZhihuishuSessionError)
        self.assertIn("重建会话无效", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)
        refresh.assert_not_called()

    def test_save_answer_msg2_does_not_retry_write_endpoint(self):
        refresh = Mock(return_value={"jt-cas": "cas-new", "o_session_id": "exam-new"})
        client, session = self.make_client([FakeResponse('{"status":"-1","msg":"2"}')], refresh)

        with self.assertRaisesRegex(ZhihuishuExamError, "status=-1"):
            client.post(EP_SAVE_ANSWER, {"answer": "1"}, label="保存答案")

        self.assertEqual(len(session.calls), 1)
        refresh.assert_not_called()

    def test_write_request_is_never_replayed_after_session_error(self):
        refresh = Mock(return_value={"jt-cas": "cas-new", "o_session_id": "exam-new"})
        client, session = self.make_client([FakeResponse("<html></html>")], refresh)

        with self.assertRaises(ZhihuishuSessionError):
            client.post(EP_SAVE_ANSWER, {"answer": "1"}, label="保存答案")

        self.assertEqual(len(session.calls), 1)
        refresh.assert_not_called()

    def test_refresh_and_retry_happen_at_most_once(self):
        refresh = Mock(return_value={"jt-cas": "cas-new", "o_session_id": "exam-new"})
        client, session = self.make_client(
            [FakeResponse("<html></html>"), FakeResponse("<html></html>")],
            refresh,
        )

        with self.assertRaises(ZhihuishuSessionError):
            client.post(EP_PAPER, {"examId": "exam"}, label="取卷")

        self.assertEqual(len(session.calls), 2)
        refresh.assert_called_once_with()

    def test_forced_bootstrap_discards_exam_cookies_and_uses_latest_cas(self):
        fake = SimpleNamespace(
            session=SimpleNamespace(cookies={
                "jt-cas": "cas-session", "CASTGC": "tgt-session",
                "o_session_id": "old-exam", "JSESSIONID": "old-js",
            }),
            cookies={
                "jt-cas": "cas-memory", "CASTGC": "tgt-memory",
                "o_session_id": "stale-exam", "JSESSIONID": "stale-js",
            },
            proxies={},
        )

        def bootstrap(cookies, **_kwargs):
            self.assertEqual(cookies["jt-cas"], "cas-memory")
            self.assertEqual(cookies["CASTGC"], "tgt-memory")
            self.assertNotIn("o_session_id", cookies)
            self.assertNotIn("JSESSIONID", cookies)
            return {**cookies, "o_session_id": "fresh-exam", "JSESSIONID": "fresh-js"}

        with patch("app.zhihuishu_homework.bootstrap_exam_session", side_effect=bootstrap):
            result = exam_cookies(fake, force_bootstrap=True)

        self.assertEqual(result["o_session_id"], "fresh-exam")
        self.assertEqual(result["JSESSIONID"], "fresh-js")


if __name__ == "__main__":
    unittest.main()
