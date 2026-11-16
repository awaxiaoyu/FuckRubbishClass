"""Unit tests for Zhidao AI course ID normalization (no network)."""

import unittest

from fucker import Fucker


class NormalizeZhidaoAiCourseTests(unittest.TestCase):
    def test_flat_fields(self):
        item = Fucker.normalizeZhidaoAiCourse({
            "courseId": "1839", "classId": 4239, "courseName": "测试课",
        })
        self.assertEqual(item.courseId, 1839)
        self.assertEqual(item.classId, 4239)
        self.assertEqual(item.courseName, "测试课")

    def test_nested_origen_param(self):
        item = Fucker.normalizeZhidaoAiCourse({
            "mapCourseId": 10001,
            "courseName": "嵌套课",
            "origenParam": {"classId": "55"},
        })
        self.assertEqual(item.courseId, 10001)
        self.assertEqual(item.classId, 55)

    def test_rejects_incomplete(self):
        self.assertIsNone(Fucker.normalizeZhidaoAiCourse({"courseId": 1}))
        self.assertIsNone(Fucker.normalizeZhidaoAiCourse({"classId": 2}))
        self.assertIsNone(Fucker.normalizeZhidaoAiCourse(None))

    def test_coerce_nested_list(self):
        payload = {"result": {"list": [{"courseId": 9, "classId": 8, "name": "A"}]}}
        items = Fucker._coerceAiCourseItems(payload)
        self.assertEqual(len(items), 1)
        normalized = Fucker.normalizeZhidaoAiCourse(items[0])
        self.assertEqual((normalized.courseId, normalized.classId), (9, 8))


if __name__ == "__main__":
    unittest.main()
