from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import os
import subprocess

from app.adapters import KetangpaiAdapter
from app.models import AdapterUnavailable, Course
from desktop_bridge import handle
from app.settings import SettingsStore


class KetangpaiAdapterTests(unittest.TestCase):
    def test_status_reports_missing_local_sidecar_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = KetangpaiAdapter(Path(folder), SettingsStore(Path(folder) / "config.json"))
            status = adapter.service_status()
            self.assertFalse(status["running"])
            self.assertFalse(status["configured"])
            self.assertFalse(status["dependencies"])

            with self.assertRaisesRegex(AdapterUnavailable, "设置"):
                adapter.start_service()

    def test_platform_is_exposed_by_bridge_without_starting_node(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = KetangpaiAdapter(Path(folder))
            result = handle({"operation": "platforms"}, {"ketangpai": adapter})
        self.assertTrue(result["ok"])
        self.assertEqual(result["platforms"][0]["id"], "ketangpai")
        self.assertFalse(result["platforms"][0]["listing"])

    def test_legacy_config_is_not_overwritten_or_opened(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.ts").write_text("local edits", encoding="utf-8")
            adapter = KetangpaiAdapter(root)
            with patch("app.adapters.ketangpai.os.startfile", create=True) as open_file:
                with self.assertRaisesRegex(AdapterUnavailable, "设置"):
                    adapter.prepare_config()
                open_file.assert_not_called()
            self.assertEqual((root / "config.ts").read_text(encoding="utf-8"), "local edits")

    def test_start_uses_shared_json_profile_without_config_ts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binaries = root / "node_modules" / ".bin"
            binaries.mkdir(parents=True)
            (binaries / ("ts-node.cmd" if os.name == "nt" else "ts-node")).touch()
            entry = root / "node_modules" / "ts-node" / "dist" / "bin.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            store = SettingsStore(root / "config.json")
            profile = store.load()["ketangpai"]
            profile["USERS"] = [{"friendlyName": "test", "username": "test-only", "password": "test-only"}]
            profile["CLASSES"] = [{"friendlyName": "test", "classId": "test-only", "dayOfWeek": 1, "startTime": "08:00", "endTime": "09:50"}]
            store.save({"ketangpai": profile})
            adapter = KetangpaiAdapter(root, store)
            process = Mock()
            process.wait.side_effect = subprocess.TimeoutExpired("node", 1)
            process.poll.return_value = None
            with patch.object(adapter, "_runtime_available", return_value=True), patch("app.adapters.ketangpai.subprocess.Popen", return_value=process) as start:
                adapter.start_service()
            self.assertTrue(adapter.is_running)
            self.assertEqual(start.call_args.kwargs["env"]["COURSE_HUB_KTP_CONFIG"], str(store.path.resolve()))
            self.assertEqual(start.call_args.args[0][1:], [str(entry), "index.ts"])
            self.assertFalse((root / "config.ts").exists())
            process.poll.return_value = 0
            adapter.close()

    def test_bridge_calls_config_setup_without_progress_argument(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = KetangpaiAdapter(Path(folder))
            with patch.object(adapter, "prepare_config", return_value={"created": True}) as prepare:
                response = handle({"operation": "prepare_config", "platform": "ketangpai"}, {"ketangpai": adapter})
            prepare.assert_called_once_with()
        self.assertTrue(response["ok"])
        self.assertEqual(response["status"], {"created": True})

    @unittest.skipUnless(os.name == "nt", "Windows bundled runtime")
    def test_bundled_runtime_works_without_system_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = root / "runtime" / "node"
            runtime.mkdir(parents=True)
            (runtime / "node.exe").touch()
            (runtime / "pnpm.cmd").touch()
            adapter = KetangpaiAdapter(root / "integrations" / "ketangpai")
            adapter.app_root = root
            with patch("app.adapters.ketangpai.shutil.which", return_value=None), \
                    patch("app.adapters.ketangpai.subprocess.run", return_value=Mock(stdout="v22.20.0")) as version:
                self.assertTrue(adapter._runtime_available())
                self.assertEqual(version.call_args.args[0][0], str(runtime / "node.exe"))
                self.assertEqual(adapter._runtime_environment()["PATH"].split(os.pathsep)[0], str(runtime))

    def test_runtime_rejects_old_node_and_missing_package_manager(self):
        adapter = KetangpaiAdapter()
        with patch.object(adapter, "_runtime_paths", return_value=("node", "pnpm")), \
                patch("app.adapters.ketangpai.subprocess.run", return_value=Mock(stdout="v16.20.2")):
            self.assertFalse(adapter._runtime_available())
        with patch.object(adapter, "_runtime_paths", return_value=("node", None)):
            self.assertFalse(adapter._runtime_available())

    def test_pnpm_entry_preserves_isolated_dependency_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            entry = root / "node_modules/.pnpm/ts-node@10.9.2/node_modules/ts-node/dist/bin.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            adapter = KetangpaiAdapter(root)
            self.assertEqual(adapter._ts_node_entry(), entry)

    def test_fetch_course_catalog_logs_in_and_normalizes_course_rows(self):
        adapter = KetangpaiAdapter()
        responses = [
            {"status": 1, "data": {"token": "test-token"}},
            {"status": 1, "data": {
                "topcourses": [{"id": "course-1", "coursename": "课程一", "classname": "班级 A"}],
                "semester": [
                    {"semester": "2026-2027", "term": "1"},
                    {"semester": "2025-2026", "term": "2"},
                ],
            }},
            {"status": 1, "data": [
                {"id": "course-1", "coursename": "课程一", "classname": "班级 A"},
                {"id": "course-2", "coursename": "课程二", "classname": "班级 B"},
                {"coursename": "缺少 ID"},
            ]},
            {"status": 1, "data": {"list": [
                {"id": "course-3", "coursename": "课程三", "classname": "班级 C"},
                {"id": "course-4", "coursename": "课程四", "classname": "班级 D"},
            ]}},
        ]
        with patch("app.adapters.ketangpai._post_ketangpai_json", side_effect=responses) as post:
            courses = adapter.fetch_course_catalog(" student@example.com ", "test-password")

        self.assertEqual(post.call_args_list[0].args[0], "UserApi/login")
        self.assertEqual(post.call_args_list[0].args[1]["email"], "student@example.com")
        self.assertEqual(post.call_args_list[0].args[1]["password"], "test-password")
        self.assertEqual(post.call_args_list[1].args[0], "CourseApi/semesterList")
        self.assertEqual(post.call_args_list[1].args[2], "test-token")
        self.assertEqual(post.call_args_list[1].args[1]["isstudy"], "1")
        self.assertEqual(post.call_args_list[2].args[0], "CourseApi/semesterCourseList")
        self.assertEqual(post.call_args_list[2].args[1]["semester"], "2026-2027")
        self.assertEqual(post.call_args_list[3].args[1]["term"], "2")
        self.assertEqual(courses, [
            Course("ketangpai", "course-1", "课程一", {"className": "班级 A", "semester": "2026-2027", "term": "1"}),
            Course("ketangpai", "course-2", "课程二", {"className": "班级 B", "semester": "2026-2027", "term": "1"}),
            Course("ketangpai", "course-3", "课程三", {"className": "班级 C", "semester": "2025-2026", "term": "2"}),
            Course("ketangpai", "course-4", "课程四", {"className": "班级 D", "semester": "2025-2026", "term": "2"}),
        ])

    def test_course_catalog_bridge_returns_only_normalized_course_fields(self):
        adapter = KetangpaiAdapter()
        with patch.object(adapter, "fetch_course_catalog", return_value=[
            Course("ketangpai", "course-1", "课程一", {"className": "班级 A"}),
        ]) as fetch:
            result = handle({
                "operation": "course_catalog", "platform": "ketangpai",
                "username": "test-user", "password": "test-password",
            }, {"ketangpai": adapter})

        fetch.assert_called_once_with("test-user", "test-password")
        self.assertEqual(result["courses"], [{
            "platform": "ketangpai", "courseId": "course-1", "name": "课程一", "metadata": {"className": "班级 A"},
        }])


if __name__ == "__main__":
    unittest.main()
