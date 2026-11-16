"""Local sidecar boundary for the MIT-licensed ktpwarp-server integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import webbrowser
from threading import Event
from typing import Any

from app.adapters.base import PlatformAdapter, ProgressCallback
from app.models import AdapterError, AdapterUnavailable, Course, ProgressEvent
from app.settings import SettingsStore, validate_ketangpai


def _post_ketangpai_json(path: str, body: dict[str, Any], token: str = "") -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_1 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.56(0x18003835) NetType/WIFI Language/zh_CN"
        ),
        "Origin": "https://w.ketangpai.com",
        "Referer": "https://w.ketangpai.com/",
    }
    if token:
        headers["token"] = token
    request = Request(
        f"https://openapiv5.ketangpai.com/{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AdapterError(f"课堂派接口请求失败（HTTP {exc.code}）") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AdapterError("连接课堂派失败，请检查网络后重试") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("课堂派返回了无法识别的数据") from exc
    if not isinstance(payload, dict):
        raise AdapterError("课堂派返回了无法识别的数据")
    return payload


class KetangpaiAdapter(PlatformAdapter):
    platform_id = "ketangpai"
    display_name = "课堂派"
    supports_course_listing = False
    supports_course_execution = False
    platform_url = "https://www.ketangpai.com"
    upstream_url = "https://github.com/celesWuff/ktpwarp-server"

    def __init__(self, root: Path | None = None, settings_store: SettingsStore | None = None):
        self.root = root or Path(__file__).resolve().parents[2] / "integrations" / "ketangpai"
        self.app_root = Path(__file__).resolve().parents[2]
        self._process: subprocess.Popen[str] | None = None
        self._log_stream = None
        self.settings_store = settings_store or SettingsStore()

    @property
    def config_path(self) -> Path:
        return self.settings_store.path

    @property
    def log_path(self) -> Path:
        return self.settings_store.path.parent / "logs" / "ktpwarp-server.log"

    def _runtime_paths(self) -> tuple[str | None, str | None]:
        if os.name == "nt":
            bundled = self.app_root / "runtime" / "node"
            if (bundled / "node.exe").is_file() and (bundled / "pnpm.cmd").is_file():
                return str(bundled / "node.exe"), str(bundled / "pnpm.cmd")
        return shutil.which("node"), shutil.which("pnpm")

    def _runtime_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        node, pnpm = self._runtime_paths()
        directories = list(dict.fromkeys(str(Path(p).parent) for p in (node, pnpm) if p))
        environment["PATH"] = os.pathsep.join([*directories, environment.get("PATH", "")])
        return environment

    def _runtime_available(self) -> bool:
        node, pnpm = self._runtime_paths()
        if node is None or pnpm is None:
            return False
        try:
            version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=3, check=True).stdout.strip()
            major = int(version.removeprefix("v").split(".", 1)[0])
            return major >= 18
        except (OSError, subprocess.SubprocessError, ValueError):
            return False

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _ts_node_entry(self) -> Path:
        # pnpm's isolated tree must remain the resolution base even when a
        # Windows release copy has materialized junctions as normal directories.
        isolated = self.root / "node_modules" / ".pnpm"
        for entry in sorted(isolated.glob("ts-node@*/node_modules/ts-node/dist/bin.js")):
            if entry.is_file():
                return entry
        return self.root / "node_modules" / "ts-node" / "dist" / "bin.js"

    def _dependencies_available(self) -> bool:
        executable = "ts-node.cmd" if os.name == "nt" else "ts-node"
        return ((self.root / "node_modules" / ".bin" / executable).is_file()
                and self._ts_node_entry().is_file())

    def _require_installation(self) -> None:
        if not self.root.is_dir():
            raise AdapterUnavailable("课堂派集成目录不存在")
        try:
            validate_ketangpai(self.settings_store.load()["ketangpai"], ready=True)
        except ValueError as exc:
            raise AdapterUnavailable(str(exc)) from exc
        if not self._dependencies_available():
            raise AdapterUnavailable("课堂派 Node 依赖未安装：请点击课堂派页面的“安装依赖”")
        if not self._runtime_available():
            raise AdapterUnavailable("课堂派需要 Node.js 18+ 和 pnpm，请先安装运行环境并重新启动应用")

    def prepare_config(self) -> dict[str, Any]:
        raise AdapterUnavailable("课堂派配置已迁移到应用左下角的 设置 → 课堂派")

    def install_dependencies(self, progress: ProgressCallback | None = None) -> None:
        if not (self.root / "package.json").is_file() or not (self.root / "pnpm-lock.yaml").is_file():
            raise AdapterUnavailable("课堂派 package.json 或锁文件不存在")
        if not self._runtime_available():
            raise AdapterUnavailable("课堂派需要 Node.js 18+ 和 pnpm，请先安装运行环境并重新启动应用")
        _, pnpm = self._runtime_paths()
        # Resolve the batch wrapper through the prepared PATH, avoiding nested
        # cmd.exe quoting when the runtime directory contains spaces.
        command = ([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", "pnpm install --frozen-lockfile"]
                   if os.name == "nt" else [pnpm, "install", "--frozen-lockfile"])
        if progress:
            progress(ProgressEvent("正在按 pnpm-lock.yaml 安装课堂派依赖…"))
        try:
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=900, check=False,
                                    env=self._runtime_environment())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterError(f"课堂派依赖安装失败：{exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "pnpm 未提供错误信息").strip().splitlines()[-1]
            raise AdapterError(f"课堂派依赖安装失败：{detail}")
        if progress:
            progress(ProgressEvent("课堂派依赖安装完成", current=1, total=1))

    def open_logs(self) -> None:
        if not self.log_path.is_file():
            raise AdapterUnavailable("还没有课堂派服务日志；先启动服务")
        try:
            os.startfile(str(self.log_path))
        except (AttributeError, OSError) as exc:
            raise AdapterError(f"无法打开课堂派服务日志：{exc}") from exc

    def login(self, progress: ProgressCallback | None = None) -> bool:
        self.open_platform()
        if progress:
            progress(ProgressEvent("课堂派网页登录页已打开；桌面账号请在 设置 → 课堂派 中配置", level="warning"))
        return False

    def open_platform(self) -> None:
        webbrowser.open(self.platform_url)

    def open_upstream(self) -> None:
        webbrowser.open(self.upstream_url)

    def start_service(self, progress: ProgressCallback | None = None) -> None:
        if self.is_running:
            if progress:
                progress(ProgressEvent("课堂派服务已经在运行"))
            return
        self._require_installation()
        node, _ = self._runtime_paths()
        command = [node, str(self._ts_node_entry()), "index.ts"]
        environment = {**self._runtime_environment(), "COURSE_HUB_KTP_CONFIG": str(self.config_path.resolve())}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_stream = self.log_path.open("a", encoding="utf-8")
            self._process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=self._log_stream,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except OSError as exc:
            self._process = None
            if self._log_stream:
                self._log_stream.close()
                self._log_stream = None
            raise AdapterError(f"无法启动课堂派 Node 服务：{exc}") from exc
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        else:
            self._process = None
            self._log_stream.close()
            self._log_stream = None
            raise AdapterError("课堂派服务启动后退出，请点击“查看日志”检查原因")
        if progress:
            progress(ProgressEvent("课堂派 Node 服务已启动"))

    def stop_service(self, progress: ProgressCallback | None = None) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            if self._log_stream:
                self._log_stream.close()
                self._log_stream = None
            if progress:
                progress(ProgressEvent("课堂派服务当前未运行"))
            return
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                               capture_output=True, check=False)
            except OSError:
                pass
        else:
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        if self._log_stream:
            self._log_stream.close()
            self._log_stream = None
        if progress:
            progress(ProgressEvent("课堂派 Node 服务已停止"))

    def service_status(self) -> dict[str, Any]:
        issue = ""
        try:
            validate_ketangpai(self.settings_store.load()["ketangpai"], ready=True)
        except ValueError as exc:
            issue = str(exc)
        return {
            "running": self.is_running,
            "configured": not issue,
            "configurationIssue": issue,
            "dependencies": self._dependencies_available(),
            "runtimeAvailable": self._runtime_available(),
            "configPath": str(self.config_path),
            "logPath": str(self.log_path),
        }

    def fetch_course_catalog(self, username: str, password: str) -> list[Course]:
        username = username.strip()
        if not username or not password:
            raise AdapterUnavailable("请先选择并填写课堂派账号和密码")

        login = _post_ketangpai_json("UserApi/login", {
            "email": username,
            "password": password,
            "remember": "1",
            "source_type": 1,
            "reqtimestamp": int(time.time() * 1000),
        })
        data = login.get("data")
        token = data.get("token") if isinstance(data, dict) else None
        if login.get("status") != 1 or not isinstance(token, str) or not token:
            message = login.get("message")
            raise AdapterError(f"课堂派登录失败：{message or '请检查账号和密码'}")

        response = _post_ketangpai_json("CourseApi/semesterList", {
            "isstudy": "1",
            "search": "",
            "reqtimestamp": int(time.time() * 1000),
        }, token)
        course_data = response.get("data")
        if response.get("status") != 1 or not isinstance(course_data, dict):
            message = response.get("message")
            raise AdapterError(f"读取课堂派课程失败：{message or '学期列表响应格式无效'}")

        topcourse_rows = course_data.get("topcourses")
        topcourse_rows = list(topcourse_rows) if isinstance(topcourse_rows, list) else []
        rows: list[dict[str, Any]] = []
        semesters = course_data.get("semester")
        if isinstance(semesters, list):
            for semester in semesters:
                if not isinstance(semester, dict) or not semester.get("semester"):
                    continue
                semester_name = str(semester["semester"])
                term = str(semester.get("term") or "")
                semester_response = _post_ketangpai_json("CourseApi/semesterCourseList", {
                    "isstudy": "1",
                    "search": "",
                    "semester": semester_name,
                    "term": term,
                    "reqtimestamp": int(time.time() * 1000),
                }, token)
                semester_data = semester_response.get("data")
                if isinstance(semester_data, dict):
                    semester_rows = semester_data.get("list")
                else:
                    semester_rows = semester_data
                if semester_response.get("status") != 1 or not isinstance(semester_rows, list):
                    message = semester_response.get("message")
                    raise AdapterError(f"读取 {semester_name} 学期课程失败：{message or '课程列表响应格式无效'}")
                for row in semester_rows:
                    if isinstance(row, dict):
                        rows.append({
                            **row,
                            "semester": row.get("semester") or semester_name,
                            "term": row.get("term") or term,
                        })
        rows.extend(row for row in topcourse_rows if isinstance(row, dict))

        courses: list[Course] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            course_id = str(row.get("id") or "").strip()
            name = str(row.get("coursename") or "").strip()
            if not course_id or not name or course_id in seen:
                continue
            seen.add(course_id)
            courses.append(Course("ketangpai", course_id, name, {
                "className": str(row.get("classname") or "").strip(),
                "semester": str(row.get("semester") or "").strip(),
                "term": str(row.get("term") or "").strip(),
            }))
        return courses

    def list_courses(self) -> list[Course]:
        raise AdapterUnavailable("课堂派使用签到时间表，不提供课程列表同步")

    def run_course(self, course: Course, progress: ProgressCallback | None = None, cancel_event: Event | None = None) -> None:
        raise AdapterUnavailable("课堂派签到由 Node 服务按设置页中的课程时间表监测")

    def close(self) -> None:
        self.stop_service()
