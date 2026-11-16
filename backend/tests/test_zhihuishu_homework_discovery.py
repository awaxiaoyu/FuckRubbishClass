"""章节测试列表必须同时识别未提交和已提交的入口；并并入见面课测验。"""

import unittest
from unittest.mock import MagicMock, patch

from app.adapters.zhihuishu import ZhihuishuAdapter
from app.models import Course
from app.zhihuishu_exam import (
    HomeworkTarget,
    merge_homework_targets,
    targets_from_homework_list,
    targets_from_videolist,
)


def exam_url(route, student_id="student", exam_id="exam"):
    suffix = "/0" if route.lower() == "dohomework" else ""
    return (
        "//onlineexamh5new.zhihuishu.com/stuExamWeb.html#/webExamList/"
        f"{route}/recruit/{student_id}/{exam_id}/course/school{suffix}"
    )


def chapter(route, student_id="student", exam_id="exam"):
    return {
        "id": 123,
        "name": "第一章",
        "studentExamDto": {
            "state": 4 if route.lower() == "checkhomework" else 1,
            "examId": None,
            "examUrl": exam_url(route, student_id, exam_id),
            "exam": {"endDate": "2026-12-11 23:59:59"},
        },
    }


class HomeworkDiscoveryTests(unittest.TestCase):
    def test_both_routes_parse_the_same_target_fields(self):
        for route in ("dohomework", "checkHomework"):
            with self.subTest(route=route):
                url = exam_url(route) + ("?state=4" if route == "checkHomework" else "")
                target = HomeworkTarget.from_exam_url(url)
                self.assertEqual(target.five_tuple(), {
                    "recruitId": "recruit", "examId": "exam", "studentExamId": "student",
                    "schoolId": "school", "courseId": "course",
                })
                self.assertEqual(target.exam_url, url)
                self.assertEqual(HomeworkTarget.from_mapping({"examUrl": url}).five_tuple(),
                                 target.five_tuple())

    def test_all_submitted_chapters_are_listed(self):
        targets = targets_from_videolist({"videoChapterDtos": [chapter("checkHomework")]})

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].name, "第一章")
        self.assertEqual(targets[0].chapter_id, 123)
        self.assertEqual(targets[0].deadline, "2026-12-11 23:59:59")
        self.assertEqual(targets[0].exam_url, "https:" + exam_url("checkHomework"))

    def test_mixed_routes_deduplicate_without_losing_unsubmitted_tests(self):
        targets = targets_from_videolist({"videoChapterDtos": [
            chapter("checkHomework", "submitted", "first"),
            chapter("dohomework", "pending", "second"),
            chapter("checkHomework", "submitted", "first"),
            chapter("otherRoute", "unrelated", "third"),
        ]})

        self.assertEqual([target.stu_exam_id for target in targets], ["submitted", "pending"])

    def test_homework_list_items_become_meet_targets(self):
        items = [{
            "id": "meetStu",
            "examId": "meetExam",
            "examName": "我的性格，我做主",
            "courseId": 1000006252,
            "meetCourseType": 1,
            "state": 1,
            "endDate": "2026-11-06 23:59:59",
        }]
        targets = targets_from_homework_list(
            items,
            recruit_id="473151",
            course_id="1000006252",
            school_id="2351",
            kind="meet",
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].kind, "meet")
        self.assertEqual(targets[0].meet_course_type, 1)
        self.assertEqual(targets[0].five_tuple(), {
            "recruitId": "473151",
            "examId": "meetExam",
            "studentExamId": "meetStu",
            "schoolId": "2351",
            "courseId": "1000006252",
        })
        self.assertIn("/dohomework/473151/meetStu/meetExam/1000006252/2351/0", targets[0].exam_url)

    def test_merge_prefers_chapter_then_appends_meet(self):
        chapter_t = HomeworkTarget.from_exam_url("https:" + exam_url("dohomework", "same", "chExam"))
        meet_same = targets_from_homework_list(
            [{"id": "same", "examId": "meetExam", "courseId": "course", "meetCourseType": 1}],
            recruit_id="recruit", school_id="school", course_id="course", kind="meet",
        )
        meet_new = targets_from_homework_list(
            [{"id": "meetOnly", "examId": "meetExam2", "courseId": "course", "meetCourseType": 1,
              "examName": "见面课A"}],
            recruit_id="recruit", school_id="school", course_id="course", kind="meet",
        )
        merged = merge_homework_targets([chapter_t], meet_same, meet_new)
        self.assertEqual([t.stu_exam_id for t in merged], ["same", "meetOnly"])
        self.assertEqual(merged[0].exam_id, "chExam")  # chapter kept
        self.assertEqual(merged[1].kind, "meet")

    def test_adapter_returns_submitted_test_to_desktop_with_complete_target(self):
        adapter = ZhihuishuAdapter({"save_cookies": False})
        course = Course("zhihuishu", "zhidao:secret", "测试课程", {"kind": "zhidao", "native_id": "secret"})
        fake_client = MagicMock()
        fake_client.homework_list.return_value = {
            "studentHomeworkList": [{
                "id": "meetStu",
                "examId": "meetExam",
                "examName": "见面课测验样例",
                "courseId": "course",
                "meetCourseType": 1,
                "state": 1,
                "endDate": "2026-12-01 23:59:59",
            }]
        }
        fake_qc = MagicMock()
        fake_qc.recruitId = "recruit"
        fake_qc.schoolId = "school"
        fake_qc.courseId = "course"
        with patch.object(adapter, "_require_session"), patch.object(
            adapter, "_videolist", return_value={"videoChapterDtos": [chapter("checkHomework")],
                                                 "courseId": "course", "recruitId": "recruit"},
        ), patch.object(adapter, "_exam_client", return_value=fake_client), patch.object(
            type(adapter), "fucker", new_callable=lambda: property(lambda self: MagicMock(queryCourse=MagicMock(return_value=fake_qc)))):
            # fucker property is tricky; patch query via _get_fucker path instead
            pass

        with patch.object(adapter, "_require_session"), patch.object(
            adapter, "_videolist", return_value={"videoChapterDtos": [chapter("checkHomework")],
                                                 "courseId": "course", "recruitId": "recruit"},
        ), patch.object(adapter, "_exam_client", return_value=fake_client):
            adapter._fucker = MagicMock()
            adapter._fucker.queryCourse.return_value = fake_qc
            with patch.object(type(adapter), "fucker", property(lambda self: self._fucker)):
                results = adapter.list_homeworks(course)

        self.assertEqual(len(results), 2)
        kinds = {r["studentExamId"]: r.get("kind") for r in results}
        self.assertEqual(kinds["student"], "chapter")
        self.assertEqual(kinds["meetStu"], "meet")
        meet = next(r for r in results if r["studentExamId"] == "meetStu")
        self.assertIn("见面课", meet["name"])
        self.assertEqual(HomeworkTarget.from_mapping(meet).five_tuple(), {
            "recruitId": "recruit", "examId": "meetExam", "studentExamId": "meetStu",
            "schoolId": "school", "courseId": "course",
        })
        fake_client.homework_list.assert_called()
        args, kwargs = fake_client.homework_list.call_args
        self.assertEqual(args[0], "recruit")
        self.assertEqual(kwargs.get("flag"), "1")


if __name__ == "__main__":
    unittest.main()
