"""智慧树「在线学堂 · 平时测试 / 作业」：把纯 HTTP 引擎接进适配层。

本模块不重复实现协议，只做三件事：

1. 把 ``fucker`` 的登录态（``session.cookies``）转成 exam 网关可用的会话
   （缺 ``o_session_id`` 时用 ``jt-cas`` + ``CASTGC`` 走 CAS 自举）；
2. 从学习页 ``learning/videolist`` 里捞出每章挂的「平时测试」目标；
3. 用 ``HomeworkRunner`` 一场一场跑：取卷 → AI 逐题作答 → 逐题保存 → 读回校验
   →（可选）交卷。

协议细节与逐字节验证见 ``work/ANALYSIS_zhs_homework_api.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping, Sequence

from app.models import AdapterError, Course, ProgressEvent
from app.zhihuishu_exam import (
    AnswerCache,
    HomeworkRunner,
    HomeworkTarget,
    Question,
    ZhihuishuExamClient,
    ZhihuishuExamError,
    ZhihuishuSessionError,
    bootstrap_exam_session,
    build_cookie_dict,
    exam_page_url,
    merge_homework_targets,
    openai_answerer,
    parse_questions,
    targets_from_homework_list,
    targets_from_videolist,
)

ProgressCallback = Callable[[Any], None]

#: 只有这两类课程挂在 studyservice 的 ``learning/videolist`` 上，才有平时测试。
SUPPORTED_KINDS = ("zhidao", "hike")


def _say(progress: ProgressCallback | None, message: str, level: str = "info",
         current: float | None = None, total: float | None = None) -> None:
    if not progress:
        return
    try:
        progress(ProgressEvent(message, level=level, current=current, total=total))
    except Exception:  # UI 回调不应打断执行
        pass


def answer_cache_path(recruit_id: str) -> Path:
    """答案缓存：``<项目根>/homeworkAnswer/<recruitId>.json``（跨卷按题干复用）。"""
    root = Path(__file__).resolve().parents[1]
    return root / "homeworkAnswer" / f"{recruit_id or 'unknown'}.json"


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
def exam_cookies(
    fucker: Any,
    *,
    fromurl: str = "",
    log: Callable[[str], None] | None = None,
    force_bootstrap: bool = False,
) -> dict[str, str]:
    """合并最新登录 cookie；考试子会话缺失或强制刷新时通过 CAS 换票。"""
    session_cookies = getattr(getattr(fucker, "session", None), "cookies", None)
    memory_cookies = getattr(fucker, "cookies", None)
    jar: dict[str, str] = {}
    for source in (session_cookies, memory_cookies):
        if source:
            jar.update(build_cookie_dict(source, fallback=False))
    if not jar:
        jar = build_cookie_dict()
    if not jar.get("jt-cas"):
        raise AdapterError(
            "智慧树考试会话缺少 jt-cas：请先在桌面端重新登录智慧树，再运行平时测试。"
        )
    if force_bootstrap:
        jar.pop("o_session_id", None)
        jar.pop("JSESSIONID", None)
    elif jar.get("o_session_id"):
        return jar
    try:
        return bootstrap_exam_session(
            jar,
            fromurl=fromurl,
            proxies=getattr(fucker, "proxies", None),
            log=log,
        )
    except ZhihuishuSessionError as exc:
        raise AdapterError(str(exc)) from exc


def exam_client(
    fucker: Any,
    *,
    fromurl: str = "",
    timeout: float = 20.0,
    log: Callable[[str], None] | None = None,
) -> ZhihuishuExamClient:
    jar = exam_cookies(fucker, fromurl=fromurl, log=log)
    return ZhihuishuExamClient(
        jar,
        proxies=getattr(fucker, "proxies", None),
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# 目标发现
# --------------------------------------------------------------------------- #
@dataclass
class HomeworkTask:
    """一份平时测试的可展示描述（给 UI / bridge 用）。"""

    target: HomeworkTarget
    name: str = ""
    chapter: str = ""
    deadline: str = ""
    question_count: int = 0
    total_score: str = ""
    has_answer: bool = False
    questions: list[Question] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.target.stu_exam_id,
            "name": self.name or self.target.name,
            "chapter": self.chapter,
            "deadline": self.deadline,
            "questionCount": self.question_count,
            "totalScore": self.total_score,
            "hasAnswer": self.has_answer,
            "recruitId": self.target.recruit_id,
            "examId": self.target.exam_id,
            "courseId": self.target.course_id,
            "examUrl": exam_page_url(self.target),
            "kind": getattr(self.target, "kind", "chapter") or "chapter",
            "meetCourseType": int(getattr(self.target, "meet_course_type", 0) or 0),
            "state": getattr(self.target, "state", None),
        }


def targets_for_course(fucker: Any, course: Course) -> list[HomeworkTarget]:
    """取该课程挂的章节平时测试 + 见面课测验目标（只读）。"""
    kind = str(course.metadata.get("kind") or "")
    if kind not in SUPPORTED_KINDS:
        return []
    rac_id = str(course.metadata.get("native_id") or course.course_id or "")
    if not rac_id:
        return []
    if kind == "zhidao":
        rac_id = rac_id.split(":", 1)[-1] if rac_id.startswith("zhidao:") else rac_id
    ctx = fucker.getZhidaoContext(rac_id)
    chapters = ctx.chapters
    try:
        data = {k: chapters.get(k) for k in chapters.keys()}  # type: ignore[union-attr]
    except Exception:
        data = chapters if isinstance(chapters, Mapping) else {}
    recruit_id = str(getattr(ctx.course, "recruitId", "") or "")
    course_id = str(
        getattr(chapters, "courseId", None)
        or (data.get("courseId") if isinstance(data, Mapping) else "")
        or ""
    )
    chapter_targets = targets_from_videolist(
        data or {},
        recruit_id=recruit_id,
        course_id=course_id,
    )
    for target in chapter_targets:
        if recruit_id and not target.recruit_id:
            target.recruit_id = recruit_id

    school_id = ""
    if chapter_targets:
        school_id = str(chapter_targets[0].school_id or "")
        if not course_id:
            course_id = str(chapter_targets[0].course_id or "")
        if not recruit_id:
            recruit_id = str(chapter_targets[0].recruit_id or "")
    try:
        qc = fucker.queryCourse(rac_id)
        recruit_id = str(getattr(qc, "recruitId", None) or recruit_id or "")
        school_id = str(getattr(qc, "schoolId", None) or school_id or "")
        course_id = str(getattr(qc, "courseId", None) or course_id or "")
    except Exception:
        pass

    meet_targets: list[HomeworkTarget] = []
    if recruit_id and course_id and school_id:
        try:
            fromurl = exam_page_url(chapter_targets[0]) if chapter_targets else ""
            client = exam_client(fucker, fromurl=fromurl)
            rt = client.homework_list(recruit_id, course_id=course_id, flag="1")
            meet_targets = targets_from_homework_list(
                rt,
                recruit_id=recruit_id,
                course_id=course_id,
                school_id=school_id,
                kind="meet",
            )
        except Exception:
            meet_targets = []

    return merge_homework_targets(chapter_targets, meet_targets)


def list_homeworks(
    fucker: Any,
    course: Course,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
    with_paper: bool = True,
) -> list[HomeworkTask]:
    """列出该课程所有平时测试；``with_paper=True`` 时顺带取卷拿到题量/截止时间。"""
    if cancel_event and cancel_event.is_set():
        return []
    _say(progress, f"正在读取「{course.name}」的章节测试…")
    try:
        targets = targets_for_course(fucker, course)
    except ZhihuishuExamError as exc:
        raise AdapterError(f"读取章节测试失败：{exc}") from exc
    if not targets:
        _say(progress, "该课程没有挂平时测试/见面课测验（或不是共享学分课）。", level="warning")
        return []

    client: ZhihuishuExamClient | None = None
    if with_paper:
        client = exam_client(fucker, fromurl=exam_page_url(targets[0]))

    tasks: list[HomeworkTask] = []
    total = len(targets)
    for index, target in enumerate(targets, start=1):
        if cancel_event and cancel_event.is_set():
            break
        task = HomeworkTask(target=target, name=target.name, chapter=target.name,
                            deadline=target.deadline)
        if client is not None:
            try:
                paper = client.paper(target)
                task.questions = list(paper["questions"])
                task.question_count = len(task.questions)
                base = paper.get("examBase") or {}
                task.name = str(base.get("name") or task.name or target.name)
                task.total_score = str(base.get("totalScore") or "")
                task.deadline = str(paper.get("deadline") or task.deadline)
                answers = client.readback(target, task.questions)
                # readback 恒返回每个题目的占位条目，只有真填了答案才算「已有作答」
                task.has_answer = any(
                    str((value or {}).get("answer") or "").strip()
                    for value in answers.values()
                )
            except ZhihuishuSessionError:
                raise
            except ZhihuishuExamError as exc:
                _say(progress, f"[{index}/{total}] 跳过（{exc}）", level="warning")
                continue
        tasks.append(task)
        _say(
            progress,
            f"[{index}/{total}] {task.name}：{task.question_count} 题"
            + (f"，截止 {task.deadline}" if task.deadline else "")
            + ("（已有作答）" if task.has_answer else ""),
        )
    return tasks


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
@dataclass
class HomeworkRunSummary:
    course: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)
    submitted: int = 0
    saved_questions: int = 0
    skipped_questions: int = 0
    failed_questions: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "course": self.course,
            "tasks": self.tasks,
            "submitted": self.submitted,
            "saved": self.saved_questions,
            "skipped": self.skipped_questions,
            "failed": self.failed_questions,
            "errors": self.errors,
        }


def build_answerer(ai_config: Mapping[str, Any], *, progress: ProgressCallback | None = None,
                   cancel_event: Event | None = None):
    """按 AI 设置构造答题器；未配置 Key 时抛出可读错误。"""
    from app.settings import validate_ai

    try:
        validate_ai(dict(ai_config))
    except ValueError as exc:
        raise AdapterError(f"AI 未配置好，无法自动作答：{exc}") from exc

    def on_retry(message: str) -> None:
        _say(progress, f"AI 重试：{message}", level="warning")

    return openai_answerer(ai_config, on_retry=on_retry)


def run_homework(
    fucker: Any,
    course: Course,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
    config: Mapping[str, Any] | None = None,
    submit: bool = False,
    only_ids: Sequence[str] | None = None,
    answerer: Callable[[Mapping[str, Any]], Sequence[str]] | None = None,
    delay: float = 0.8,
) -> HomeworkRunSummary:
    """跑一门课的全部（或指定的）平时测试。

    ``submit=False``（默认）只作答保存、不交卷；``submit=True`` 才会在全部题目
    都有答案时调用 ``answer/submit``（不可逆，直接判分）。
    """
    config = config or {}
    summary = HomeworkRunSummary(course=course.name)
    tasks = list_homeworks(
        fucker,
        course,
        progress=progress,
        cancel_event=cancel_event,
        with_paper=False,
    )
    if only_ids:
        wanted = {str(x) for x in only_ids}
        tasks = [t for t in tasks if t.target.stu_exam_id in wanted]
    if not tasks:
        _say(progress, "没有可执行的平时测试。", level="warning")
        return summary

    if answerer is None:
        from app.settings import ai_settings

        ai = ai_settings(dict(config)) if config else {}
        answerer = build_answerer(ai, progress=progress, cancel_event=cancel_event)

    client = exam_client(fucker, fromurl=exam_page_url(tasks[0].target))
    recruit_id = tasks[0].target.recruit_id
    cache = AnswerCache(answer_cache_path(recruit_id))

    total = len(tasks)
    for index, task in enumerate(tasks, start=1):
        if cancel_event and cancel_event.is_set():
            _say(progress, "已取消", level="warning")
            break
        _say(progress, f"[{index}/{total}] 处理《{task.name or task.target.name}》",
             current=index, total=total)

        def relay(event: Any) -> None:
            _say(
                progress,
                getattr(event, "message", str(event)),
                level=getattr(event, "level", "info"),
                current=getattr(event, "current", None),
                total=getattr(event, "total", None),
            )

        runner = HomeworkRunner(
            client,
            answerer=answerer,
            answer_cache=cache,
            delay=delay,
            only_missing=True,
            progress=relay,
            cancel_event=cancel_event,
        )
        try:
            result = runner.run(task.target, submit=bool(submit))
        except ZhihuishuSessionError:
            raise
        except ZhihuishuExamError as exc:
            summary.errors.append(f"{task.name or task.target.stu_exam_id}: {exc}")
            _say(progress, f"《{task.name}》执行失败：{exc}", level="error")
            continue

        summary.saved_questions += len(result.saved)
        summary.skipped_questions += len(result.skipped)
        summary.failed_questions += len(result.failed)
        if result.submitted:
            summary.submitted += 1
        summary.tasks.append(
            {
                "id": task.target.stu_exam_id,
                "name": task.name or task.target.name,
                "saved": len(result.saved),
                "skipped": len(result.skipped),
                "failed": len(result.failed),
                "pending": len(result.pending),
                "submitted": result.submitted,
            }
        )

    _say(
        progress,
        f"平时测试完成：作答 {summary.saved_questions} 题，"
        f"沿用已有 {summary.skipped_questions} 题，失败 {summary.failed_questions} 题，"
        f"交卷 {summary.submitted} 份。",
        level="success",
    )
    return summary


def readback_task(fucker: Any, target: HomeworkTarget) -> dict[str, Any]:
    """读回一份卷子的题目与已答内容（只读）。"""
    client = exam_client(fucker, fromurl=exam_page_url(target))
    paper = client.paper(target)
    questions = list(paper["questions"])
    saved = client.readback(target, questions)
    return {
        "name": str((paper.get("examBase") or {}).get("name") or target.name),
        "deadline": paper.get("deadline"),
        "questions": [
            {**question.as_dict(), "saved": (saved.get(question.eid) or {}).get("answer", "")}
            for question in questions
        ],
    }
