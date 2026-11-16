"""Adapter around the existing Zhihuishu Fucker implementation."""

from threading import Event
from typing import Any, Callable
import json
import logging
import sys
from functools import partial
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.adapters.base import PlatformAdapter, ProgressCallback
from app.models import AdapterError, Course, ProgressEvent
from app.settings import ai_settings, validate_ai
from app.paths import data_root


class ZhihuishuAdapter(PlatformAdapter):
    platform_id = "zhihuishu"
    display_name = "智慧树 / 知到"

    def __init__(self, config: dict[str, Any] | None = None):
        if config is None:
            config_path = data_root() / "config.json"
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                config = {}
        self.config = config
        self._fucker = None
        self._logged_in = False

    def _cookie_path(self) -> Path:
        path = Path(self.config.get("cookie_path", "cookies.json"))
        if not path.is_absolute():
            path = data_root() / path
        return path

    @property
    def has_saved_session(self) -> bool:
        return self.config.get("save_cookies", True) and self._cookie_path().is_file()

    def _get_fucker(self):
        if self._fucker is None:
            # Keep the CLI's heavy dependencies out of the UI import path.
            from fucker import Fucker

            cookie_path = self._cookie_path()
            cookies = None
            if self.config.get("save_cookies", True) and cookie_path.exists():
                try:
                    cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cookies = None
            self._fucker = Fucker(
                cookies=cookies,
                proxies={k: v for k, v in (self.config.get("proxies") or {}).items() if v},
                speed=self.config.get("speed"),
                end_thre=self.config.get("threshold"),
                limit=self.config.get("limit", 0),
                pushplus_token=self._push_token("pushplus"),
                bark_token=self._push_token("bark"),
                tree_view=False,
                progressbar_view=False,
                image_path=self.config.get("image_path", ""),
            )
        return self._fucker

    def _push_token(self, service: str) -> str:
        settings = self.config.get(service, {})
        if isinstance(settings, dict) and "enable" in settings:
            return settings.get("token", "") if settings["enable"] else ""
        return self.config.get(f"{service}_token", "")

    def apply_settings(self, settings: dict) -> None:
        self.config.update(settings["general"])
        self.config["ai"] = settings["ai"]
        self.config["zhihuishu"] = settings.get("zhihuishu", {})
        if self._fucker is not None:
            from push import pushpluser, barkpusher

            engine = self._fucker
            engine.speed = self.config["speed"]
            engine.end_thre = self.config["threshold"]
            engine.limit = self.config["limit"]
            engine.proxies = {k: v for k, v in self.config["proxies"].items() if v} or urllib.request.getproxies()
            engine.image_path = self.config["image_path"]
            for name, sender in (("pushplus", pushpluser), ("bark", barkpusher)):
                token = self._push_token(name)
                setattr(engine, f"_{name}", partial(sender, token=token) if token else lambda *a, **kw: None)

    @property
    def fucker(self):
        return self._get_fucker()

    def login(self, progress: ProgressCallback | None = None) -> bool:
        def qr_callback(image: bytes) -> None:
            # Store before emitting because Qt may deliver the queued signal
            # while the login worker is still running.
            setattr(self, "last_qr_image", image)
            if progress:
                progress(ProgressEvent("二维码已生成，请使用智慧树 App 扫码"))
                progress(ProgressEvent("二维码已显示", level="qr", current=0, total=0, qr_image=image))

        try:
            self.fucker.login(use_qr=True, qr_callback=qr_callback)
        except Exception as exc:
            raise AdapterError(f"智慧树登录失败：{exc}") from exc
        self._logged_in = True
        self._save_cookies()
        if progress:
            progress(ProgressEvent("智慧树登录成功"))
        return True

    def _save_cookies(self) -> None:
        if not self.config.get("save_cookies", True):
            return
        try:
            from utils import cookie_jar_to_list

            cookie_path = self._cookie_path()
            cookie_path.write_text(
                json.dumps(cookie_jar_to_list(self.fucker.cookies), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError):
            # Persistence is a convenience; login should still succeed when
            # the selected path is not writable.
            return

    def _require_session(self) -> None:
        if not self.fucker.cookies:
            raise AdapterError("智慧树尚未登录，请先点击“登录”")

    @staticmethod
    def _playwright_proxy(proxies: dict) -> dict | None:
        proxy_url = proxies.get("https") or proxies.get("http")
        if not proxy_url:
            return None
        parsed = urlsplit(proxy_url)
        if not parsed.scheme or not parsed.hostname:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        server = f"{parsed.scheme}://{host}"
        if port:
            server += f":{port}"
        result = {"server": server}
        if parsed.username:
            result["username"] = unquote(parsed.username)
        if parsed.password:
            result["password"] = unquote(parsed.password)
        return result

    def _open_captcha_browser(self, recruit_and_course_id: str, progress, cancel_event) -> bool:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AdapterError(
                "智慧树滑块验证需要 Playwright；当前 bridge Python 未安装该依赖："
                f"{sys.executable}。请使用标准 Windows CPython 创建 .venv-win，"
                "并安装 requirements-csharp-desktop.txt。"
            ) from exc

        engine = self.fucker
        source_cookies = engine.session.cookies
        browser_cookies = []
        for cookie in source_cookies:
            domain = cookie.domain or ".zhihuishu.com"
            host = domain.lstrip(".").casefold()
            if host != "zhihuishu.com" and not host.endswith(".zhihuishu.com"):
                continue
            if cookie.expires is not None and cookie.expires <= time.time():
                continue
            item = {
                "name": cookie.name,
                "value": cookie.value,
                "domain": domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "httpOnly": "HttpOnly" in (cookie._rest or {}),
            }
            if cookie.expires is not None:
                # Requests may carry a signed-64-bit "never expires" sentinel;
                # Chromium only accepts cookie dates through year 9999.
                item["expires"] = min(int(cookie.expires), 253402300799)
            same_site = (cookie._rest or {}).get("SameSite")
            same_site = same_site.title() if isinstance(same_site, str) else None
            if same_site in ("Strict", "Lax", "None"):
                item["sameSite"] = same_site
            browser_cookies.append(item)

        options = self.config.get("chaoxing", {})
        channel = options.get("browser", "msedge") if isinstance(options, dict) else "msedge"
        proxy = self._playwright_proxy(getattr(engine, "proxies", {}) or {})
        launch_options = {"headless": False}
        if channel != "chromium":
            launch_options["channel"] = channel
        if proxy:
            launch_options["proxy"] = proxy

        from requests.cookies import RequestsCookieJar, create_cookie

        continue_event = Event()
        playwright = browser = context = page = None
        if progress:
            progress(ProgressEvent("正在打开智慧树 Playwright 验证窗口…", level="warning"))
        try:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(**launch_options)
            context = browser.new_context(locale="zh-CN")
            if browser_cookies:
                context.add_cookies(browser_cookies)
            page = context.new_page()
            page.set_default_timeout(15000)
            page.expose_function("courseHubCaptchaContinue", lambda: continue_event.set())
            course_url = (
                "https://studyh5.zhihuishu.com/videoStudy.html#/studyVideo?recruitAndCourseId="
                f"{recruit_and_course_id}"
            )
            page.goto(course_url, wait_until="domcontentloaded", timeout=45000)
            page.bring_to_front()
            if progress:
                progress(ProgressEvent(
                    "请在智慧树窗口完成滑块验证，然后点击页面右下角“验证完成，继续课程”。",
                    level="warning",
                ))

            deadline = time.monotonic() + 600
            next_button_refresh = 0.0
            while time.monotonic() < deadline and not continue_event.is_set():
                if cancel_event and cancel_event.is_set():
                    return False
                if page.is_closed() or not browser.is_connected():
                    return False
                now = time.monotonic()
                if now >= next_button_refresh:
                    page.evaluate("""() => {
                      let button = document.getElementById('coursehub-captcha-continue');
                      if (!button) {
                        button = document.createElement('button');
                        button.id = 'coursehub-captcha-continue';
                        button.textContent = '验证完成，继续课程';
                        button.style.cssText = 'position:fixed;right:24px;bottom:24px;z-index:2147483647;'
                          + 'padding:12px 18px;border:0;border-radius:6px;background:#1677ff;color:white;'
                          + 'font:600 15px sans-serif;cursor:pointer;box-shadow:0 2px 12px #0005';
                        button.onclick = () => window.courseHubCaptchaContinue();
                        document.documentElement.appendChild(button);
                      }
                    }""")
                    next_button_refresh = now + 1
                page.wait_for_timeout(250)

            if not continue_event.is_set():
                return False

            client = page.evaluate("""() => {
              const data = navigator.userAgentData;
              return {
                userAgent: navigator.userAgent,
                brands: data ? data.brands : [],
                mobile: data ? data.mobile : false,
                platform: data ? data.platform : navigator.platform
              };
            }""")
            if isinstance(client, dict) and client.get("userAgent"):
                engine.headers["User-Agent"] = client["userAgent"]
                if client.get("brands"):
                    engine.headers["sec-ch-ua"] = ", ".join(
                        f"{json.dumps(brand['brand'])};v={json.dumps(brand['version'])}"
                        for brand in client["brands"]
                    )
                    engine.headers["sec-ch-ua-mobile"] = "?1" if client.get("mobile") else "?0"
                    engine.headers["sec-ch-ua-platform"] = json.dumps(client.get("platform", ""))
                engine.session.headers.update(engine.headers)

            updated = RequestsCookieJar()
            for item in context.cookies():
                expires = item.get("expires")
                rest = {"HttpOnly": None} if item.get("httpOnly") else {}
                if item.get("sameSite"):
                    rest["SameSite"] = item["sameSite"]
                cookie = create_cookie(
                    name=item["name"], value=item["value"],
                    domain=item.get("domain", ".zhihuishu.com"),
                    path=item.get("path", "/"), secure=item.get("secure", False),
                    expires=int(expires) if expires and expires > 0 else None,
                    rest=rest,
                )
                updated.set_cookie(cookie)
            if not updated:
                return False

            # Assign the jar directly so the existing cookie setter does not log secret values.
            engine._cookies = updated
            try:
                engine.uuid = json.loads(unquote(updated["CASLOGC"]))["uuid"]
                engine._cookies[f"exitRecod_{engine.uuid}"] = "2"
            except (KeyError, TypeError, ValueError):
                pass
            engine.session.cookies = engine.cookies.copy()
            for cached in getattr(engine, "context", {}).values():
                cached.cookies = engine.cookies.copy()
                cached.headers = engine.headers.copy()
            self._save_cookies()
            if progress:
                progress(ProgressEvent("验证已提交，正在重试智慧树进度上报…"))
            return True
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"无法启动或使用智慧树 Playwright 验证窗口：{exc}") from exc
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass

    @staticmethod
    def _course_name(item: Any, fallback: str) -> str:
        return str(getattr(item, "courseName", None) or getattr(item, "name", None) or fallback)

    @staticmethod
    def _item_get(item: Any, *names: str, default=None):
        getter = item.get if isinstance(item, dict) else lambda key, fallback=None: getattr(item, key, fallback)
        for name in names:
            value = getter(name, None)
            if value is not None and value != "":
                return value
        return default

    def _append_zhidao_ai_courses(self, result: list[Course], errors: list[str]) -> None:
        try:
            items = self.fucker.getZhidaoAiList()
        except Exception as exc:
            errors.append(f"zhidao_ai: {exc}")
            return
        existing = {course.course_id for course in result}
        for item in items or []:
            normalized = item
            if not (getattr(item, "courseId", None) and getattr(item, "classId", None)):
                try:
                    from fucker import Fucker
                    normalized = Fucker.normalizeZhidaoAiCourse(item)
                except Exception:
                    normalized = None
            if not normalized:
                continue
            course_id = str(self._item_get(normalized, "courseId"))
            class_id = str(self._item_get(normalized, "classId"))
            if not course_id.isdigit() or not class_id.isdigit():
                continue
            course_key = f"zhidao_ai:{course_id}:{class_id}"
            if course_key in existing:
                continue
            existing.add(course_key)
            name = self._course_name(normalized, course_id)
            if "（AI）" not in str(name) and not str(name).startswith("知到 AI"):
                name = f"{name}（AI）"
            result.append(Course(
                self.platform_id,
                course_key,
                name,
                {
                    "kind": "zhidao_ai",
                    "native_id": course_id,
                    "class_id": class_id,
                    "mode": "ai",
                },
            ))

    @staticmethod
    def _polymas_looks_like_share_course(item: Any) -> bool:
        """Polymas 总览接口常把共享课再列一遍；这些应归到 zhidao，不单独占一行。"""
        course_type = str(ZhihuishuAdapter._item_get(item, "courseType", default="") or "").lower()
        link = str(ZhihuishuAdapter._item_get(item, "linkUrl", "url", default="") or "").lower()
        origen = ZhihuishuAdapter._item_get(item, "origenParam", default=None)
        secret = ZhihuishuAdapter._item_get(item, "secret", default=None)
        if secret is None and origen is not None:
            secret = ZhihuishuAdapter._item_get(origen, "secret", default=None)
        if origen is not None and not link:
            link = str(ZhihuishuAdapter._item_get(origen, "url", default="") or "").lower()
        if course_type in {"sharecourse", "share_course", "share"}:
            return True
        if secret and not str(secret).isdigit():
            # recruitAndCourseId / secret 形态，属于共享课
            return True
        if "recruitandcourseid=" in link or "studyvideoh5.zhihuishu.com" in link \
                or "studywisdomh5.zhihuishu.com" in link or "wisdom-mooc.zhihuishu.com" in link:
            return True
        if "polymas.com" in link:
            return False
        return False

    def list_courses(self) -> list[Course]:
        self._require_session()
        result: list[Course] = []
        errors = []
        seen_keys: set[str] = set()

        def remember(*keys: str) -> None:
            for key in keys:
                if key:
                    seen_keys.add(str(key))

        def already(keys: list[str]) -> bool:
            return any(str(key) in seen_keys for key in keys if key)

        # 1) 共享学分课
        try:
            for item in self.fucker.getZhidaoList() or []:
                secret = str(self._item_get(item, "secret", default="") or "")
                course_id = str(self._item_get(item, "courseId", default="") or "")
                if not secret:
                    continue
                if already([f"secret:{secret}", f"course:{course_id}"]):
                    continue
                remember(f"secret:{secret}", f"course:{course_id}")
                result.append(Course(
                    self.platform_id,
                    f"zhidao:{secret}",
                    self._course_name(item, secret),
                    {"kind": "zhidao", "native_id": secret, "mode": "normal",
                     "numeric_id": course_id or None},
                ))
        except Exception as exc:
            errors.append(f"zhidao: {exc}")

        # 2) 校内学分课
        try:
            for item in self.fucker.getHikeList() or []:
                native_id = str(self._item_get(item, "courseId", default="") or "")
                if not native_id or already([f"course:{native_id}", f"hike:{native_id}"]):
                    continue
                remember(f"course:{native_id}", f"hike:{native_id}")
                result.append(Course(
                    self.platform_id,
                    f"hike:{native_id}",
                    self._course_name(item, native_id),
                    {"kind": "hike", "native_id": native_id, "mode": "normal"},
                ))
        except Exception as exc:
            errors.append(f"hike: {exc}")

        # 3) Polymas：跳过与共享课重复的条目，只保留真正的 polymas 智课
        try:
            for item in self.fucker.getPolymasList() or []:
                native_id = str(self._item_get(item, "courseId", default="") or "")
                origen = self._item_get(item, "origenParam", default=None)
                secret = str(self._item_get(item, "secret", default="")
                             or self._item_get(origen, "secret", default="") or "")
                overlap_keys = [f"course:{native_id}", f"secret:{secret}", f"polymas:{native_id}"]
                if self._polymas_looks_like_share_course(item) or already(overlap_keys):
                    # 仍记下 key，避免后续再混入
                    remember(*overlap_keys)
                    continue
                if not native_id:
                    continue
                remember(*overlap_keys)
                result.append(Course(
                    self.platform_id,
                    f"polymas:{native_id}",
                    self._course_name(item, native_id),
                    {"kind": "polymas", "native_id": native_id, "mode": "polymas"},
                ))
        except Exception as exc:
            errors.append(f"polymas: {exc}")

        self._append_zhidao_ai_courses(result, errors)
        if not result and errors:
            raise AdapterError(f"获取智慧树课程失败：{'；'.join(errors)}")
        return result

    def run_course(
        self,
        course: Course,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> None:
        kind = str(course.metadata.get("kind") or "")
        is_ai = kind == "zhidao_ai" or str(course.course_id).startswith("zhidao_ai:")
        if is_ai:
            ai = ai_settings(self.config)
            validate_ai(ai)
            try:
                course_id = int(course.metadata.get("native_id") or "")
                class_id = int(course.metadata.get("class_id") or "")
                if (course_id <= 0 or class_id <= 0) and str(course.course_id).startswith("zhidao_ai:"):
                    parts = str(course.course_id).split(":")
                    if len(parts) >= 3:
                        course_id = int(parts[1])
                        class_id = int(parts[2])
                if course_id <= 0 or class_id <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                raise AdapterError("知到 AI 课程的 Course ID 和 Class ID 必须为正整数") from None
        self._require_session()
        if cancel_event and cancel_event.is_set():
            return
        native_id = course.metadata.get("native_id", course.course_id)
        if kind == "polymas" or str(course.course_id).startswith("polymas:"):
            native_id = f"polymas:{native_id}" if not str(native_id).startswith("polymas:") else str(native_id)
        route = "AI 刷课" if is_ai else ("Polymas AI 智课" if str(native_id).startswith("polymas:") else "普通刷课")
        if progress:
            progress(ProgressEvent(f"开始执行：{course.name}（自动判定：{route}）"))
        try:
            from logger import logger

            captcha_attempted = False
            captcha_issue = ""
            error_messages = []

            def handle_captcha():
                nonlocal captcha_attempted, captcha_issue
                if captcha_attempted or is_ai:
                    return False
                captcha_attempted = True
                try:
                    return self._open_captcha_browser(str(native_id), progress, cancel_event)
                except AdapterError as exc:
                    captcha_issue = str(exc)
                    if progress:
                        progress(ProgressEvent(captcha_issue, level="error"))
                    return False

            class ProgressHandler(logging.Handler):
                has_errors = False
                requires_captcha = False

                def emit(self, record):
                    message = record.getMessage()
                    is_captcha = record.levelno >= logging.ERROR and (
                        "captcha required" in message.casefold()
                        or "滑块验证" in message
                        or "需要验证码" in message
                    )
                    if is_captcha:
                        self.requires_captcha = True
                    elif record.levelno >= logging.ERROR:
                        self.has_errors = True
                        if message and message not in error_messages:
                            error_messages.append(message)
                    if not progress:
                        return
                    levels = {
                        logging.WARNING: "warning",
                        logging.ERROR: "error",
                        logging.CRITICAL: "error",
                    }
                    if record.levelno >= logging.INFO:
                        progress(ProgressEvent(
                            "智慧树要求滑块验证，请在弹出的 Playwright 窗口完成验证。"
                            if is_captcha else message,
                            current=getattr(record, "progress_current", None),
                            total=getattr(record, "progress_total", None),
                            level="warning" if is_captcha else levels.get(record.levelno, "info"),
                        ))

            handler = ProgressHandler()
            logger.addHandler(handler)
            previous_captcha_handler = getattr(self.fucker, "captcha_handler", None)
            self.fucker.captcha_handler = handle_captcha
            try:
                if is_ai:
                    self.fucker.fuckAiCourse(course_id, class_id, aiConfig=ai, no_exam=not ai["enabled"])
                else:
                    self.fucker.fuckCourse(str(native_id))
            finally:
                if previous_captcha_handler is None:
                    try:
                        del self.fucker.captcha_handler
                    except AttributeError:
                        pass
                else:
                    self.fucker.captcha_handler = previous_captcha_handler
                logger.removeHandler(handler)
            if handler.requires_captcha:
                detail = f"：{captcha_issue}" if captcha_issue else ""
                raise AdapterError(f"智慧树要求滑块验证，但验证未完成或未通过{detail}。")
            if handler.has_errors:
                detail = error_messages[-1] if error_messages else "智慧树未提供错误详情"
                if "多窗口" in detail or "code: -10" in detail or "code -10" in detail:
                    raise AdapterError(
                        "智慧树拒绝进度上报（code -10「疑是多窗口观看」），已自动重建学习会话并重试 3 次仍失败。"
                        "说明同一账号的学习会话正被持续抢占：请关闭浏览器里正在播放的智慧树视频页、退出其它设备上的智慧树学习，"
                        "并确认没有另一个本程序实例在跑同一账号，然后重试。"
                    )
                raise AdapterError(f"课程“{course.name}”执行期间出现错误：{detail}")
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"执行课程“{course.name}”失败：{exc}") from exc
        if progress:
            progress(ProgressEvent(f"已完成：{course.name}", current=1, total=1))

    # ------------------------------------------------------------------ #
    # 平时测试 / 作业（能力本体在 app/zhihuishu_exam.py，这里只做接线）
    # ------------------------------------------------------------------ #
    def _homework_settings(self) -> dict[str, Any]:
        candidate = self.config.get("zhihuishu")
        if not isinstance(candidate, dict):
            candidate = {}
        homework = candidate.get("homework")
        base = {
            "enabled": False, "api_base": "", "api_key": "", "model_name": "",
            "timeout": 45, "answer_delay": 0.8, "auto_submit": False,
            "api_retries": 4, "api_retry_backoff": 1.5, "only_missing": False,
            "overwrite_with_verified": True,
            # 重做：卷子已交卷且没满分时，开刷前自动消耗 1 次重做机会换新卷面
            "auto_redo": True, "redo_when": "not_full", "redo_max": 3,
        }
        if isinstance(homework, dict):
            base.update({k: v for k, v in homework.items() if v is not None})
        return base

    def _homework_ai_config(self) -> dict[str, Any]:
        """作业 AI 配置：作业级字段优先，回落全局 ai.openai，再回落可用的答题端点。

        config.json 里 ai.openai.api_key 常见是 `****…****` 这类占位符（或掩码串），
        过去只判"非空"，于是占位符被当成真 key → 每道题 401 → 未命中题库的题全靠人工。
        这里统一按"可用"判定挑端点，保证桌面端刷题一定有可用的 LLM。
        """
        homework = self._homework_settings()
        global_ai = self.config.get("ai")
        global_ai = global_ai if isinstance(global_ai, dict) else {}
        chaoxing = self.config.get("chaoxing")
        chaoxing = chaoxing if isinstance(chaoxing, dict) else {}

        def section(value: Any) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        sources: list[tuple[str, dict[str, Any]]] = [
            ("zhihuishu.homework", homework),
            ("ai.openai", section(global_ai.get("openai"))),
            ("chaoxing.exam_auto", section(chaoxing.get("exam_auto"))),
            ("chaoxing.exam_assist", section(chaoxing.get("exam_assist"))),
        ]

        def usable_key(key: str) -> bool:
            if not key:
                return False
            if "*" in key or "…" in key or key.strip().strip("*") == "":
                return False
            return len(key) >= 12

        chosen_name, chosen = "none", {}
        for name, conf in sources:
            if all(
                str(conf.get(field) or "").strip()
                for field in ("api_base", "model_name")
            ) and usable_key(str(conf.get("api_key") or "").strip()):
                chosen_name, chosen = name, conf
                break

        return {
            "api_base": str(chosen.get("api_base") or "").strip(),
            "api_key": str(chosen.get("api_key") or "").strip(),
            "model_name": str(chosen.get("model_name") or "").strip(),
            "timeout": homework.get("timeout", chosen.get("timeout", 45)),
            "api_retries": homework.get("api_retries", chosen.get("api_retries", 4)),
            "api_retry_backoff": homework.get(
                "api_retry_backoff", chosen.get("api_retry_backoff", 1.5)
            ),
            "source": chosen_name,
        }

    def _exam_client(self, progress: ProgressCallback | None = None, target=None):
        from app.zhihuishu_exam import ZhihuishuExamClient, exam_page_url
        from app.zhihuishu_homework import exam_cookies

        self._require_session()
        proxies = {k: v for k, v in (self.config.get("proxies") or {}).items() if v}
        fromurl = exam_page_url(target) if target is not None else ""
        # The normal Zhihuishu login gives us jt-cas, while the exam service
        # needs its own o_session_id.  Bootstrap that child session through
        # the SSO flow when it is not already present.
        def session_log(message: str) -> None:
            if progress:
                progress(ProgressEvent(f"智慧树考试会话：{message}"))

        cookies = exam_cookies(
            self.fucker,
            fromurl=fromurl,
            log=session_log,
        )

        def refresh_session():
            if progress:
                progress(ProgressEvent("考试应用会话失效，正在重新建立 SSO 会话…", level="warning"))
            refreshed = exam_cookies(
                self.fucker,
                fromurl=fromurl,
                log=session_log,
                force_bootstrap=True,
            )
            from requests.cookies import cookiejar_from_dict

            engine = self.fucker
            engine._cookies = cookiejar_from_dict(refreshed)
            try:
                from utils import get_cookie_value

                engine.uuid = json.loads(unquote(get_cookie_value(refreshed, "CASLOGC") or ""))["uuid"]
                engine._cookies[f"exitRecod_{engine.uuid}"] = "2"
            except (KeyError, TypeError, ValueError):
                pass
            engine.session.cookies = engine.cookies.copy()
            for cached in getattr(engine, "context", {}).values():
                cached.cookies = engine.cookies.copy()
            self._save_cookies()
            return refreshed

        return ZhihuishuExamClient(
            cookies=cookies,
            cookie_file=self._cookie_path(),
            proxies=proxies,
            timeout=float(self.config.get("homework_timeout") or 20.0),
            refresh_session=refresh_session,
        )

    def _videolist(self, course: Course):
        """取一门课的 videolist（章节里挂着 studentExamDto = 平时测试入口）。"""
        RAC_id = str(course.metadata.get("native_id") or course.course_id)
        kind = str(course.metadata.get("kind") or "")
        if kind in ("polymas", "zhidao_ai"):
            raise AdapterError("AI 智课 / AI 课程的平时测试不在章节里，暂不支持自动作答")
        return self.fucker.videoList(RAC_id)

    def list_homeworks(
        self,
        course: Course,
        *,
        progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """列出某门课程的「章节平时测试 + 见面课测验」（只读）。

        章节测试来自 learning/videolist 的 studentExamDto；
        见面课测验不在章节树上，需另调 getStudentHomework(flag=1)。
        """
        from app.zhihuishu_exam import (
            merge_homework_targets,
            targets_from_homework_list,
            targets_from_videolist,
        )

        self._require_session()
        if progress:
            progress(ProgressEvent(f"读取「{course.name}」章节，查找平时测试。"))
        chapters = self._videolist(course)
        chapter_targets = targets_from_videolist(chapters)

        recruit_id = ""
        course_id = ""
        school_id = ""
        if chapter_targets:
            recruit_id = str(chapter_targets[0].recruit_id or "")
            course_id = str(chapter_targets[0].course_id or "")
            school_id = str(chapter_targets[0].school_id or "")
        try:
            course_id = str(getattr(chapters, "courseId", None) or (chapters.get("courseId") if isinstance(chapters, dict) else "") or course_id or "")
            recruit_id = str(getattr(chapters, "recruitId", None) or (chapters.get("recruitId") if isinstance(chapters, dict) else "") or recruit_id or "")
        except Exception:
            pass

        secret = str(course.metadata.get("native_id") or course.course_id or "")
        if secret.startswith("zhidao:"):
            secret = secret.split(":", 1)[-1]
        try:
            qc = self.fucker.queryCourse(secret)
            recruit_id = str(getattr(qc, "recruitId", None) or recruit_id or "")
            if not school_id:
                school_id = str(getattr(qc, "schoolId", None) or "")
            if not course_id:
                course_id = str(getattr(qc, "courseId", None) or "")
        except Exception as exc:
            if progress:
                progress(ProgressEvent(
                    f"解析学号/学校信息失败，见面课测验可能无法列出：{exc}",
                    level="warning",
                ))

        meet_targets: list = []
        if recruit_id and course_id and school_id:
            if progress:
                progress(ProgressEvent(f"读取「{course.name}」见面课测验列表。"))
            try:
                client = self._exam_client(
                    progress=progress,
                    target=chapter_targets[0] if chapter_targets else None,
                )
                rt = client.homework_list(recruit_id, course_id=course_id, flag="1")
                meet_targets = targets_from_homework_list(
                    rt,
                    recruit_id=recruit_id,
                    course_id=course_id,
                    school_id=school_id,
                    kind="meet",
                )
            except Exception as exc:
                if progress:
                    progress(ProgressEvent(
                        f"见面课测验列表获取失败（章节测试仍可用）：{exc}",
                        level="warning",
                    ))
                meet_targets = []
        elif progress:
            progress(ProgressEvent(
                "缺少 recruitId/courseId/schoolId，跳过见面课测验发现。",
                level="warning",
            ))

        targets = merge_homework_targets(chapter_targets, meet_targets)
        results: list[dict[str, Any]] = []
        for target in targets:
            tuple_data = target.five_tuple()
            is_meet = str(getattr(target, "kind", "") or "") == "meet" or int(getattr(target, "meet_course_type", 0) or 0) == 1
            display = target.name or target.exam_id
            if is_meet and display and not str(display).startswith("见面课"):
                display = f"见面课 · {display}"
            results.append({
                "platform": self.platform_id,
                "correlationId": f"{course.course_id}:{target.stu_exam_id}",
                "name": display,
                "chapterName": target.name,
                "deadline": target.deadline or "",
                "examUrl": target.exam_url or "",
                "recruitId": tuple_data["recruitId"],
                "examId": tuple_data["examId"],
                "studentExamId": tuple_data["studentExamId"],
                "schoolId": tuple_data["schoolId"],
                "courseId": tuple_data["courseId"],
                "courseName": course.name,
                "courseKey": course.course_id,
                "kind": "meet" if is_meet else "chapter",
                "meetCourseType": 1 if is_meet else int(getattr(target, "meet_course_type", 0) or 0),
                "state": getattr(target, "state", None),
            })
        if progress:
            meet_n = sum(1 for r in results if r.get("kind") == "meet")
            chapter_n = len(results) - meet_n
            progress(ProgressEvent(
                f"「{course.name}」找到 {len(results)} 份测试（章节 {chapter_n} / 见面课 {meet_n}）",
                current=len(results), total=len(results),
            ))
        return results

    def _answer_bank_path(self) -> Path:
        return data_root() / "work" / "zhs_answer_bank.json"

    def fetch_homework_answers(
        self, task: dict[str, Any], *, progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        from app.zhihuishu_answer_bank import fetch_answer_images
        from app.zhihuishu_exam import HomeworkTarget

        target = HomeworkTarget.from_mapping(task)
        result = fetch_answer_images(self._exam_client(progress=progress, target=target), target,
                                     progress=progress, cancel_event=cancel_event)
        for item in result["items"]:
            item.pop("raw", None)
        return result

    def harvest_homework_answers(
        self, task: dict[str, Any], *, progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        from app.zhihuishu_answer_bank import sync_answer_bank
        from app.zhihuishu_exam import HomeworkTarget

        target = HomeworkTarget.from_mapping(task)
        return sync_answer_bank(self._exam_client(progress=progress, target=target), target, self._answer_bank_path(),
                                progress=progress, cancel_event=cancel_event)

    def run_homework(
        self,
        task: dict[str, Any],
        *,
        ai: dict[str, Any] | None = None,
        auto_submit: bool | None = None,
        auto_redo: bool | None = None,
        redo_when: str | None = None,
        redo_max: int | None = None,
        overwrite_existing_with_verified: bool | None = None,
        confirm_ai_only_submit: Callable[[list[str]], bool] | None = None,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """优先按答案图和题库作答，未命中时用 AI；显式开启后才交卷。"""
        from app.zhihuishu_exam import (
            REDO_LIMIT,
            HomeworkRunner,
            HomeworkTarget,
            ZhihuishuExamError,
            ZhihuishuSessionError,
            openai_answerer,
        )
        from app.zhihuishu_answer_bank import AnswerBank, AnswerImageLookup, fetch_answer_images

        data = dict(task or {})
        try:
            target = HomeworkTarget.from_mapping(data)
        except ZhihuishuExamError as exc:
            raise AdapterError(f"作业定位参数无效：{exc}") from exc

        homework = self._homework_settings()
        bank = AnswerBank(self._answer_bank_path())
        client = self._exam_client(progress=progress, target=target)

        image_answers = AnswerImageLookup()
        if not (cancel_event and cancel_event.is_set()):
            if progress:
                progress(ProgressEvent("优先读取本次测试答案图；未识别题目将回退到本地题库和 AI"))
            try:
                fetched = fetch_answer_images(
                    client, target, progress=progress, cancel_event=cancel_event,
                )
                image_answers = AnswerImageLookup(fetched["items"])
                if progress:
                    progress(ProgressEvent(f"当前测试识别到 {image_answers.count} 道答案图"))
            except ZhihuishuSessionError:
                raise
            except Exception as exc:
                if progress:
                    progress(ProgressEvent(
                        f"答案图获取失败（{type(exc).__name__}），将回退到本地题库和 AI",
                        level="warning",
                    ))

        def verified_answerer(question):
            return image_answers.answer_labels(question) or bank.answer_labels(question)

        answerer = None
        if ai is None or ai.get("enabled", True):
            ai_config = self._homework_ai_config() if ai is None else {**self._homework_ai_config(), **ai}
            if not (ai_config.get("api_base") and ai_config.get("api_key") and ai_config.get("model_name")):
                if progress:
                    progress(ProgressEvent("未配置 AI，将仅使用已验证题库答案；未命中的题目需要人工处理", level="warning"))
            else:
                answerer = openai_answerer(
                    ai_config,
                    on_retry=lambda message: progress and progress(
                        ProgressEvent(f"AI 请求重试：{message}", level="warning")
                    ),
                )

        cache_path = data_root() / "work" / "zhs_homework_cache.json"
        runner = HomeworkRunner(
            client,
            answerer=answerer,
            verified_answerer=verified_answerer,
            answer_cache=cache_path,
            delay=float(homework.get("answer_delay") or 0.0),
            only_missing=bool(homework.get("only_missing", False)),
            overwrite_existing_with_verified=(
                bool(homework.get("overwrite_with_verified", True))
                if overwrite_existing_with_verified is None
                else bool(overwrite_existing_with_verified)
            ),
            auto_redo=(
                bool(homework.get("auto_redo", True)) if auto_redo is None else bool(auto_redo)
            ),
            redo_when=(
                str(homework.get("redo_when") or "not_full")
                if redo_when is None
                else str(redo_when)
            ),
            redo_max=(
                int(homework.get("redo_max") or REDO_LIMIT)
                if redo_max is None
                else int(redo_max)
            ),
            confirm_ai_only_submit=confirm_ai_only_submit,
            progress=progress,
            cancel_event=cancel_event,
        )
        submit = bool(homework.get("auto_submit", False)) if auto_submit is None else bool(auto_submit)
        result = runner.run(target, submit=submit)
        payload = result.as_dict()
        payload["courseName"] = str(data.get("courseName") or "")
        payload["examUrl"] = target.exam_url or str(data.get("examUrl") or "")
        payload["autoSubmit"] = submit
        payload["autoRedo"] = bool(runner.auto_redo)
        return payload

    def run_homeworks(
        self,
        course: Course,
        *,
        auto_submit: bool | None = None,
        auto_redo: bool | None = None,
        redo_when: str | None = None,
        redo_max: int | None = None,
        overwrite_existing_with_verified: bool | None = None,
        confirm_ai_only_submit: Callable[[list[str]], bool] | None = None,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """按章节顺序完成一门课的全部平时测试。

        单份测试失败会记录并继续下一份；会话失效或用户主动停止则结束整批任务。
        """
        from app.zhihuishu_exam import ZhihuishuSessionError

        tasks = self.list_homeworks(course, progress=progress)
        total = len(tasks)
        summary: dict[str, Any] = {
            "courseName": course.name,
            "total": total,
            "completed": 0,
            "failed": 0,
            "saved": 0,
            "skipped": 0,
            "pending": 0,
            "aiOnly": 0,
            "submitted": 0,
            "redoApplied": 0,
            "redoItems": [],
            "unsubmitted": 0,
            "cancelled": False,
            "items": [],
        }
        if not tasks:
            if progress:
                progress(ProgressEvent("该课程没有可执行的平时测试", level="warning", current=0, total=0))
            return summary

        def count(value: Any) -> int:
            return len(value) if isinstance(value, (dict, list, tuple, set)) else 0

        for index, task in enumerate(tasks, start=1):
            if cancel_event and cancel_event.is_set():
                summary["cancelled"] = True
                break
            if progress:
                progress(ProgressEvent(
                    f"[{index}/{total}] 开始：{task.get('name') or task.get('chapterName') or '平时测试'}",
                    current=index - 1,
                    total=total,
                ))
            try:
                result = self.run_homework(
                    task,
                    auto_submit=auto_submit,
                    auto_redo=auto_redo,
                    redo_when=redo_when,
                    redo_max=redo_max,
                    overwrite_existing_with_verified=overwrite_existing_with_verified,
                    confirm_ai_only_submit=confirm_ai_only_submit,
                    progress=progress,
                    cancel_event=cancel_event,
                )
            except ZhihuishuSessionError:
                raise
            except AdapterError as exc:
                summary["failed"] += 1
                summary["items"].append({
                    "id": str(task.get("studentExamId") or task.get("examId") or ""),
                    "name": str(task.get("name") or task.get("chapterName") or "平时测试"),
                    "error": str(exc),
                })
                if progress:
                    progress(ProgressEvent(
                        f"[{index}/{total}] 失败，继续下一份：{exc}",
                        level="error",
                        current=index,
                        total=total,
                    ))
                continue

            if result.get("cancelled"):
                summary["cancelled"] = True
                break
            saved = count(result.get("saved"))
            skipped = count(result.get("skipped"))
            pending = count(result.get("pending"))
            ai_only = count(result.get("aiOnly"))
            failed = count(result.get("failed"))
            summary["completed"] += 1
            summary["saved"] += saved
            summary["skipped"] += skipped
            summary["pending"] += pending
            summary["aiOnly"] = summary.get("aiOnly", 0) + ai_only
            summary["failed"] += failed
            redo_info = result.get("redo") if isinstance(result.get("redo"), dict) else {}
            if redo_info.get("applied"):
                summary["redoApplied"] += 1
                summary["redoItems"].append({
                    "id": str(task.get("studentExamId") or task.get("examId") or ""),
                    "name": str(task.get("name") or task.get("chapterName") or "平时测试"),
                    "score": redo_info.get("score"),
                    "totalScore": redo_info.get("totalScore"),
                })
            if result.get("submitted"):
                summary["submitted"] += 1
            else:
                summary["unsubmitted"] += 1
            summary["items"].append({
                "id": str(task.get("studentExamId") or task.get("examId") or ""),
                "name": str(result.get("examName") or task.get("name") or task.get("chapterName") or "平时测试"),
                "saved": saved,
                "skipped": skipped,
                "pending": pending,
                "aiOnly": ai_only,
                "failed": failed,
                "submitted": bool(result.get("submitted")),
            })
            if progress:
                progress(ProgressEvent(
                    f"[{index}/{total}] 已完成作答"
                    f"（{'已交卷' if result.get('submitted') else '未交卷'}）："
                    f"{task.get('name') or task.get('chapterName') or '平时测试'}"
                    + ("（已用 1 次重做机会换新卷）" if redo_info.get("applied") else ""),
                    level="success" if failed == 0 and pending == 0 and result.get("submitted")
                    else "warning",
                    current=index,
                    total=total,
                ))

        if progress:
            level = "warning" if summary["failed"] or summary["pending"] or summary["cancelled"] else "success"
            progress(ProgressEvent(
                f"整门课处理结束：完成 {summary['completed']}/{total} 份"
                f"（交卷 {summary['submitted']} 份、未交卷 {summary['unsubmitted']} 份），"
                f"作答 {summary['saved']} 题，失败 {summary['failed']} 题"
                + (f"，自动重做 {summary['redoApplied']} 份" if summary.get("redoApplied") else "")
                + ("，任务已停止" if summary["cancelled"] else ""),
                level=level,
                current=total if not summary["cancelled"] else summary["completed"],
                total=total,
            ))
        return summary
