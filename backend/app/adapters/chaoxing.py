"""Independent managed-browser integration; no upstream userscript is injected."""

from __future__ import annotations

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Event
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from app.adapters.base import PlatformAdapter, ProgressCallback
from app.chaoxing_exam import (
    EXAM_LIST_STATUSES,
    ChaoxingExamRunner,
    ExamAnswerClient,
    ExamListItem,
    ExamTarget,
    exam_list_url,
    parse_exam_list_html,
)
from app.exam_assist import (
    ExamAssistError,
    ExamAssistService,
    ExplanationProtocolError,
    ExplanationRequest,
    OpenAICompatibleExplanationClient,
    PageKind,
    QuestionItem,
)
from app.models import AdapterError, AdapterUnavailable, Course, ProgressEvent
from app.settings import SettingsStore


class CourseHTMLParser(HTMLParser):
    """Read native course cards and folder IDs without executing server HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.courses = []
        self.folders = []
        self._card = None
        self._div_depth = 0
        self._name_tag = None
        self._name_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "li" and (attrs.get("fileid") or "").isdigit():
            self.folders.append(attrs["fileid"])
        if tag == "div":
            if self._card is not None:
                self._div_depth += 1
            elif "course" in classes:
                self._card = {}
                self._div_depth = 1
        if self._card is None:
            return
        if tag == "a" and attrs.get("href"):
            query = parse_qs(urlparse(attrs["href"]).query)
            if query.get("cpi", [""])[0].isdigit():
                self._card.setdefault("cpi", query["cpi"][0])
        if tag == "input":
            for css, key in (("courseId", "native_id"), ("clazzId", "class_id"),
                             ("cpi", "cpi"), ("curPersonId", "cpi")):
                if css in classes and attrs.get("value"):
                    self._card[key] = attrs["value"]
        if "course-name" in classes:
            self._card["name"] = attrs.get("title", "")
            self._name_tag, self._name_parts = tag, []
        if tag == "p" and "color3" in classes:
            self._card["teacher"] = attrs.get("title", "")

    def handle_endtag(self, tag):
        if tag == self._name_tag:
            if self._card is not None and not self._card.get("name"):
                self._card["name"] = "".join(self._name_parts).strip()
            self._name_tag = None
        if tag == "div" and self._card is not None:
            self._div_depth -= 1
            if self._div_depth == 0:
                self.courses.append(self._card)
                self._card = None

    def handle_data(self, data):
        if self._name_tag:
            self._name_parts.append(data)


CATALOG_SELECTOR = '.chapter_item[id^="cur"], #coursetree .posCatalog_select:not(.firstLayer)'
CATALOG_JS = """selector => Array.from(document.querySelectorAll(selector)).map((e, index) => ({
    id: e.id, index,
    title: (e.querySelector('[title]')?.getAttribute('title') || e.innerText || '').trim(),
    done: /已完成/.test(e.querySelector('.bntHoverTips')?.textContent || '')
}))"""
MEDIA_JS = """options => Array.from(document.querySelectorAll('video')).map((v, index) => {
    if (options.pause) v.pause();
    else if (!v.ended) {
        v.muted = options.muted;
        v.playbackRate = options.rate;
        if (v.paused) v.play().catch(() => {});
    }
    return {index, current: v.currentTime, duration: Number.isFinite(v.duration) ? v.duration : 0,
        ended: v.ended, paused: v.paused, error: v.error?.code || 0};
})"""


class ChaoxingAdapter(PlatformAdapter):
    platform_id = "chaoxing"
    display_name = "学习通 / 超星"
    upstream_url = "https://github.com/chaolucky18/xuexitongScript"
    platform_url = "https://passport2.chaoxing.com/login?" + urlencode({
        "newversion": "true", "fid": "-1", "refer": "https://i.chaoxing.com/base",
    })
    course_base = "https://mooc2-ans.chaoxing.com/mooc2-ans"

    def __init__(self, settings_store: SettingsStore | None = None):
        self.settings_store = settings_store or SettingsStore()
        self._playwright = self._browser = self._context = self._page = None
        settings = self.settings_store.load()
        self._options = settings["chaoxing"]
        self._ai_options = settings["ai"]
        self._exam_assist_service = None

    @property
    def has_saved_session(self) -> bool:
        return self._options.get("save_session", True) and self.session_path.exists()

    @property
    def session_path(self) -> Path:
        return self.settings_store.path.parent / ".sessions" / "chaoxing.json"

    def apply_settings(self, settings: dict) -> None:
        options = settings["chaoxing"]
        if options["browser"] != self._options["browser"]:
            self.close()
        self._options = dict(options)
        self._ai_options = dict(settings.get("ai", self._ai_options))
        self._exam_assist_service = None
        if not options["save_session"]:
            self.session_path.unlink(missing_ok=True)

    @staticmethod
    def _emit(progress, message, current=None, total=None, level="info"):
        if progress:
            progress(ProgressEvent(message, current, total, level))

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise AdapterError("学习通任务已停止")

    def _ensure_browser(self):
        self.apply_settings(self.settings_store.load())
        if self._browser is not None and self._browser.is_connected():
            if self._page is None or self._page.is_closed():
                self._page = self._context.new_page()
            return
        self.close()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AdapterUnavailable("学习通需要 Playwright，请用应用的 Python 环境安装 requirements-desktop.txt。") from exc
        try:
            self._playwright = sync_playwright().start()
            channel = self._options["browser"]
            self._browser = self._playwright.chromium.launch(
                channel=None if channel == "chromium" else channel, headless=False,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            state = None
            if self._options["save_session"] and self.session_path.exists():
                try:
                    state = json.loads(self.session_path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise AdapterError("学习通会话文件损坏，请点击“清除会话”后重新登录。") from exc
            self._context = self._browser.new_context(storage_state=state, locale="zh-CN")
            self._context.set_default_timeout(8000)
            self._page = self._context.new_page()
        except AdapterError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise AdapterUnavailable("无法启动学习通浏览器。请在设置中选择已安装的 msedge 或 chrome；"
                                     "chromium 需先执行 python -m playwright install chromium。") from exc

    def _save_session(self):
        if not self._options["save_session"] or self._context is None:
            return
        path = self.session_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            state = self._context.storage_state()
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             prefix=".chaoxing-", suffix=".tmp", delete=False) as out:
                temporary = Path(out.name)
                json.dump(state, out, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def _has_identity(self):
        return any(c["name"] in ("_uid", "UID") and c["value"] for c in self._context.cookies())

    @staticmethod
    def _response_html(response):
        if response.status != 200:
            raise AdapterError(f"学习通课程服务返回 HTTP {response.status}，请检查网络、代理或账号访问权限。")
        if "login" in response.url.lower() or "passport" in response.url.lower():
            raise AdapterError("学习通会话已失效，请重新登录。")
        body = response.text()
        if re.search(r'<(?:title|form)[^>]*>[^<]*(?:用户登录|登录)', body) or 'id="loginBtn"' in body:
            raise AdapterError("学习通会话已失效，请重新登录。")
        return body

    def _course_html(self, folder="0"):
        response = self._context.request.post(
            self.course_base + "/visit/courselistdata",
            form={"courseType": "1", "courseFolderId": folder, "query": "", "superstarClass": "0"},
            headers={"Referer": self.course_base + "/visit/interaction"}, timeout=12000,
        )
        try:
            return self._response_html(response)
        finally:
            response.dispose()

    def _courses_from_html(self, body):
        parser = CourseHTMLParser()
        parser.feed(body)
        courses = []
        for item in parser.courses:
            if not all(item.get(key, "").isdigit() for key in ("native_id", "class_id", "cpi")):
                continue
            params = {"courseid": item["native_id"], "clazzid": item["class_id"], "cpi": item["cpi"], "ut": "s"}
            metadata = {**item, "url": self.course_base + "/mycourse/studentcourse?" + urlencode(params)}
            course_id = ":".join(item[key] for key in ("native_id", "class_id", "cpi"))
            courses.append(Course(self.platform_id, course_id, item.get("name") or "未命名课程", metadata))
        return courses

    def _fetch_courses(self, cancel_event=None):
        self._check_cancel(cancel_event)
        if not self._has_identity():
            raise AdapterError("请先点击“登录”，在学习通窗口中完成认证。")
        response = self._context.request.get(self.course_base + "/visit/interaction", timeout=12000)
        try:
            interaction = self._response_html(response)
        finally:
            response.dispose()
        if not re.search(r'courselistdata|course-list|courseList|courseFolder|暂无课程', interaction):
            raise AdapterError("学习通未返回可识别的课程页，请确认账号已登录并可访问“我学的课”。")
        self._check_cancel(cancel_event)
        body = self._course_html()
        courses = self._courses_from_html(body)
        if body.strip() and not courses and not re.search(r'暂无|没有课程|无课程|未搜索到|course-list|courseList', body):
            raise AdapterError("学习通课程数据格式发生变化，未将未知页面当作空课程列表。")
        folders = CourseHTMLParser()
        folders.feed(interaction)
        for folder in dict.fromkeys(folders.folders):
            self._check_cancel(cancel_event)
            if folder != "0":
                courses.extend(self._courses_from_html(self._course_html(folder)))
        return list({course.course_id: course for course in courses}.values())

    def login(self, progress: ProgressCallback | None = None, cancel_event: Event | None = None) -> bool:
        self._check_cancel(cancel_event)
        self._ensure_browser()
        self._emit(progress, "正在校验学习通会话…")
        if self._has_identity():
            try:
                self._fetch_courses(cancel_event)
                self._save_session()
                self._emit(progress, "学习通保存的会话已通过课程服务验证", 1, 1, "success")
                return True
            except Exception:
                self._check_cancel(cancel_event)
                self._emit(progress, "保存的会话暂不可用，请在学习通窗口中重新登录", level="warning")
        try:
            self._page.goto(self.platform_url, wait_until="domcontentloaded", timeout=30000)
            self._page.bring_to_front()
        except Exception as exc:
            raise AdapterError("无法加载学习通登录页面，请检查网络或系统代理后重试。") from exc
        self._emit(progress, "请在应用管理的学习通窗口中扫码或登录；关闭窗口可取消。成功后将自动同步课程。")
        deadline = time.monotonic() + self._options["login_timeout"]
        issue = "未检测到登录会话"
        while time.monotonic() < deadline:
            self._check_cancel(cancel_event)
            if self._page.is_closed() or not self._browser.is_connected():
                raise AdapterError("学习通登录窗口已关闭，未完成登录。")
            if self._has_identity():
                try:
                    self._fetch_courses(cancel_event)
                    self._save_session()
                    self._emit(progress, "学习通登录成功，会话已回传并通过课程接口验证", 1, 1, "success")
                    return True
                except Exception as exc:
                    issue = str(exc) if isinstance(exc, AdapterError) else "课程服务暂不可访问，请检查浏览器页面与网络"
                    self._emit(progress, issue, level="warning")
            self._page.wait_for_timeout(2000)
        raise AdapterError(f"学习通登录等待超时：{issue}。可调整设置中的登录等待时间后重试。")

    def list_courses(self, cancel_event=None) -> list[Course]:
        self._check_cancel(cancel_event)
        self._ensure_browser()
        try:
            courses = self._fetch_courses(cancel_event)
            self._save_session()
            return courses
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError("读取学习通课程失败，请检查网络或重新登录。") from exc

    def _catalog(self, page):
        for frame in page.frames:
            try:
                chapters = frame.evaluate(CATALOG_JS, CATALOG_SELECTOR)
                if chapters:
                    return frame, chapters
            except Exception:
                continue
        return None, []

    def _pause_media(self):
        if self._context is not None:
            for page in self._context.pages:
                for frame in page.frames:
                    try:
                        frame.evaluate(MEDIA_JS, {"pause": True})
                    except Exception:
                        pass

    def _play_chapter(self, progress, cancel_event):
        last_position, last_notice = -1, 0
        started = last_change = time.monotonic()
        saw_media = False
        while True:
            self._check_cancel(cancel_event)
            if not self._browser.is_connected() or not self._context.pages:
                raise AdapterError("学习通浏览器已关闭，任务已停止。")
            states = []
            for page in self._context.pages:
                for frame in page.frames:
                    try:
                        states.extend(frame.evaluate(MEDIA_JS, {
                            "pause": False, "muted": self._options["muted"], "rate": self._options["playback_rate"],
                        }))
                        if not states and not saw_media:
                            tab = frame.get_by_text(re.compile(r"^(?:\d+\s*)?视频$"), exact=True)
                            if tab.count() == 1 and tab.is_visible():
                                tab.click(timeout=1000)
                    except Exception:
                        continue
            now = time.monotonic()
            if states:
                saw_media = True
                if any(s["error"] for s in states):
                    raise AdapterError("学习通播放器报告媒体加载失败，请在浏览器检查视频或刷新后重试。")
                position = sum(s["current"] for s in states)
                total = sum(s["duration"] for s in states)
                if position > last_position + 0.1:
                    last_position, last_change = position, now
                if total > 0 and now - last_notice >= 1:
                    self._emit(progress, f"视频实际播放 {int(position)} / {int(total)} 秒", position, total)
                    last_notice = now
                if all(s["ended"] for s in states):
                    self._save_session()
                    return True
            elif not saw_media and now - started > 15:
                self._emit(progress, "当前章节未识别到视频，课件或测验请在浏览器手动处理", level="warning")
                return False
            if now - last_change > self._options["stall_timeout"]:
                raise AdapterError("学习通视频长时间无进展，任务已暂停。请在浏览器处理弹题、验证码或加载错误后重试。")
            if now - last_notice >= 5:
                self._emit(progress, "等待播放器加载；若有弹题或验证码，请在学习通窗口中完成")
                last_notice = now
            page = next((p for p in reversed(self._context.pages) if not p.is_closed()), None)
            if page is None:
                raise AdapterError("学习通任务窗口已关闭。")
            page.wait_for_timeout(500)

    def run_course(self, course: Course, progress: ProgressCallback | None = None,
                   cancel_event: Event | None = None) -> None:
        self._check_cancel(cancel_event)
        if course.platform != self.platform_id:
            raise AdapterError("请选择学习通课程。")
        selected = next((c for c in self.list_courses(cancel_event) if c.course_id == course.course_id), None)
        if selected is None:
            raise AdapterError("当前账号没有该课程，请重新同步课程。")
        self._emit(progress, f"正在打开学习通课程：{selected.name}")
        self._page.goto(selected.metadata["url"], wait_until="domcontentloaded", timeout=30000)
        self._page.bring_to_front()
        frame, chapters = None, []
        for _ in range(30):
            self._check_cancel(cancel_event)
            frame, chapters = self._catalog(self._page)
            if chapters:
                break
            self._page.wait_for_timeout(500)
        if not chapters:
            raise AdapterError("未识别到课程章节目录，请在学习通窗口确认课程已开放后重试。")
        completed = manual = 0
        try:
            for index, chapter in enumerate(chapters):
                self._check_cancel(cancel_event)
                self._emit(progress, f"章节 {index + 1}/{len(chapters)}：{chapter['title']}", index, len(chapters))
                if chapter["done"]:
                    continue
                if index:
                    self._page.goto(selected.metadata["url"], wait_until="domcontentloaded", timeout=30000)
                frame, current = None, []
                for _ in range(20):
                    frame, current = self._catalog(self._page)
                    if current:
                        break
                    self._check_cancel(cancel_event)
                    self._page.wait_for_timeout(500)
                if not current or index >= len(current):
                    raise AdapterError("学习通章节目录发生变化，请重新同步后重试。")
                node = frame.locator(CATALOG_SELECTOR).nth(index)
                link = node.locator(".clicktitle, .posCatalog_name, a").first
                (link if link.count() else node).click(timeout=8000)
                self._page.wait_for_timeout(800)
                if self._play_chapter(progress, cancel_event):
                    completed += 1
                else:
                    manual += 1
                self._pause_media()
                for page in list(self._context.pages):
                    if page is not self._page:
                        page.close()
            if manual:
                raise AdapterError(f"已处理 {completed} 个视频章节；另有 {manual} 个章节需手动处理课件或测验，未标记为课程完成。")
            self._emit(progress, f"视频任务处理结束（播放 {completed} 个章节），最终任务点状态以平台为准。", len(chapters), len(chapters), "success")
        finally:
            self._pause_media()

    def _get_exam_assist_service(self) -> ExamAssistService:
        self._ensure_browser()
        if self._page is None:
            raise AdapterError("学习通当前没有可用页面")
        if self._exam_assist_service is not None and self._exam_assist_service.page is self._page:
            return self._exam_assist_service

        options = self._options.get("exam_assist", {})
        client = None
        if options.get("enabled"):
            provider = self._ai_options.get("openai", {}) if isinstance(self._ai_options, dict) else {}
            base_url = options.get("api_base") or provider.get("api_base") or "https://api.openai.com/v1"
            api_key = options.get("api_key") or provider.get("api_key") or ""
            model = options.get("model_name") or provider.get("model_name") or "gpt-4o-mini"
            client = OpenAICompatibleExplanationClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout=float(options.get("timeout", 45)),
                max_chars=int(options.get("max_chars", 12000)),
            )
        self._exam_assist_service = ExamAssistService(self._page, explanation_client=client)
        return self._exam_assist_service

    def inspect_current_page(self) -> dict:
        """Return a read-only classification of the current managed page."""
        try:
            return self._get_exam_assist_service().detect_page().to_dict()
        except ExamAssistError as exc:
            raise AdapterError(f"学习通考试页面识别失败：{exc}") from exc

    def extract_current_questions(self) -> dict:
        """Extract visible question text only; never calls a platform API."""
        try:
            service = self._get_exam_assist_service()
            context = service.detect_page()
            questions = service.extract_questions(context)
            return {"page": context.to_dict(), "questions": [item.to_dict() for item in questions]}
        except ExamAssistError as exc:
            raise AdapterError(f"学习通题面提取失败：{exc}") from exc

    def explain_current_question(
        self,
        question: dict | None = None,
        *,
        mode: str = "explain",
        reference_notes: list[str] | tuple[str, ...] = (),
        user_candidate: str = "",
        consent: dict | None = None,
    ) -> dict:
        """Generate a read-only study explanation; never fills or submits the page."""
        try:
            service = self._get_exam_assist_service()
            if question is None:
                questions = service.extract_questions(service.detect_page())
                if not questions:
                    raise AdapterError("当前页面没有可解释的可见题目")
                item = questions[0]
            else:
                item = QuestionItem.from_dict(question)
            consent = consent or {}
            confirmed = bool(consent.get("currentPageOnly")) and bool(consent.get("noSessionData"))
            result = service.explain(
                item,
                mode=mode,
                reference_notes=reference_notes,
                user_candidate=user_candidate,
                consent=confirmed,
            )
            return {"question": item.to_dict(), "result": result.to_dict()}
        except AdapterError:
            raise
        except (ExamAssistError, ExplanationProtocolError) as exc:
            raise AdapterError(f"学习通题面解析失败：{exc}") from exc

    def cancel_assist(self) -> None:
        self._exam_assist_service = None

    def list_exams(
        self,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> list[dict]:
        """Fetch exams for every synced course and return selectable rows."""
        self._check_cancel(cancel_event)
        self._ensure_browser()
        courses = self.list_courses(cancel_event)
        if not courses:
            raise AdapterError("当前账号没有可同步的课程，无法获取考试列表。")
        collected: dict[tuple[str, str, str], ExamListItem] = {}
        total = len(courses)
        for index, course in enumerate(courses):
            self._check_cancel(cancel_event)
            meta = course.metadata or {}
            course_id = str(meta.get("native_id") or "").strip()
            class_id = str(meta.get("class_id") or "").strip()
            cpi = str(meta.get("cpi") or "").strip()
            if not all(value.isdigit() for value in (course_id, class_id, cpi)):
                continue
            self._emit(progress, f"拉取考试列表：{course.name}（{index + 1}/{total}）", index, total)
            for status in EXAM_LIST_STATUSES:
                self._check_cancel(cancel_event)
                url = exam_list_url(course_id, class_id, cpi, status=status)
                try:
                    response = self._context.request.get(url, timeout=20000)
                except Exception as exc:
                    raise AdapterError(f"读取课程《{course.name}》考试列表失败：{exc}") from exc
                try:
                    if response.status != 200:
                        continue
                    if "passport" in response.url.lower() or "login" in response.url.lower():
                        raise AdapterError("学习通会话已失效，请重新登录后再获取考试列表。")
                    html = response.text()
                finally:
                    response.dispose()
                if re.search(r"用户登录|id=\"loginBtn\"", html):
                    raise AdapterError("学习通会话已失效，请重新登录后再获取考试列表。")
                for item in parse_exam_list_html(
                    html,
                    course_id=course_id,
                    class_id=class_id,
                    cpi=cpi,
                    course_name=course.name,
                ):
                    collected[(item.course_id, item.class_id, item.exam_id)] = item
        rows = [item.to_dict() for item in collected.values()]
        rows.sort(key=lambda row: (row.get("courseName") or "", row.get("endTime") or "", row.get("title") or ""))
        self._emit(progress, f"共获取到 {len(rows)} 场考试", total, total, "success")
        self._save_session()
        return rows

    def run_exam(
        self,
        *,
        exam_id: str = "",
        course_id: str = "",
        class_id: str = "",
        cpi: str = "",
        notes_url: str = "",
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict:
        """Enter one exam, wait for user captcha, AI-fill answers, then submit."""
        self._check_cancel(cancel_event)
        options = self._options.get("exam_auto", {})
        if not options.get("enabled"):
            raise AdapterError("请先在学习通设置中启用“自动考试”，并填写独立的 API Base / Key / 模型。")
        target = ExamTarget.from_inputs(
            exam_id=exam_id,
            course_id=course_id,
            class_id=class_id,
            cpi=cpi,
            notes_url=notes_url,
        )
        self._ensure_browser()
        if not self._has_identity():
            raise AdapterError("请先登录学习通。")
        client = ExamAnswerClient(
            api_base=str(options.get("api_base") or ""),
            api_key=str(options.get("api_key") or ""),
            model_name=str(options.get("model_name") or ""),
            timeout=float(options.get("timeout", 45)),
            max_retries=int(options.get("api_retries", 4)),
            retry_backoff=float(options.get("api_retry_backoff", 1.5)),
        )
        runner = ChaoxingExamRunner(
            self._page,
            client,
            captcha_timeout=float(options.get("captcha_timeout", 300)),
            answer_delay=float(options.get("answer_delay", 0.8)),
            auto_submit=bool(options.get("auto_submit", True)),
        )
        result = runner.run(target, progress=progress, cancel_event=cancel_event)
        self._save_session()
        return result

    def logout(self):
        self.close()
        self.session_path.unlink(missing_ok=True)

    def open_platform(self):
        self._ensure_browser()
        url = self.course_base + "/visit/interaction" if self._has_identity() else self.platform_url
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._page.bring_to_front()

    def open_upstream(self):
        webbrowser.open(self.upstream_url)

    def close(self):
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = self._context = self._page = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
