"""JSONL bridge used by the C# WPF desktop client.

The bridge keeps platform automation in Python while the desktop shell is
implemented in C#. Each line from stdin is one request and every response is
one JSON object on stdout, which makes the boundary easy to diagnose and to
replace with a native adapter later.
"""

from __future__ import annotations

import json
import io
import sys
from contextlib import redirect_stdout
from base64 import b64encode
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time
from typing import Any
from uuid import uuid4

from app.adapters import ChaoxingAdapter, KetangpaiAdapter, ZhihuishuAdapter
from app.models import Course, ProgressEvent
from app.settings import SettingsStore


def _adapters() -> dict[str, Any]:
    return {"zhihuishu": ZhihuishuAdapter(), "chaoxing": ChaoxingAdapter(), "ketangpai": KetangpaiAdapter()}


def _event(event: ProgressEvent) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "progress", "message": event.message, "level": event.level}
    if event.current is not None:
        result["current"] = event.current
    if event.total is not None:
        result["total"] = event.total
    if event.qr_image is not None:
        result["qrImageBase64"] = b64encode(event.qr_image).decode("ascii")
    return result


def handle(
    request: dict[str, Any],
    adapters: dict[str, Any],
    on_progress: Any = None,
    settings_store: SettingsStore | None = None,
    cancel_event: Event | None = None,
    confirm_ai_only_submit: Any = None,
) -> dict[str, Any]:
    operation = request.get("operation")
    platform = request.get("platform")
    if operation in ("get_settings", "save_settings"):
        store = settings_store or SettingsStore()
        if operation == "save_settings":
            settings = store.save(request.get("settings"))
        else:
            settings = store.load()
        # Apply immediately, retaining the current login session.
        for adapter in adapters.values():
            apply = getattr(adapter, "apply_settings", None)
            if apply:
                apply(settings)
        return {"ok": True, "settings": settings, "configPath": str(store.path)}
    if operation == "platforms":
        return {
            "ok": True,
            "platforms": [
                {
                    "id": key,
                    "name": adapter.display_name,
                    "listing": adapter.supports_course_listing,
                    "execution": adapter.supports_course_execution,
                }
                for key, adapter in adapters.items()
            ],
        }
    if platform not in adapters:
        return {"ok": False, "error": f"未知平台：{platform}"}
    adapter = adapters[platform]
    if operation in ("list_homeworks", "run_homework", "run_homeworks", "fetch_homework_answers", "harvest_homework_answers"):
        # 智慧树「平时测试 / 作业」：独立 operation，不复用学习通的考试门禁。
        callback = getattr(adapter, operation, None)
        if callback is None:
            return {"ok": False, "error": "该平台不支持平时测试作答"}
        messages: list[dict[str, Any]] = []

        def homework_progress(value: ProgressEvent) -> None:
            item = _event(value)
            messages.append(item)
            if on_progress:
                on_progress(item)

        if operation in ("fetch_homework_answers", "harvest_homework_answers"):
            homework = request.get("homework") or {}
            if not isinstance(homework, dict) or not homework:
                return {"ok": False, "error": "请先选择一份平时测试"}
            result = callback(homework, progress=homework_progress, cancel_event=cancel_event)
            return {"ok": True, "result": result, "events": messages}

        if operation == "list_homeworks":
            course_data = request.get("course") or {}
            if not course_data.get("courseId"):
                return {"ok": False, "error": "请先选择课程"}
            course = Course(
                platform,
                str(course_data.get("courseId", "")),
                str(course_data.get("name", "")),
                course_data.get("metadata") or {},
            )
            homeworks = callback(course, progress=homework_progress)
            return {"ok": True, "homeworks": homeworks, "events": messages}
        if operation == "run_homeworks":
            course_data = request.get("course") or {}
            if not course_data.get("courseId"):
                return {"ok": False, "error": "请先选择课程"}
            course = Course(
                platform,
                str(course_data.get("courseId", "")),
                str(course_data.get("name", "")),
                course_data.get("metadata") or {},
            )
            auto_submit = request.get("autoSubmit")
            confirm_kwargs = {}
            if confirm_ai_only_submit is not None:
                confirm_kwargs["confirm_ai_only_submit"] = confirm_ai_only_submit
            if request.get("overwriteVerifiedAnswers") is not None:
                confirm_kwargs["overwrite_existing_with_verified"] = bool(
                    request["overwriteVerifiedAnswers"]
                )
            if request.get("autoRedo") is not None:
                confirm_kwargs["auto_redo"] = bool(request["autoRedo"])
            if request.get("redoWhen") is not None:
                confirm_kwargs["redo_when"] = str(request["redoWhen"])
            if request.get("redoMax") is not None:
                confirm_kwargs["redo_max"] = int(request["redoMax"])
            result = callback(
                course,
                auto_submit=None if auto_submit is None else bool(auto_submit),
                progress=homework_progress,
                cancel_event=cancel_event,
                **confirm_kwargs,
            )
            return {"ok": True, "result": result, "events": messages}
        auto_submit = request.get("autoSubmit")
        confirm_kwargs = {}
        if confirm_ai_only_submit is not None:
            confirm_kwargs["confirm_ai_only_submit"] = confirm_ai_only_submit
        if request.get("overwriteVerifiedAnswers") is not None:
            confirm_kwargs["overwrite_existing_with_verified"] = bool(
                request["overwriteVerifiedAnswers"]
            )
        if request.get("autoRedo") is not None:
            confirm_kwargs["auto_redo"] = bool(request["autoRedo"])
        if request.get("redoWhen") is not None:
            confirm_kwargs["redo_when"] = str(request["redoWhen"])
        if request.get("redoMax") is not None:
            confirm_kwargs["redo_max"] = int(request["redoMax"])
        result = callback(
            request.get("homework") or {},
            auto_submit=None if auto_submit is None else bool(auto_submit),
            progress=homework_progress,
            cancel_event=cancel_event,
            **confirm_kwargs,
        )
        return {"ok": True, "result": result, "events": messages}
    if operation in ("detect_page", "extract_question", "explain_question", "cancel_assist", "run_exam", "list_exams"):
        if platform != "chaoxing":
            return {"ok": False, "error": "考试相关操作当前仅支持学习通"}
        if operation == "detect_page":
            return {"ok": True, "page": adapter.inspect_current_page()}
        if operation == "extract_question":
            return {"ok": True, **adapter.extract_current_questions()}
        if operation == "cancel_assist":
            adapter.cancel_assist()
            return {"ok": True}
        if operation == "list_exams":
            messages: list[dict[str, Any]] = []

            def list_progress(value: ProgressEvent) -> None:
                item = _event(value)
                messages.append(item)
                if on_progress:
                    on_progress(item)

            exams = adapter.list_exams(progress=list_progress, cancel_event=cancel_event)
            return {"ok": True, "exams": exams, "events": messages}
        if operation == "run_exam":
            messages: list[dict[str, Any]] = []

            def exam_progress(value: ProgressEvent) -> None:
                item = _event(value)
                messages.append(item)
                if on_progress:
                    on_progress(item)

            result = adapter.run_exam(
                exam_id=str(request.get("examId", request.get("exam_id", "")) or ""),
                course_id=str(request.get("courseId", request.get("course_id", "")) or ""),
                class_id=str(request.get("classId", request.get("class_id", "")) or ""),
                cpi=str(request.get("cpi", "") or ""),
                notes_url=str(request.get("notesUrl", request.get("notes_url", "")) or ""),
                progress=exam_progress,
                cancel_event=cancel_event,
            )
            return {"ok": True, "result": result, "events": messages}
        result = adapter.explain_current_question(
            request.get("question"),
            mode=str(request.get("mode", "explain")),
            reference_notes=request.get("referenceNotes", request.get("reference_notes", ())) or (),
            user_candidate=str(request.get("userCandidate", request.get("user_candidate", "")) or ""),
            consent=request.get("consent") or {},
        )
        return {"ok": True, **result}
    if operation == "course_catalog":
        if platform != "ketangpai":
            return {"ok": False, "error": "该平台不支持课程目录查询"}
        courses = adapter.fetch_course_catalog(
            str(request.get("username", "")), str(request.get("password", ""))
        )
        return {
            "ok": True,
            "courses": [
                {
                    "platform": course.platform,
                    "courseId": course.course_id,
                    "name": course.name,
                    "metadata": course.metadata,
                }
                for course in courses
            ],
        }
    if operation in ("service_status", "start_service", "stop_service", "prepare_config", "install_dependencies", "open_logs"):
        callback = getattr(adapter, operation, None)
        if callback is None:
            return {"ok": False, "error": "该平台没有本地服务控制能力"}
        events: list[dict[str, Any]] = []
        def progress(value: ProgressEvent) -> None:
            item = _event(value)
            events.append(item)
            if on_progress:
                on_progress(item)
        result = callback(progress) if operation in ("start_service", "stop_service", "install_dependencies") else callback()
        response: dict[str, Any] = {"ok": True, "events": events}
        if result is not None:
            response["status"] = result
        return response
    if operation == "login":
        messages: list[dict[str, Any]] = []

        def progress(value: ProgressEvent) -> None:
            item = _event(value)
            messages.append(item)
            if on_progress:
                on_progress(item)

        result = (adapter.login(progress, cancel_event) if platform == "chaoxing"
                  else adapter.login(progress))
        return {"ok": True, "result": bool(result), "events": messages}
    if operation == "courses":
        courses = adapter.list_courses(cancel_event) if platform == "chaoxing" else adapter.list_courses()
        return {
            "ok": True,
            "courses": [
                {
                    "platform": course.platform,
                    "courseId": course.course_id,
                    "name": course.name,
                    "metadata": course.metadata,
                }
                for course in courses
            ],
        }
    if operation == "run":
        course_data = request.get("course") or {}
        course = Course(
            platform,
            str(course_data.get("courseId", "")),
            str(course_data.get("name", "")),
            course_data.get("metadata") or {},
        )
        messages: list[dict[str, Any]] = []

        def progress(value: ProgressEvent) -> None:
            item = _event(value)
            messages.append(item)
            if on_progress:
                on_progress(item)

        adapter.run_course(course, progress, cancel_event)
        return {"ok": True, "events": messages}
    if operation == "logout":
        callback = getattr(adapter, "logout", None)
        if callback is None:
            return {"ok": False, "error": "该平台不支持清除会话"}
        callback()
        return {"ok": True}
    if operation == "open_platform":
        callback = getattr(adapter, "open_platform", None)
        if callback is None:
            return {"ok": False, "error": "该平台没有独立的浏览器入口"}
        callback()
        return {"ok": True}
    if operation == "open_upstream":
        callback = getattr(adapter, "open_upstream", None)
        if callback is None:
            return {"ok": False, "error": "该平台没有上游集成说明"}
        callback()
        return {"ok": True}
    return {"ok": False, "error": f"未知操作：{operation}"}


