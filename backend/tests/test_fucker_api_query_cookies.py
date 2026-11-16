"""回归：`_apiQuery` 必须自己把实例 cookie / UA 灌进 session。

背景（真实故障）：桌面端「平时测试」报 `× code: 401 msg: Forbidden`。
日志显示 videolist 请求发出时 `cookies: <RequestsCookieJar[]>` 为空——
`Fucker.__init__` 只把 cookie 存到 `self._cookies`，`zhidaoQuery -> _apiQuery`
这条 studyservice-api 学习页路径既不调 `_sessionReady`，也不像 `polymasQuery`
那样 `self.session.cookies = self.cookies.copy()`，于是新实例第一个请求裸奔 401。
只有先前跑过 `list_courses`（走 zhsQuery）才会"碰巧"正常。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from requests.cookies import cookiejar_from_dict  # noqa: E402

from fucker import Fucker  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return dict(self._payload)


class FakeSession:
    """模拟新建的 requests.Session：cookies 空、headers 只有默认项。"""

    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies if cookies is not None else cookiejar_from_dict({})
        self.headers = dict(headers or {})
        self.proxies = {}
        self.sent = []

    def post(self, url, data=None, proxies=None, timeout=None):
        self.sent.append({
            "url": url,
            "cookies": {c.name: c.value for c in self.cookies},
            "headers": dict(self.headers),
        })
        return FakeResponse({"code": 0, "message": "success", "data": {}})


class FakeEngine:
    """只提供 _apiQuery 需要的属性，走 Fucker 真实代码路径。"""

    _apiQuery = Fucker._apiQuery

    def __init__(self, cookies, session):
        self._cookies = cookiejar_from_dict(dict(cookies))
        self.headers = {"User-Agent": "Mozilla/5.0 (test-ua)"}
        self.proxies = {}
        self.session = session

    @property
    def cookies(self):
        return self._cookies


class ApiQueryCookieTest(unittest.TestCase):
    def test_empty_session_gets_instance_cookies(self):
        session = FakeSession()
        engine = FakeEngine({"jt-cas": "jwt-token", "o_session_id": "abc"}, session)
        engine._apiQuery("https://studyservice-api.zhihuishu.com/learning/videolist", {"a": 1})
        self.assertEqual(len(session.sent), 1)
        self.assertEqual(session.sent[0]["cookies"].get("jt-cas"), "jwt-token")
        self.assertEqual(session.sent[0]["cookies"].get("o_session_id"), "abc")

    def test_empty_session_gets_ua(self):
        session = FakeSession()
        engine = FakeEngine({"jt-cas": "jwt"}, session)
        engine._apiQuery("https://studyservice-api.zhihuishu.com/learning/videolist", {})
        self.assertEqual(session.sent[0]["headers"].get("User-Agent"), "Mozilla/5.0 (test-ua)")

    def test_session_cookies_not_overwritten(self):
        """_sessionReady 已设的 per-course ctx cookie 不能被覆盖。"""
        ctx = cookiejar_from_dict({"jt-cas": "ctx-token"})
        session = FakeSession(cookies=ctx)
        engine = FakeEngine({"jt-cas": "instance-token"}, session)
        engine._apiQuery("https://studyservice-api.zhihuishu.com/learning/videolist", {})
        self.assertEqual(session.sent[0]["cookies"].get("jt-cas"), "ctx-token")

    def test_existing_session_ua_kept(self):
        session = FakeSession(headers={"User-Agent": "already-set"})
        engine = FakeEngine({"jt-cas": "jwt"}, session)
        engine._apiQuery("https://studyservice-api.zhihuishu.com/learning/videolist", {})
        self.assertEqual(session.sent[0]["headers"].get("User-Agent"), "already-set")


if __name__ == "__main__":
    unittest.main()
