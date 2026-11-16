"""Chaoxing exam automation: enter exam, wait for user captcha, AI-answer, submit.

Captcha is never bypassed. When the slide puzzle appears the runner pauses and
waits for the user to finish it in the managed browser window.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import re
import time
from threading import Event
from types import SimpleNamespace
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from app.models import AdapterError, ProgressEvent


ProgressCallback = Callable[[ProgressEvent], None]

HOST = "https://mooc1-api.chaoxing.com"
EXAMNOTES_PATH = "/exam-ans/exam/test/examcode/examnotes"
EXAM_LIST_PATH = "/exam-ans/mooc2/exam/exam-list"
ANSWER_MARKERS = ("reVersionTestStartNew", "/exam-ans/exam/test/")
# status=0 全部/可进入，status=1 常含进行中；2 多为已结束。拉取时合并去重。
EXAM_LIST_STATUSES = ("0", "1", "2")

TYPE_MAP = {
    "0": "single",
    "1": "multiple",
    "2": "fill",
    "3": "judge",
    "4": "essay",
    "单选": "single",
    "多选": "multiple",
    "填空": "fill",
    "判断": "judge",
    "简答": "essay",
    "材料": "essay",
    "single": "single",
    "multiple": "multiple",
    "fill": "fill",
    "judge": "judge",
    "essay": "essay",
    "choice": "single",
}


def map_display_answers_to_data(
    answers: list[str],
    options: list[dict[str, Any]] | None,
    *,
    question_type: str = "",
) -> list[str]:
    """Map AI display-letter answers to Chaoxing option `data` values.

    When ``randomOptions=true``, the letter shown on the page (A/B/C/D) may differ
    from ``span[data]``. Clicking must target ``data``, otherwise the wrong option
    is selected or nothing is written to ``#answer<qid>``.
    """
    raw = [str(item or "").strip() for item in (answers or []) if str(item or "").strip()]
    if not raw:
        return []
    type_key = TYPE_MAP.get(str(question_type or "").strip(), str(question_type or "").strip())
    if type_key in {"fill", "essay"}:
        return raw

    # Flatten "ABC" style multiple answers into per-letter tokens when needed.
    tokens: list[str] = []
    for item in raw:
        letters = re.sub(r"[^A-Za-z]", "", item).upper()
        if type_key == "multiple" and len(letters) > 1:
            tokens.extend(list(letters))
        else:
            tokens.append(item)

    opts = [item for item in (options or []) if isinstance(item, dict)]
    if not opts:
        # No option metadata: keep letters only.
        out: list[str] = []
        for token in tokens:
            if re.search(r"对|正确|true|yes", token, re.I):
                letter = "A"
            elif re.search(r"错|错误|false|no", token, re.I):
                letter = "B"
            else:
                letter = re.sub(r"[^A-Za-z]", "", token).upper() or token
            if letter and letter not in out:
                out.append(letter)
        return out

    mapped: list[str] = []
    for token in tokens:
        if re.search(r"对|正确|true|yes", token, re.I):
            needle = "A"
        elif re.search(r"错|错误|false|no", token, re.I):
            needle = "B"
        else:
            needle = re.sub(r"[^A-Za-z]", "", token).upper() or token
        hit = ""
        # 1) 先按显示字母 label 匹配（AI 返回的就是页面上看到的 A/B/C/D）
        if needle:
            for opt in opts:
                label = str(opt.get("label") or "").strip().upper()
                data = str(opt.get("data") or "").strip().upper()
                if label == needle or label.startswith(needle):
                    hit = data or label
                    break
        # 2) 再按文案匹配
        if not hit and token:
            for opt in opts:
                data = str(opt.get("data") or "").strip().upper()
                label = str(opt.get("label") or "").strip().upper()
                text = str(opt.get("text") or "").strip()
                if text == token or token in text:
                    hit = data or label or token
                    break
        # 3) 最后才按 data 自身匹配（调用方若已传入 data 真值）
        if not hit and needle:
            for opt in opts:
                data = str(opt.get("data") or "").strip().upper()
                label = str(opt.get("label") or "").strip().upper()
                if data == needle:
                    hit = data or label
                    break
        if not hit:
            hit = needle
        if hit and hit not in mapped:
            mapped.append(hit)
    return mapped


@dataclass(frozen=True)
class ExamTarget:
    exam_id: str
    course_id: str
    class_id: str
    cpi: str
    notes_url: str

    @classmethod
    def from_inputs(
        cls,
        *,
        exam_id: str = "",
        course_id: str = "",
        class_id: str = "",
        cpi: str = "",
        notes_url: str = "",
    ) -> "ExamTarget":
        url = (notes_url or "").strip()
        query: dict[str, str] = {}
        if url:
            parsed = urlparse(url)
            raw = parse_qs(parsed.query)
            query = {key: (values[0] if values else "") for key, values in raw.items()}
            exam_id = exam_id or query.get("examId") or query.get("tId") or ""
            course_id = course_id or query.get("courseId") or query.get("courseid") or ""
            class_id = class_id or query.get("classId") or query.get("clazzid") or query.get("classid") or ""
            cpi = cpi or query.get("cpi") or ""
        exam_id, course_id, class_id, cpi = (
            str(exam_id).strip(),
            str(course_id).strip(),
            str(class_id).strip(),
            str(cpi).strip(),
        )
        if not all(value.isdigit() for value in (exam_id, course_id, class_id, cpi)):
            raise AdapterError("请填写有效的 examId / courseId / classId / cpi，或粘贴完整 examnotes URL。")
        notes = urlunparse((
            "https",
            "mooc1-api.chaoxing.com",
            EXAMNOTES_PATH,
            "",
            urlencode({"examId": exam_id, "courseId": course_id, "classId": class_id, "cpi": cpi}),
            "",
        ))
        return cls(exam_id, course_id, class_id, cpi, notes)


@dataclass(frozen=True)
class ExamListItem:
    exam_id: str
    course_id: str
    class_id: str
    cpi: str
    answer_id: str
    title: str
    end_time: str = ""
    paper_id: str = ""
    is_retest: bool = False
    stop_exam: str = "0"
    status_text: str = ""
    course_name: str = ""
    notes_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "examId": self.exam_id,
            "courseId": self.course_id,
            "classId": self.class_id,
            "cpi": self.cpi,
            "answerId": self.answer_id,
            "title": self.title,
            "endTime": self.end_time,
            "paperId": self.paper_id,
            "isRetest": self.is_retest,
            "stopExam": self.stop_exam,
            "statusText": self.status_text,
            "courseName": self.course_name,
            "notesUrl": self.notes_url or ExamTarget.from_inputs(
                exam_id=self.exam_id,
                course_id=self.course_id,
                class_id=self.class_id,
                cpi=self.cpi,
            ).notes_url,
            "display": self.display_label(),
        }

    def display_label(self) -> str:
        course = self.course_name or self.course_id
        title = self.title or f"考试 {self.exam_id}"
        status = self.status_text or ("已停考" if self.stop_exam == "1" else "")
        end = f" · 截止 {self.end_time}" if self.end_time else ""
        suffix = f" · {status}" if status else ""
        return f"[{course}] {title}{suffix}{end}"


def exam_list_url(course_id: str, class_id: str, cpi: str, status: str = "0") -> str:
    return urlunparse((
        "https",
        "mooc1-api.chaoxing.com",
        EXAM_LIST_PATH,
        "",
        urlencode({
            "courseid": course_id,
            "clazzid": class_id,
            "cpi": cpi,
            "ut": "s",
            "enc": "",
            "status": status,
            "openc": "",
        }),
        "",
    ))


def parse_exam_list_html(
    html: str,
    *,
    course_id: str = "",
    class_id: str = "",
    cpi: str = "",
    course_name: str = "",
) -> list[ExamListItem]:
    """Parse /mooc2/exam/exam-list HTML for goTest(...) entries."""
    if not html:
        return []
    hidden = {
        name: (match.group(1) if (match := re.search(rf'id="{name}"[^>]*value="([^"]*)"', html)) else "")
        for name in ("courseId", "classId", "cpi")
    }
    course_id = str(course_id or hidden.get("courseId") or "").strip()
    class_id = str(class_id or hidden.get("classId") or "").strip()
    cpi = str(cpi or hidden.get("cpi") or "").strip()
    items: list[ExamListItem] = []
    for match in re.finditer(r'onclick="goTest\(([^)]*)\);"', html):
        raw_args = match.group(1)
        normalized = (
            raw_args.replace("false", "False")
            .replace("true", "True")
            .replace("null", "None")
        )
        try:
            args = ast.literal_eval("[" + normalized + "]")
        except (SyntaxError, ValueError):
            continue
        if len(args) < 8:
            continue
        chunk = html[match.start() : match.end() + 1200]
        title_match = re.search(r'class="overHidden2[^"]*"[^>]*>(.*?)</p>', chunk, re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        title = re.sub(r"<[^>]+>", "", title).strip()
        texts = [
            re.sub(r"\s+", " ", text).strip()
            for text in re.findall(r">([^<>]{1,30})<", chunk)
            if text and text.strip() and text.strip() != title
        ]
        status_text = next(
            (
                text for text in texts
                if text in {"进入", "重考", "查看", "未开始", "进行中", "已结束", "已交卷", "继续考试"}
                or ("考试" not in text and 1 <= len(text) <= 8)
            ),
            texts[0] if texts else "",
        )
        item_course = str(args[0]).strip() or course_id
        item_exam = str(args[1]).strip()
        item_answer = str(args[2]).strip()
        if not (item_course.isdigit() and item_exam.isdigit() and class_id.isdigit() and cpi.isdigit()):
            continue
        items.append(ExamListItem(
            exam_id=item_exam,
            course_id=item_course,
            class_id=class_id,
            cpi=cpi,
            answer_id=item_answer,
            title=title or f"考试 {item_exam}",
            end_time=str(args[3] or "").strip(),
            paper_id=str(args[4] or "").strip(),
            is_retest=bool(args[5]),
            stop_exam=str(args[7] or "0").strip(),
            status_text=status_text,
            course_name=course_name,
        ))
    # Deduplicate by examId within one course page.
    unique: dict[str, ExamListItem] = {}
    for item in items:
        unique[item.exam_id] = item
    return list(unique.values())


class _HttpChatClient:
    """OpenAI 兼容 ``/chat/completions`` 的零依赖客户端（没有 openai SDK 时兜底）。

    只实现 ExamAnswerClient 用到的那一个调用面：
    ``client.chat.completions.create(model=, messages=, stream=False) ->
    .choices[0].message.content``。
    """

    class _Completions:
        def __init__(self, owner: "_HttpChatClient") -> None:
            self._owner = owner

        def create(self, *, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
            owner = self._owner
            url = owner.api_base.rstrip("/") + "/chat/completions"
            payload = json.dumps(
                {"model": model, "messages": messages, "stream": False},
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(url, data=payload, method="POST")
            request.add_header("Content-Type", "application/json")
            if owner.api_key:
                request.add_header("Authorization", f"Bearer {owner.api_key}")
            try:
                with urllib.request.urlopen(request, timeout=owner.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
            data = json.loads(body or "{}")
            choice = (data.get("choices") or [{}])[0]
            content = str((choice.get("message") or {}).get("content") or "")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    def __init__(self, owner: Any) -> None:
        self.api_base = owner.api_base
        self.api_key = owner.api_key
        self.timeout = owner.timeout
        self.chat = SimpleNamespace(completions=self._Completions(self))


class ExamAnswerClient:
    """OpenAI-compatible answerer that returns option labels / fill texts."""

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model_name: str,
        timeout: float = 45.0,
        max_retries: int = 4,
        retry_backoff: float = 1.5,
        client: Any = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> None:
        self.api_base = self._normalize_base(api_base)
        self.api_key = str(api_key or "").strip()
        self.model_name = str(model_name or "").strip()
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.2, float(retry_backoff))
        self._client = client
        self.on_retry = on_retry

    @staticmethod
    def _normalize_base(value: str) -> str:
        base = str(value or "").strip().rstrip("/")
        if not base:
            return "https://api.openai.com/v1"
        return base if base.endswith("/v1") else base + "/v1"

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise AdapterError("请先在学习通设置中填写自动考试 API Key。")
            try:
                from openai import OpenAI
            except ImportError:
                # 没有 openai SDK 时不再中断刷题：走自带 HTTP 客户端。
                self._client = _HttpChatClient(self)
                return self._client
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                timeout=self.timeout,
                # 由本类自己做带日志的重试，避免 SDK 静默重试叠乘。
                max_retries=0,
            )
        return self._client

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        retry_tokens = (
            "timeout", "timed out", "rate limit", "429", "500", "502", "503", "504",
            "520", "521", "522", "523", "524", "overloaded", "temporarily", "connection",
            "connect", "reset", "ssl", "gateway", "unavailable", "try again", "retry",
            "cloudflare", "network", "dns", "eof", "broken pipe",
        )
        if any(token in name for token in ("timeout", "connection", "ratelimit", "apierror", "internal")):
            return True
        return any(token in text for token in retry_tokens)

    def _notify_retry(self, message: str) -> None:
        if self.on_retry:
            try:
                self.on_retry(message)
            except Exception:
                pass

    def answer(self, question: dict[str, Any]) -> list[str]:
        qtype = TYPE_MAP.get(str(question.get("type") or "").lower(), str(question.get("type") or "unknown"))
        stem = str(question.get("stem") or "")
        options = question.get("options") or []
        prompt = self._prompt(qtype, stem, options)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是考试答题助手。只输出一个 markdown 代码块 ```answer ... ```。"
                    "选择题输出选项字母列表的 JSON，例如 [\"A\"] 或 [\"A\",\"C\"]；"
                    "判断题输出 [\"对\"] 或 [\"错\"]（或选项字母）；"
                    "填空题每行一个空的纯文本答案。"
                ),
            },
            {"role": "user", "content": prompt},
        ]
        attempts = self.max_retries + 1
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    stream=False,
                )
                content = str(response.choices[0].message.content or "")
                return self._parse(content, qtype)
            except AdapterError as exc:
                # 解析失败也可再要一次答案；认证类错误不重试。
                last_error = exc
                text = str(exc).lower()
                auth_like = any(token in text for token in ("api key", "401", "unauthorized", "invalid_api_key", "authentication"))
                if auth_like or attempt >= attempts:
                    raise
                delay = self.retry_backoff * (2 ** (attempt - 1))
                self._notify_retry(f"AI 答案无效，{delay:.1f}s 后重试（{attempt}/{attempts}）：{exc}")
                time.sleep(delay)
            except Exception as exc:
                last_error = exc
                text = str(exc).lower()
                auth_like = any(token in text for token in ("api key", "401", "unauthorized", "invalid_api_key", "authentication"))
                if auth_like or not self._is_retryable(exc) or attempt >= attempts:
                    raise AdapterError(f"自动考试 AI 请求失败（已尝试 {attempt}/{attempts} 次）：{exc}") from exc
                delay = self.retry_backoff * (2 ** (attempt - 1))
                self._notify_retry(f"AI 请求失败，{delay:.1f}s 后重试（{attempt}/{attempts}）：{exc}")
                time.sleep(delay)
        raise AdapterError(f"自动考试 AI 请求失败：{last_error}")

    @staticmethod
    def _prompt(qtype: str, stem: str, options: list[dict[str, Any]]) -> str:
        lines = [f"题型：{qtype}", f"题干：{stem}"]
        if options:
            lines.append("选项：")
            for option in options:
                label = option.get("label") or ""
                text = option.get("text") or ""
                lines.append(f"{label}. {text}")
        lines.append("请给出答案。")
        return "\n".join(lines)

    @staticmethod
    def _parse(content: str, qtype: str) -> list[str]:
        text = str(content or "").strip()
        block = re.search(r"```answer\s*\r?\n(.*?)\r?\n```", text, re.DOTALL | re.I)
        body = (block.group(1) if block else text).strip()
        if qtype == "fill":
            values = [line.strip() for line in body.splitlines() if line.strip()]
            if not values:
                raise AdapterError("AI 未返回填空答案")
            return values
        try:
            raw = json.loads(body)
        except json.JSONDecodeError:
            raw = [part.strip() for part in re.split(r"[\s,，、;；]+", body) if part.strip()]
        if isinstance(raw, dict):
            raw = raw.get("answers") or raw.get("answer") or list(raw.values())
        if not isinstance(raw, list) or not raw:
            raise AdapterError("AI 答案格式无效")
        result = []
        for item in raw:
            if isinstance(item, dict):
                value = item.get("label") or item.get("content") or item.get("id")
            else:
                value = item
            value = str(value or "").strip()
            if value:
                result.append(value)
        if not result:
            raise AdapterError("AI 答案为空")
        return result


EXTRACT_QUESTIONS_JS = r"""
() => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const roots = Array.from(document.querySelectorAll('.questionLi, [id^="sigleQuestionDiv_"]'))
    .filter(visible);
  return roots.map((root, index) => {
    const idInput = root.querySelector('input[name="questionId"]') || document.querySelector('#questionId');
    const id = clean(idInput?.value || root.getAttribute('data-question-id') || root.getAttribute('data') || '');
    const typeInput = root.querySelector(`input[name="type${id}"]`)
      || document.querySelector(`#type${id}`)
      || root.querySelector('input[name^="type"]');
    const type = clean(typeInput?.value || root.getAttribute('data-question-type') || '');
    const stemNode = root.querySelector('.mark_name, .question-title, .q_title, .ques-title, .stem, h3, h2');
    let stem = clean(stemNode?.innerText || '');
    if (!stem) stem = clean(root.innerText).slice(0, 500);
    const options = [];
    // 超星真实 DOM：.answerBg > span.num_option[data=真值]{显示字母} + .answer_p{选项文案}
    // randomOptions=true 时显示字母与 data 真值可能不一致，必须拆开。
    const rows = Array.from(root.querySelectorAll('.answerBg')).filter(visible);
    if (rows.length) {
      for (const row of rows) {
        const span = row.querySelector('.num_option, .num_option_dx, .addChoice, [data]');
        const dataVal = clean(span?.getAttribute('data') || '');
        const displayRaw = clean(span?.innerText || '');
        let label = '';
        const m = displayRaw.match(/^([A-Z]|对|错|正确|错误)/);
        if (m) label = m[1];
        const textNode = row.querySelector('.answer_p, .fl.answer_p');
        let text = clean(textNode?.innerText || '');
        if (!text) {
          text = clean(row.innerText || '');
          if (label && text.startsWith(label)) text = clean(text.slice(label.length));
        }
        if (!label && options.length < 26) label = String.fromCharCode(65 + options.length);
        if (!text && !dataVal) continue;
        if (options.some((item) => item.text === text || item.label === label)) continue;
        options.push({label, text, data: dataVal || label});
      }
    } else {
      const optionNodes = Array.from(root.querySelectorAll('.num_option, .num_option_dx, label.option, .option'))
        .filter(visible);
      for (const node of optionNodes) {
        const dataVal = clean(node.getAttribute('data') || node.querySelector('[data]')?.getAttribute('data') || '');
        const text = clean(node.innerText || '');
        if (!text) continue;
        let label = '';
        const m = text.match(/^([A-Z]|对|错|正确|错误)[.、)）\s]/);
        if (m) label = m[1];
        if (!label && dataVal && /^[A-Z]$/.test(dataVal)) label = dataVal;
        if (!label && options.length < 26) label = String.fromCharCode(65 + options.length);
        if (options.some((item) => item.text === text || item.label === label)) continue;
        options.push({label, text, data: dataVal || label});
      }
    }
    return {id, index, type, stem, options};
  }).filter((item) => item.id && item.stem);
}
"""


EXTRACT_SHEET_JS = r"""
() => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const items = Array.from(document.querySelectorAll('.topicNumber_list li[onclick], .topicNumber li[onclick], .answerSheet li[onclick]'));
  const sheet = [];
  for (const li of items) {
    const onclick = li.getAttribute('onclick') || '';
    const m = onclick.match(/getTheQuestionByStart\(\s*(\d+)\s*(?:,\s*'?([^')]+)'?)?\s*\)/);
    const start = m ? Number(m[1]) : sheet.length;
    const groupId = m && m[2] != null ? String(m[2]) : '0';
    const qid = clean(li.getAttribute('data-asq') || li.getAttribute('data-qid') || '');
    const label = clean(li.innerText || '') || String(start + 1);
    const cls = String(li.className || '');
    sheet.push({
      start,
      groupId,
      questionId: qid,
      label,
      current: li.classList.contains('current') || li.classList.contains('curr'),
      // 服务端渲染的已作答标记：class=active（current 也可能兼有）
      answered: li.classList.contains('active') || /\banswered\b/i.test(cls),
    });
  }
  // Deduplicate by start index while preserving order.
  const seen = new Set();
  const unique = [];
  for (const item of sheet) {
    if (seen.has(item.start)) continue;
    seen.add(item.start);
    unique.push(item);
  }
  return unique;
}
"""


PAGE_STATE_JS = r"""
() => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const nextBtn = Array.from(document.querySelectorAll('a, button'))
    .find((el) => visible(el) && /下一题/.test(clean(el.innerText || el.textContent || '')));
  const prevBtn = Array.from(document.querySelectorAll('a, button'))
    .find((el) => visible(el) && /上一题/.test(clean(el.innerText || el.textContent || '')));
  const submitBtn = Array.from(document.querySelectorAll('a.completeBtn, a, button'))
    .find((el) => visible(el) && /(提交|交卷)/.test(clean(el.innerText || el.textContent || '')));
  const currentLi = document.querySelector('.topicNumber_list li.current, .topicNumber li.current, .answerSheet li.current');
  let currentStart = null;
  if (currentLi) {
    const onclick = currentLi.getAttribute('onclick') || '';
    const m = onclick.match(/getTheQuestionByStart\(\s*(\d+)/);
    if (m) currentStart = Number(m[1]);
  }
  const url = location.href || '';
  const startMatch = url.match(/[?&]start=(\d+)/);
  if (currentStart == null && startMatch) currentStart = Number(startMatch[1]);
  const visibleQuestions = Array.from(document.querySelectorAll('.questionLi, [id^="sigleQuestionDiv_"]')).filter(visible).length;
  return {
    hasNext: Boolean(nextBtn),
    hasPrev: Boolean(prevBtn),
    hasSubmit: Boolean(submitBtn),
    currentStart,
    visibleQuestions,
    oneByOne: Boolean(nextBtn) || visibleQuestions <= 1,
    url,
  };
}
"""


CLICK_NEXT_JS = r"""
() => {
  // 现场结论：翻页必须先 tempSave。getTheQuestionByStart / getTheNextQuestion
  // 内部都会先 submitForm(true,...) 再跳转；直接改 location.href 会丢答案。
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const currentLi = document.querySelector('.topicNumber_list li.current, .topicNumber li.current');
  let currentStart = null;
  if (currentLi) {
    const onclick = currentLi.getAttribute('onclick') || '';
    const m = onclick.match(/getTheQuestionByStart\(\s*(\d+)/);
    if (m) currentStart = Number(m[1]);
  }
  if (currentStart == null) {
    const startInput = document.querySelector('#submitTest input[name="start"], input[name="start"]');
    if (startInput && String(startInput.value || '').match(/^\d+$/)) currentStart = Number(startInput.value);
  }
  if (currentStart == null) {
    const m = (location.href || '').match(/[?&]start=(\d+)/);
    if (m) currentStart = Number(m[1]);
  }

  // 优先：官方下一题（内部会 tempSave）
  if (typeof getTheNextQuestion === 'function') {
    getTheNextQuestion(1);
    return {ok: true, via: 'getTheNextQuestion', from: currentStart};
  }
  // 次选：答题卡下一题号（同样会 tempSave）
  if (typeof getTheQuestionByStart === 'function' && currentStart != null) {
    getTheQuestionByStart(currentStart + 1, '0');
    return {ok: true, via: 'getTheQuestionByStart', from: currentStart, to: currentStart + 1};
  }
  const nextBtn = Array.from(document.querySelectorAll('a, button'))
    .find((el) => visible(el) && /下一题/.test(clean(el.innerText || el.textContent || '')));
  if (nextBtn) {
    nextBtn.click();
    return {ok: true, via: 'click', from: currentStart};
  }
  return {ok: false, reason: 'next-missing', from: currentStart};
}
"""


APPLY_ANSWER_JS = r"""
({questionId, type, answers}) => {
  // 现场结论（2026-10-30）：超星无 radio；站点原生 addChoice(obj) 会：
  //   1) 给 .addChoice span 加/切 check_answer
  //   2) 把已选 span[data] 拼进 $("#answer"+qid).val(...)
  // Playwright element.click() 不一定触发内联 onclick，所以优先直接调 addChoice/addMultipleChoice；
  // 再失败则手工复刻同一写值逻辑。randomOptions 下 AI 显示字母需先映射到 data。
  const root = document.querySelector(`.questionLi input[name="questionId"][value="${questionId}"]`)
    ?.closest('.questionLi')
    || document.querySelector(`.questionLi[data="${questionId}"]`)
    || document.querySelector(`#sigleQuestionDiv_${questionId}`)
    || document.querySelector(`.questionLi`);
  if (!root) return {ok: false, reason: 'question-not-found'};
  const normalized = (answers || []).map((item) => String(item || '').trim()).filter(Boolean);
  if (!normalized.length) return {ok: false, reason: 'empty-answer'};
  const typeValue = String(type || '');
  const qid = String(questionId || '');
  const findAnswerInput = () => (
    document.getElementById('answer' + qid)
    || root.querySelector(`#answer${qid}`)
    || root.querySelector(`input[name="answer${qid}"]`)
    || document.querySelector(`input[name="answer${qid}"]`)
  );
  const findAnswersInput = () => (
    document.getElementById('answers' + qid)
    || root.querySelector(`#answers${qid}`)
    || root.querySelector(`input[name="answers${qid}"]`)
  );

  const optionRows = () => Array.from(root.querySelectorAll('.answerBg'));
  const choiceSpans = () => Array.from(document.querySelectorAll('.choice' + qid));
  const isMultiple = typeValue === '1' || typeValue === 'multiple'
    || root.querySelector('.addMultipleChoice, .num_option_dx, .check_answer_dx');

  const resolveTargets = (tokens) => {
    const rows = optionRows();
    const wanted = [];
    for (const raw of tokens) {
      let token = String(raw || '').trim();
      if (/对|正确|true|yes/i.test(token)) token = 'A';
      if (/错|错误|false|no/i.test(token)) token = 'B';
      const letter = token.replace(/[^A-Za-z]/g, '').toUpperCase();
      let hit = null;
      for (const row of rows) {
        const span = row.querySelector('.num_option, .num_option_dx, .addChoice, .addMultipleChoice, [data]') || row;
        const dataVal = String(span.getAttribute('data') || '').toUpperCase();
        const display = String(span.innerText || '').trim().toUpperCase();
        const text = String(row.querySelector('.answer_p')?.innerText || row.innerText || '').trim();
        if (letter && dataVal === letter) { hit = dataVal; break; }
        if (letter && display.startsWith(letter)) { hit = dataVal || letter; break; }
        if (token && (text === token || text.includes(token))) { hit = dataVal || letter || token; break; }
      }
      if (!hit && letter) hit = letter;
      if (hit && !wanted.includes(hit)) wanted.push(hit);
    }
    return wanted;
  };

  const writeAnswerValue = (value) => {
    const input = findAnswerInput();
    if (input) {
      input.value = value;
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
    }
    const answersInput = findAnswersInput();
    if (answersInput) {
      answersInput.value = value;
      answersInput.dispatchEvent(new Event('input', {bubbles: true}));
      answersInput.dispatchEvent(new Event('change', {bubbles: true}));
    }
    // 兼容页面里偶发的 jQuery 绑定
    try {
      if (window.jQuery) {
        window.jQuery('#answer' + qid).val(value);
        if (answersInput || window.jQuery('#answers' + qid).length) {
          window.jQuery('#answers' + qid).val(value);
        }
      }
    } catch (e) {}
    return String(findAnswerInput()?.value || findAnswersInput()?.value || '');
  };

  const syncCheckedClasses = (dataVals, multi) => {
    const wanted = new Set((dataVals || []).map((v) => String(v || '').toUpperCase()));
    const onCls = multi ? 'check_answer_dx' : 'check_answer';
    const offCls = multi ? 'check_answer' : 'check_answer_dx';
    for (const span of choiceSpans()) {
      const dataVal = String(span.getAttribute('data') || '').toUpperCase();
      span.classList.remove(offCls);
      if (wanted.has(dataVal)) span.classList.add(onCls);
      else span.classList.remove(onCls);
    }
  };

  const invokeNativeChoice = (row, multi) => {
    if (!row) return false;
    try {
      if (multi && typeof addMultipleChoice === 'function') {
        addMultipleChoice(row);
        return true;
      }
      if (!multi && typeof addChoice === 'function') {
        addChoice(row);
        return true;
      }
    } catch (e) {}
    // 退路：派发真实 click 事件，尽量触发内联 onclick="addChoice(this)"
    try {
      row.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
      return true;
    } catch (e) {}
    try { row.click(); return true; } catch (e) {}
    return false;
  };

  if (typeValue === '2' || typeValue === '4' || typeValue === 'fill' || typeValue === 'essay') {
    const areas = Array.from(root.querySelectorAll(`textarea[name^="answerEditor${qid}"]`));
    if (areas.length) {
      areas.forEach((area, index) => { area.value = normalized[index] || ''; });
      return {ok: true, applied: normalized.slice(0, areas.length), via: 'textarea-editor'};
    }
    const area = root.querySelector(`textarea[name="answer${qid}"]`) || document.querySelector(`#answer${qid}`);
    if (area) {
      const html = normalized.join('\n');
      area.value = html;
      try {
        if (window.UE && UE.instants) {
          for (const key of Object.keys(UE.instants)) {
            const ed = UE.instants[key];
            if (ed && typeof ed.setContent === 'function') ed.setContent(html.replace(/\n/g, '<br/>'));
          }
        }
      } catch (e) {}
      area.dispatchEvent(new Event('input', {bubbles: true}));
      area.dispatchEvent(new Event('change', {bubbles: true}));
      return {ok: true, applied: normalized, via: 'textarea'};
    }
    return {ok: false, reason: 'textarea-missing'};
  }

  const tokens = [];
  for (const item of normalized) {
    const letters = String(item).replace(/[^A-Za-z]/g, '').toUpperCase();
    if (letters.length > 1 && (typeValue === '1' || typeValue === 'multiple' || isMultiple)) {
      tokens.push(...letters.split(''));
    } else {
      tokens.push(item);
    }
  }
  const targets = resolveTargets(tokens);
  if (!targets.length) return {ok: false, reason: 'targets-empty', tokens};

  const multi = Boolean(isMultiple);
  const log = [];
  // 先清空已选（单选：点已选项会取消；多选：逐个取消）
  for (const span of choiceSpans()) {
    const on = span.classList.contains('check_answer') || span.classList.contains('check_answer_dx');
    if (!on) continue;
    invokeNativeChoice(span.closest('.answerBg') || span.parentElement, multi);
    log.push('clear:' + String(span.getAttribute('data') || ''));
  }

  for (const want of targets) {
    const target = String(want || '').toUpperCase();
    let row = null;
    for (const candidate of optionRows()) {
      const span = candidate.querySelector('.num_option, .num_option_dx, .addChoice, .addMultipleChoice, [data]');
      if (String(span?.getAttribute('data') || '').toUpperCase() === target) { row = candidate; break; }
    }
    if (!row) { log.push(target + ':not-found'); continue; }
    const span = row.querySelector('.addChoice, .addMultipleChoice, .num_option, .num_option_dx, [data]');
    const already = span && (span.classList.contains('check_answer') || span.classList.contains('check_answer_dx'));
    if (already) { log.push(target + ':already'); continue; }
    invokeNativeChoice(row, multi);
    log.push(target + ':invoked');
  }

  let applied = String(findAnswerInput()?.value || findAnswersInput()?.value || '');
  if (!applied) {
    // 手工复刻 addChoice：写 class + #answer
    const fallback = multi ? Array.from(new Set(targets)).join('') : targets[0];
    syncCheckedClasses(targets, multi);
    applied = writeAnswerValue(fallback);
    log.push('manual-write:' + applied);
    if (!applied) {
      return {
        ok: false,
        reason: 'answer-not-written',
        log,
        targets,
        hasAnswerInput: Boolean(findAnswerInput()),
        choiceCount: choiceSpans().length,
        rowCount: optionRows().length,
      };
    }
    return {ok: true, applied, via: 'manual-addChoice-replica', log, targets};
  }
  // 即使原生函数写了值，也同步一次 class，避免 UI 未勾选
  syncCheckedClasses(
    multi ? applied.split('') : [applied],
    multi,
  );
  return {ok: true, applied, via: 'native-addChoice', log, targets};
}
"""


SUBMIT_JS = r"""
() => {
  const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  // Prefer the page's "提交/交卷" button so confirm dialogs behave like a real student.
  const submitBtn = Array.from(document.querySelectorAll('a.completeBtn, a, button'))
    .find((el) => visible(el) && /(提交|交卷)/.test(clean(el.innerText || el.textContent || '')));
  if (submitBtn) {
    submitBtn.click();
    return {ok: true, via: 'button'};
  }
  if (typeof answerDirectSubmit === 'function') {
    answerDirectSubmit();
    return {ok: true, via: 'answerDirectSubmit'};
  }
  if (typeof submitForm === 'function') {
    submitForm(false, false);
    return {ok: true, via: 'submitForm'};
  }
  const confirm = document.querySelector('#submitConfirmPop .confirm');
  if (confirm) {
    confirm.setAttribute('onclick', 'submitForm(false,false)');
    confirm.click();
    return {ok: true, via: 'confirm'};
  }
  return {ok: false, reason: 'submit-api-missing'};
}
"""


CONFIRM_SUBMIT_JS = r"""
() => {
  const confirm = document.querySelector('#submitConfirmPop .confirm, #submitConfirmPop .jb_btn, .submitConfirmPop .confirm');
  if (confirm) {
    const style = getComputedStyle(confirm.closest('.maskDiv, .popDiv, #submitConfirmPop') || confirm);
    const parent = confirm.closest('.maskDiv, #submitConfirmPop');
    const parentVisible = !parent || (getComputedStyle(parent).display !== 'none' && getComputedStyle(parent).visibility !== 'hidden');
    if (parentVisible) {
      confirm.click();
      return {ok: true, via: 'confirm'};
    }
  }
  return {ok: false, reason: 'confirm-missing'};
}
"""


class ChaoxingExamRunner:
    """Drive one exam attempt on an existing Playwright page."""

    def __init__(
        self,
        page: Any,
        answer_client: ExamAnswerClient,
        *,
        captcha_timeout: float = 300.0,
        answer_delay: float = 0.8,
        auto_submit: bool = True,
        nav_retries: int = 3,
    ) -> None:
        self.page = page
        self.answer_client = answer_client
        self.captcha_timeout = float(captcha_timeout)
        self.answer_delay = float(answer_delay)
        self.auto_submit = bool(auto_submit)
        self.nav_retries = max(1, int(nav_retries))

    def run(
        self,
        target: ExamTarget,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        self._emit(progress, f"打开考试须知：examId={target.exam_id}")
        self.page.goto(target.notes_url, wait_until="domcontentloaded", timeout=45000)
        self.page.bring_to_front()
        self._check_cancel(cancel_event)
        if "passport" in (self.page.url or "").lower():
            raise AdapterError("学习通会话已失效，请先登录。")
        self._agree_and_enter(progress, cancel_event)
        self._wait_captcha_and_answer_page(progress, cancel_event)
        page_state = self._page_state()
        sheet = self._extract_sheet()
        # 一页一题：按答题卡逐题作答，每题后点「下一题」，全部完成后再交卷。
        # 整卷预览：仍可一次扫完所有可见题，但同样等全部写完才交卷。
        if page_state.get("oneByOne") or (sheet and page_state.get("visibleQuestions", 0) <= 1):
            answered = self._answer_one_by_one(sheet, progress, cancel_event)
        else:
            answered = self._answer_all_visible(progress, cancel_event)
        total = len(answered)
        if total == 0:
            raise AdapterError("已进入考试页，但没有识别到题目。")
        submit_result = None
        if self.auto_submit:
            self._emit(progress, "全部题目已作答，正在交卷…", total, total)
            submit_result = self._submit_exam()
            if not submit_result or not submit_result.get("ok"):
                raise AdapterError(f"交卷调用失败：{submit_result}")
            self.page.wait_for_timeout(1500)
            self._emit(progress, "交卷请求已发送（最终结果以平台页面为准）", total, total, "success")
        else:
            self._emit(progress, "全部题目已填答，未交卷（auto_submit=false）", total, total, "success")
        return {
            "examId": target.exam_id,
            "courseId": target.course_id,
            "classId": target.class_id,
            "cpi": target.cpi,
            "answered": answered,
            "submitted": bool(self.auto_submit),
            "submit": submit_result,
            "mode": "one_by_one" if (page_state.get("oneByOne") or (sheet and page_state.get("visibleQuestions", 0) <= 1)) else "all_visible",
            "finalUrl": self.page.url,
        }

    def _agree_and_enter(self, progress, cancel_event) -> None:
        self._check_cancel(cancel_event)
        # Tick "我已阅读并同意" then click enter.
        self.page.evaluate(
            """() => {
              const agree = document.querySelector('.face_agreement .agree_check');
              if (agree && !agree.classList.contains('agree_checked')) {
                document.querySelector('.face_agreement')?.click();
              }
              const start = document.querySelector('#startBtn');
              if (start) {
                start.classList.remove('opa40');
                start.classList.remove('opa40_freeze');
              }
            }"""
        )
        self.page.wait_for_timeout(300)
        # Prefer native click path so captcha / confirm dialogs appear as usual.
        start = self.page.locator("#startBtn")
        if start.count() == 0:
            raise AdapterError("考试须知页没有“进入考试”按钮。")
        start.click(timeout=8000)
        self.page.wait_for_timeout(500)
        # Confirm enter dialog if present.
        confirm = self.page.locator("#confirmEnterWin .confirm")
        if confirm.count() and confirm.is_visible():
            self._emit(progress, "确认进入考试…")
            confirm.click(timeout=5000)
        self._emit(progress, "若出现滑块验证码，请在学习通窗口中完成；通过后会自动继续")

    def _wait_captcha_and_answer_page(self, progress, cancel_event) -> None:
        deadline = time.monotonic() + self.captcha_timeout
        last_notice = 0.0
        while time.monotonic() < deadline:
            self._check_cancel(cancel_event)
            url = self.page.url or ""
            if any(marker in url for marker in ANSWER_MARKERS) and "examnotes" not in url:
                # Answer page reached.
                self.page.wait_for_timeout(800)
                return
            captcha_visible = self.page.evaluate(
                """() => {
                  const layer = document.querySelector('#eject, .u-captcha, .cx_comImageValidate');
                  if (!layer) return false;
                  const style = getComputedStyle(layer);
                  return style.display !== 'none' && style.visibility !== 'hidden';
                }"""
            )
            now = time.monotonic()
            if captcha_visible and now - last_notice > 5:
                self._emit(progress, "等待你完成滑块验证码…", level="warning")
                last_notice = now
            elif now - last_notice > 8:
                self._emit(progress, "等待进入答题页…")
                last_notice = now
            # Auto-click confirm enter if it reappears.
            try:
                confirm = self.page.locator("#confirmEnterWin .confirm")
                if confirm.count() and confirm.is_visible():
                    confirm.click(timeout=1000)
            except Exception:
                pass
            self.page.wait_for_timeout(1000)
        raise AdapterError("等待验证码/进入答题页超时。请在学习通窗口完成滑块后重试。")

    def _extract_questions(self) -> list[dict[str, Any]]:
        try:
            value = self.page.evaluate(EXTRACT_QUESTIONS_JS)
        except Exception as exc:
            raise AdapterError(f"读取题面失败：{exc}") from exc
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict) and item.get("id")]

    def _extract_sheet(self) -> list[dict[str, Any]]:
        try:
            value = self.page.evaluate(EXTRACT_SHEET_JS)
        except Exception:
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _page_state(self) -> dict[str, Any]:
        try:
            value = self.page.evaluate(PAGE_STATE_JS)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _answer_all_visible(
        self,
        progress: ProgressCallback | None,
        cancel_event: Event | None,
    ) -> list[dict[str, Any]]:
        self.answer_client.on_retry = lambda message: self._emit(progress, message, level="warning")
        questions = self._wait_questions(progress, cancel_event, attempts=self.nav_retries + 2)
        if not questions:
            return []
        self._emit(progress, f"识别到 {len(questions)} 道题（整卷预览），开始 AI 作答", 0, len(questions))
        answered: list[dict[str, Any]] = []
        for index, question in enumerate(questions):
            self._check_cancel(cancel_event)
            answered.append(self._answer_one_question(question, index, len(questions), progress))
            # 整卷模式只暂存，绝不中途交卷。
            self._temp_save()
            self.page.wait_for_timeout(int(self.answer_delay * 1000))
        return answered

    def _answer_one_by_one(
        self,
        sheet: list[dict[str, Any]],
        progress: ProgressCallback | None,
        cancel_event: Event | None,
    ) -> list[dict[str, Any]]:
        if not sheet:
            # 没有答题卡时，至少把当前可见题答完，再尝试下一题直到没有下一题。
            sheet = [{"start": 0, "groupId": "0", "questionId": "", "label": "1"}]
            state = self._page_state()
            estimated_total = max(1, int(state.get("visibleQuestions") or 1))
        else:
            estimated_total = len(sheet)

        # 绑定本轮进度回调，让 AI 重试信息进入活动日志。
        self.answer_client.on_retry = lambda message: self._emit(progress, message, level="warning")
        self._emit(progress, f"识别到约 {estimated_total} 道题（一页一题），开始逐题作答", 0, estimated_total)
        answered: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        max_steps = max(estimated_total * 2, estimated_total + 5, 20)

        for step in range(max_steps):
            self._check_cancel(cancel_event)
            state = self._page_state()
            questions = self._wait_questions(progress, cancel_event, attempts=self.nav_retries + 2)
            if not questions:
                if answered and not state.get("hasNext"):
                    break
                raise AdapterError(f"第 {step + 1} 步没有识别到当前题目（已重试读题）。")

            question = questions[0]
            qid = str(question.get("id") or "")
            if qid and qid in seen_ids and not state.get("hasNext"):
                break
            if qid and qid in seen_ids:
                moved = self._goto_next_question(progress, state.get("currentStart"))
                if not moved:
                    self._emit(progress, "已作答题目翻页失败，停止继续下一题", level="warning")
                    break
                continue

            index = len(answered)
            total_hint = max(estimated_total, index + 1 + (1 if state.get("hasNext") else 0))
            answered.append(self._answer_one_question(question, index, total_hint, progress))
            if qid:
                seen_ids.add(qid)

            state_after = self._page_state()
            is_last = not state_after.get("hasNext") or (sheet and index + 1 >= len(sheet))
            if is_last:
                self._temp_save()
                self.page.wait_for_timeout(int(self.answer_delay * 1000))
                break

            moved = self._goto_next_question(progress, state_after.get("currentStart"))
            if not moved:
                self._temp_save()
                raise AdapterError(
                    f"第 {index + 1} 题作答后未能进入下一题（已重试 {self.nav_retries} 次）。"
                    "请检查页面是否弹窗拦截，或稍后重试。"
                )
            self.page.wait_for_timeout(int(self.answer_delay * 1000))

        return answered

    def _wait_questions(
        self,
        progress: ProgressCallback | None,
        cancel_event: Event | None,
        *,
        attempts: int = 3,
    ) -> list[dict[str, Any]]:
        last: list[dict[str, Any]] = []
        for attempt in range(1, max(1, attempts) + 1):
            self._check_cancel(cancel_event)
            last = self._extract_questions()
            if last:
                return last
            if attempt < attempts:
                self._emit(progress, f"页面题目尚未加载，重试读题（{attempt}/{attempts}）…", level="warning")
                self.page.wait_for_timeout(700 * attempt)
        return last

    def _answer_one_question(
        self,
        question: dict[str, Any],
        index: int,
        total: int,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        self._emit(
            progress,
            f"作答第 {index + 1}/{total} 题（{question.get('type') or '?'}）",
            index,
            total,
        )
        # 每题独立绑定，避免跨题串日志。
        self.answer_client.on_retry = lambda message: self._emit(
            progress, f"第 {index + 1} 题 {message}", index, total, "warning")
        answers = self.answer_client.answer(question)
        # AI 返回的是页面显示字母；randomOptions 下必须映射到 span[data] 真值再点选。
        mapped = map_display_answers_to_data(
            answers,
            question.get("options") if isinstance(question.get("options"), list) else None,
            question_type=str(question.get("type") or ""),
        )
        result = None
        last_error = None
        for attempt in range(1, self.nav_retries + 1):
            try:
                result = self.page.evaluate(
                    APPLY_ANSWER_JS,
                    {
                        "questionId": question["id"],
                        "type": question.get("type"),
                        "answers": mapped or answers,
                    },
                )
                if result and result.get("ok"):
                    applied = str(result.get("applied") or "")
                    # 选择题写入后隐藏域必须非空，否则服务端不会标 active。
                    qtype = TYPE_MAP.get(str(question.get("type") or ""), str(question.get("type") or ""))
                    if qtype in {"single", "multiple", "judge", "0", "1", "3"} and not applied:
                        last_error = {"ok": False, "reason": "empty-applied", "result": result}
                        result = None
                    else:
                        break
                else:
                    last_error = result
            except Exception as exc:
                last_error = exc
                result = None
            if attempt < self.nav_retries:
                self._emit(
                    progress,
                    f"写入第 {index + 1} 题失败，重试（{attempt}/{self.nav_retries}）：{last_error}",
                    index,
                    total,
                    "warning",
                )
                self.page.wait_for_timeout(500 * attempt)
        if not result or not result.get("ok"):
            raise AdapterError(f"写入第 {index + 1} 题答案失败（已重试）：{last_error or result}")
        return {
            "questionId": question["id"],
            "type": question.get("type"),
            "answers": answers,
            "mapped": mapped,
            "applied": result.get("applied"),
            "via": result.get("via"),
        }

    def _goto_next_question(self, progress: ProgressCallback | None = None, previous_start: Any = None) -> bool:
        """Save current answer and navigate to the next question. Never final-submit."""
        for attempt in range(1, self.nav_retries + 1):
            before = self._page_state()
            before_url = before.get("url") or (self.page.url or "")
            before_start = before.get("currentStart")
            before_qid = ""
            visible = self._extract_questions()
            if visible:
                before_qid = str(visible[0].get("id") or "")
            try:
                result = self.page.evaluate(CLICK_NEXT_JS)
            except Exception as exc:
                result = {"ok": False, "reason": f"evaluate-failed:{exc}"}
            if not result or not result.get("ok"):
                self._emit(progress, f"点击下一题失败，重试（{attempt}/{self.nav_retries}）：{result}", level="warning")
                self.page.wait_for_timeout(600 * attempt)
                continue
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                self.page.wait_for_timeout(400)
                after = self._page_state()
                after_url = after.get("url") or (self.page.url or "")
                after_start = after.get("currentStart")
                after_questions = self._extract_questions()
                after_qid = str(after_questions[0].get("id") or "") if after_questions else ""
                if after_url != before_url:
                    return True
                if after_start is not None and before_start is not None and after_start != before_start:
                    return True
                if after_qid and before_qid and after_qid != before_qid:
                    return True
                if previous_start is not None and after_start is not None and after_start != previous_start:
                    return True
            self._emit(progress, f"下一题页面未切换，重试（{attempt}/{self.nav_retries}）", level="warning")
            self.page.wait_for_timeout(600 * attempt)
        return False

    def _submit_exam(self) -> dict[str, Any]:
        last: dict[str, Any] = {"ok": False, "reason": "not-attempted"}
        for attempt in range(1, self.nav_retries + 1):
            try:
                result = self.page.evaluate(SUBMIT_JS)
            except Exception as exc:
                result = {"ok": False, "reason": f"evaluate-failed:{exc}"}
            self.page.wait_for_timeout(600)
            try:
                confirm = self.page.evaluate(CONFIRM_SUBMIT_JS)
            except Exception:
                confirm = {"ok": False}
            if confirm and confirm.get("ok"):
                if isinstance(result, dict):
                    result = {**result, "confirmed": True, "ok": True}
                else:
                    result = {"ok": True, "via": "confirm-only", "confirmed": True}
            if isinstance(result, dict) and result.get("ok"):
                return result
            last = result if isinstance(result, dict) else {"ok": False, "reason": "bad-submit-result"}
            self.page.wait_for_timeout(700 * attempt)
        return last

    def _temp_save(self) -> None:
        try:
            self.page.evaluate(
                """() => {
                  if (typeof submitForm === 'function') {
                    // tempSave=true：只保存当前题，绝不交卷
                    submitForm(true, false);
                    return true;
                  }
                  return false;
                }"""
            )
        except Exception:
            pass

    @staticmethod
    def _emit(progress, message, current=None, total=None, level="info") -> None:
        if progress:
            progress(ProgressEvent(message, current, total, level))

    @staticmethod
    def _check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise AdapterError("学习通自动考试已停止")