def main() -> int:
    adapters = _adapters()
    pending = Queue()
    confirmation_waiters: dict[str, Queue] = {}
    confirmation_lock = Lock()

    def read_requests():
        # Playwright stays on the main thread; this reader only signals cancel.
        current_cancel = Event()
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    value = None
                if isinstance(value, dict) and value.get("operation") == "cancel":
                    current_cancel.set()
                    continue
                if isinstance(value, dict) and value.get("operation") == "confirm_ai_only_submit_response":
                    confirmation_id = str(value.get("confirmationId") or "")
                    with confirmation_lock:
                        waiter = confirmation_waiters.get(confirmation_id)
                    if waiter is not None:
                        try:
                            waiter.put_nowait(bool(value.get("confirmed")))
                        except Full:
                            pass
                    continue
                current_cancel = Event()
                pending.put((line, current_cancel))
        finally:
            current_cancel.set()
            with confirmation_lock:
                waiting = list(confirmation_waiters.values())
            for waiter in waiting:
                try:
                    waiter.put_nowait(False)
                except Full:
                    pass
            pending.put(None)

    Thread(target=read_requests, daemon=True).start()

    protocol_stdout = sys.stdout

    def write(value: dict[str, Any]) -> None:
        protocol_stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
        protocol_stdout.flush()

    def ask_ai_only_submit(question_ids: list[str], cancel_event: Event) -> bool:
        confirmation_id = uuid4().hex
        response = Queue(maxsize=1)
        with confirmation_lock:
            confirmation_waiters[confirmation_id] = response
        try:
            write({
                "stream": "confirmation",
                "event": {
                    "type": "confirmationRequired",
                    "confirmationId": confirmation_id,
                    "aiOnlyCount": len(question_ids),
                },
            })
            deadline = time.monotonic() + 300
            while not cancel_event.is_set() and time.monotonic() < deadline:
                try:
                    return bool(response.get(timeout=0.25))
                except Empty:
                    continue
            return False
        finally:
            with confirmation_lock:
                confirmation_waiters.pop(confirmation_id, None)

    try:
        while (item := pending.get()) is not None:
            line, cancel_event = item
            try:
                request = json.loads(line)
                # Platform implementations still contain CLI-era prints (for
                # example, "QR Scanned"). Keep those out of the JSONL wire.
                with redirect_stdout(io.StringIO()):
                    response = handle(
                        request,
                        adapters,
                        lambda event: write({"stream": "progress", "event": event}),
                        cancel_event=cancel_event,
                        confirm_ai_only_submit=lambda question_ids: ask_ai_only_submit(
                            list(question_ids), cancel_event,
                        ),
                    )
            except SystemExit as exc:
                response = {"ok": False, "error": f"adapter unexpectedly raised SystemExit ({exc.code!r})"}
            except Exception as exc:  # bridge errors must remain JSONL diagnostics
                response = {"ok": False, "error": str(exc)}
            write(response)
    finally:
        for adapter in adapters.values():
            close = getattr(adapter, "close", None)
            if close:
                close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
