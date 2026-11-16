# -*- coding: utf-8 -*-
"""run_homeworks 汇总必须区分「交卷 / 未交卷」——否则用户看到「完成」就以为已提交。"""
from __future__ import annotations

import unittest
from unittest import mock

from app.adapters.zhihuishu import ZhihuishuAdapter
from app.models import Course


def _course() -> Course:
    return Course(platform="zhihuishu", course_id="2351", name="示范课程")


class RunHomeworksSummaryTest(unittest.TestCase):
    def _run(self, results):
        adapter = ZhihuishuAdapter({"cookie_path": "cookies.json"})
        tasks = [{"name": f"第{i}章", "examId": f"e{i}"} for i in range(len(results))]
        events = []
        with mock.patch.object(adapter, "list_homeworks", return_value=tasks), mock.patch.object(
            adapter, "run_homework", side_effect=results
        ):
            summary = adapter.run_homeworks(_course(), progress=events.append)
        return summary, [getattr(e, "message", str(e)) for e in events]

    def test_unsubmitted_counted_separately(self):
        summary, messages = self._run(
            [
                {"submitted": True, "saved": {"a": "1"}, "skipped": {}, "pending": [], "failed": {}},
                {"submitted": False, "saved": {}, "skipped": {"b": "2"}, "pending": [], "failed": {}},
            ]
        )
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(summary["unsubmitted"], 1)
        joined = "\n".join(messages)
        self.assertIn("交卷 1 份", joined)
        self.assertIn("未交卷 1 份", joined)

    def test_unsubmitted_item_says_so(self):
        _, messages = self._run(
            [{"submitted": False, "saved": {}, "skipped": {}, "pending": [], "failed": {}}]
        )
        joined = "\n".join(messages)
        self.assertIn("已完成作答（未交卷）", joined)

    def test_submitted_item_says_so(self):
        _, messages = self._run(
            [{"submitted": True, "saved": {"a": "1"}, "skipped": {}, "pending": [], "failed": {}}]
        )
        joined = "\n".join(messages)
        self.assertIn("已完成作答（已交卷）", joined)


if __name__ == "__main__":
    unittest.main()
