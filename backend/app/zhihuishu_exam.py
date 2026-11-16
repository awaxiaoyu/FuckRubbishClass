"""智慧树「在线学堂 · 作业 / 平时测试」纯 HTTP 客户端（不启动浏览器）。

逆向结论（全部以真实接口实打验证，细节见 work/ANALYSIS_zhs_homework_api.md）：

1. 传输层：业务参数整体 JSON → AES-128-CBC/PKCS7 → base64 → 表单字段 ``secretStr``

   * KEY = ``onbfhdyvz8x7otrp``，IV = ``1g3qqdh4jvbskb9x``（与 ``zd_utils.EXAM_KEY`` 同一把钥匙）
   * POST：``Content-Type: application/x-www-form-urlencoded``，body 只有 ``secretStr=<urlencode 后的 base64>``
   * GET （``app/queryIsLimitFlow``）：``?secretStr=<...>&dateFormate=<毫秒>``
   * 没有 sign / nonce / 时间窗，同一份密文可重放；唯一有状态的是 cookie 会话。

2. 鉴权：网关双重点亮 —— ``jt-cas``（.zhihuishu.com 的 CAS JWT） + ``o_session_id``。

   * 缺 ``jt-cas`` → HTTP 401 ``{"code":401,"message":"Forbidden"}``（连不存在的路径也 401）
   * 只有 ``jt-cas`` 没有 ``o_session_id`` → **HTTP 200 但回 84KB HTML 首页**（最容易误判成"通了"）
   * 所以判定成功必须卡「JSON 可解析 + 带 rt/msg/status」，不能只看 HTTP 200。

3. 取卷 ``student/doHomework`` → ``rt.examBase.workExamParts[].questionDtos[]``；
   题目唯一键是 ``eid``（``id`` 恒为 null），选项主键是 ``questionOptions[].id``（纯数字）。

4. 写答案 ``answer/saveStudentAnswer`` **一次只能一题**（数组 >1 → ``禁止批量保存答案！``），
   ``stuExamAnswer`` 是前端自己 ``JSON.stringify`` 出来的字符串，包一层数组。

5. 交卷 ``answer/submit`` 不可逆（直接判分），本模块默认拒绝执行，必须显式 ``confirm=True``。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse

import requests

try:  # 首选 pycryptodome（快）；缺失时退回纯 Python 实现（app/zhs_aes.py）
    from Crypto.Cipher import AES
except ImportError:  # pragma: no cover - 仅在无 pycryptodome 的环境走到
    from app.zhs_aes import AES  # type: ignore[assignment]

from app.models import AdapterError

ProgressCallback = Callable[[Any], None]

GATEWAY = "https://studentexam-api.zhihuishu.com/studentExam/gateway/t/v1"
EXAM_KEY = b"onbfhdyvz8x7otrp"
EXAM_IV = b"1g3qqdh4jvbskb9x"
ORIGIN = "https://onlineexamh5new.zhihuishu.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0"
)

# 只读接口（安全）
EP_HOMEWORK_LIST = "/student/getStudentHomework"
EP_PAPER = "/student/doHomework"
EP_LOOK_HOMEWORK = "/student/lookHomework"
EP_HAS_ANSWER = "/answer/hasAnswer"
EP_READBACK = "/answer/getStuAnswerInfoNew"
EP_COURSE_NAME = "/exam/getCourseName"
EP_SINGLE_RECRUIT = "/exam/getSingleRecruit"
EP_CHECK_LOCK = "/appeal/checkIsLock"
# 写接口
EP_SAVE_ANSWER = "/answer/saveStudentAnswer"
EP_TEMPORARY_SAVE = "/answer/temporarySave"
EP_SUBMIT = "/answer/submit"
# 重做（"申请重做" = saveCourseTran；redoNum.rt 是"已用次数"，剩余 = REDO_LIMIT - rt）
EP_REDO_NUM = "/student/redoNum"
EP_SAVE_COURSE_TRAN = "/student/saveCourseTran"
EP_GET_IS_OBJECT = "/exam/getIsObject"
EP_CURRENT_TIME = "/student/currentTime"
REDO_LIMIT = 3
# 重做（"申请重做" = saveCourseTran；redoNum.rt 是"已用次数"，剩余 = REDO_LIMIT - rt）
EP_REDO_NUM = "/student/redoNum"
EP_SAVE_COURSE_TRAN = "/student/saveCourseTran"
EP_GET_IS_OBJECT = "/exam/getIsObject"
EP_CURRENT_TIME = "/student/currentTime"
REDO_LIMIT = 3

# 风控旁路：studentExam 的写入/交卷会被滑块门禁拦，改发 taurus 微服务
# （同 secretStr body，服务端同库落盘）。实测 200 {"rt":{"statu":"1"}} 且可读回。
TAURUS_GATEWAY = "https://taurusexam-api.zhihuishu.com/taurusExam/gateway/t/v1"
READ_ONLY_ENDPOINTS = frozenset({
    EP_HOMEWORK_LIST,
    EP_PAPER,
    EP_LOOK_HOMEWORK,
    EP_HAS_ANSWER,
    EP_READBACK,
    "/answer/getAnswerImgInfo",
    EP_COURSE_NAME,
    EP_SINGLE_RECRUIT,
    EP_CHECK_LOCK,
    EP_REDO_NUM,
    EP_CURRENT_TIME,
})


class ZhihuishuExamError(AdapterError):
    """作业接口返回了业务错误。"""


class ZhihuishuSessionError(ZhihuishuExamError):
    """网关鉴权失败（jt-cas / o_session_id 缺失或过期）→ 需要重新登录。"""


# --------------------------------------------------------------------------- #
# 加密层
# --------------------------------------------------------------------------- #
def _pkcs7_pad(raw: bytes) -> bytes:
    pad = 16 - len(raw) % 16
    return raw + bytes([pad]) * pad


def _pkcs7_unpad(raw: bytes) -> bytes:
    if not raw:
        return raw
    pad = raw[-1]
    if 1 <= pad <= 16 and raw[-pad:] == bytes([pad]) * pad:
        return raw[:-pad]
    return raw


def secret_str(params: Mapping[str, Any] | str) -> str:
    """把业务参数加密成 ``secretStr``（base64(AES-128-CBC(PKCS7(utf-8 json))))。"""
    # 必须与浏览器 JSON.stringify 一致：紧凑分隔符（无空格）、不转义中文
    plain = params if isinstance(params, str) else json.dumps(
        params, ensure_ascii=False, separators=(",", ":")
    )
    cipher = AES.new(EXAM_KEY, AES.MODE_CBC, EXAM_IV)
    return base64.b64encode(cipher.encrypt(_pkcs7_pad(plain.encode("utf-8")))).decode("ascii")


def decrypt_secret(value: str) -> str:
    """调试用：解回 secretStr 明文。"""
    cipher = AES.new(EXAM_KEY, AES.MODE_CBC, EXAM_IV)
    return _pkcs7_unpad(cipher.decrypt(base64.b64decode(value))).decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# 目标 / URL
# --------------------------------------------------------------------------- #
# 未提交进入 dohomework，已提交进入 checkHomework；两者定位参数顺序相同。
_EXAM_URL_RE = re.compile(
    r"(?:do|check)homework/([^/?#]+)/([^/?#]+)/([^/?#]+)/([^/?#]+)/([^/?#]+)", re.I,
)


@dataclass
class HomeworkTarget:
    """一场「平时测试 / 作业」的定位五元组。"""

    recruit_id: str
    exam_id: str
    stu_exam_id: str
    school_id: str
    course_id: str
    name: str = ""
    chapter_id: int | str | None = None
    deadline: str = ""
    exam_url: str = ""
    kind: str = "chapter"  # chapter=章节平时测试 / meet=见面课测验
    meet_course_type: int = 0
    state: int | str | None = None

    def five_tuple(self) -> dict[str, str]:
        """``doHomework`` 用的键名（含 ``studentExamId``）。"""
        return {
            "recruitId": str(self.recruit_id),
            "examId": str(self.exam_id),
            "studentExamId": str(self.stu_exam_id),
            "schoolId": str(self.school_id),
            "courseId": str(self.course_id),
        }

    def answer_tuple(self) -> dict[str, str]:
        """``hasAnswer`` / ``getStuAnswerInfoNew`` / ``appeal/checkIsLock`` 用的键名（``stuExamId``）。"""
        payload = self.five_tuple()
        payload["stuExamId"] = payload.pop("studentExamId")
        return payload

    def describe(self) -> str:
        return f"{self.name or self.exam_id}（recruit={self.recruit_id} exam={self.exam_id} stu={self.stu_exam_id}）"

    @classmethod
    def from_exam_url(cls, url: str, **extra: Any) -> "HomeworkTarget":
        match = _EXAM_URL_RE.search(unquote(str(url or "")))
        if not match:
            raise ZhihuishuExamError(
                "无法从作业链接解析定位参数，需形如 "
                "#/webExamList/dohomework/<recruitId>/<stuExamId>/<examId>/<courseId>/<schoolId>/0"
                " 或对应的 checkHomework 查看链接"
            )
        recruit_id, stu_exam_id, exam_id, course_id, school_id = match.groups()
        return cls(
            recruit_id=recruit_id,
            exam_id=exam_id,
            stu_exam_id=stu_exam_id,
            school_id=school_id,
            course_id=course_id,
            exam_url=str(url),
            **extra,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HomeworkTarget":
        def pick(*names: str) -> str:
            for name in names:
                value = data.get(name)
                if value not in (None, ""):
                    return str(value)
            return ""

        if data.get("examUrl") or data.get("exam_url"):
            extra = {k: data[k] for k in ("name", "deadline", "kind", "meet_course_type", "state", "chapter_id") if k in data}
            if "meetCourseType" in data and "meet_course_type" not in extra:
                extra["meet_course_type"] = data.get("meetCourseType")
            if data.get("kind") is None and int(data.get("meetCourseType") or data.get("meet_course_type") or 0) == 1:
                extra["kind"] = "meet"
            if data.get("name") or data.get("examName"):
                extra["name"] = str(data.get("name") or data.get("examName") or "")
            if data.get("deadline") or data.get("endDate"):
                extra["deadline"] = str(data.get("deadline") or data.get("endDate") or "")
            return cls.from_exam_url(str(data.get("examUrl") or data.get("exam_url")), **extra)
        meet_raw = data.get("meetCourseType", data.get("meet_course_type", 0))
        try:
            meet_type = int(meet_raw or 0)
        except (TypeError, ValueError):
            meet_type = 0
        kind = pick("kind") or ("meet" if meet_type == 1 else "chapter")
        target = cls(
            recruit_id=pick("recruitId", "recruit_id"),
            exam_id=pick("examId", "exam_id"),
            stu_exam_id=pick("studentExamId", "stu_exam_id", "stuExamId", "student_exam_id"),
            school_id=pick("schoolId", "school_id"),
            course_id=pick("courseId", "course_id"),
            name=pick("name", "examName"),
            chapter_id=data.get("chapterId") or data.get("ch") or None,
            deadline=pick("deadline", "examEndTime", "endTime", "endDate"),
            kind=kind,
            meet_course_type=meet_type,
            state=data.get("state"),
        )
        missing = [key for key, value in target.five_tuple().items() if not value]
        if missing:
            raise ZhihuishuExamError(f"作业定位参数缺失：{','.join(missing)}")
        return target


def targets_from_videolist(
    data: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    recruit_id: str = "",
    course_id: str = "",
) -> list[HomeworkTarget]:
    """从学习页``learning/videolist``响应里提取每章挂的「平时测试」目标。

    现网字段：``data.videoChapterDtos[].studentExamDto.examUrl``
    未提交链接为 ``dohomework``，已提交链接为 ``checkHomework``；定位参数相同。
    （``studentExamDto.examId`` 恒 null，真正的 examId 在 examUrl 里）。
    """

    def walk(node: Any, chapter_name: str = "", chapter_id: Any = None) -> Iterable[HomeworkTarget]:
        if isinstance(node, Mapping):
            # 章节自己的名字/ID 必须**先**算出来：studentExamDto 挂在章节节点上，
            # 而不是挂在子节点里；用外层 chapter_name 会拿到空串，名字会退化成 examId。
            own_name = str(node.get("name") or node.get("chapterName") or chapter_name or "")
            own_id = node.get("id") if node.get("id") is not None else chapter_id
            dto = node.get("studentExamDto")
            url = ""
            if isinstance(dto, Mapping):
                url = str(dto.get("examUrl") or "")
            elif isinstance(node.get("examUrl"), str):
                url = str(node.get("examUrl"))
            if url:
                try:
                    target = HomeworkTarget.from_exam_url(
                        ("https:" + url) if url.startswith("//") else url
                    )
                except ZhihuishuExamError:
                    target = None
                if target is not None:
                    if recruit_id:
                        target.recruit_id = str(recruit_id)
                    if course_id:
                        target.course_id = str(course_id)
                    target.name = own_name or target.name
                    target.chapter_id = own_id if own_id is not None else target.chapter_id
                    exam = dto.get("exam") if isinstance(dto, Mapping) else None
                    if isinstance(exam, Mapping):
                        target.deadline = str(exam.get("endDate") or exam.get("reviewEndTime") or "")
                    yield target
            yield from walk(node.get("videoChapterDtos") or [], own_name, own_id)
            yield from walk(node.get("videoLessons") or [], own_name, own_id)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for item in node:
                yield from walk(item, chapter_name, chapter_id)

    found: list[HomeworkTarget] = []
    seen: set[str] = set()
    for target in walk(data):
        key = target.stu_exam_id
        if key in seen:
            continue
        seen.add(key)
        found.append(target)
    return found


def targets_from_homework_list(
    items: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
    *,
    recruit_id: str,
    course_id: str = "",
    school_id: str = "",
    kind: str = "",
) -> list[HomeworkTarget]:
    """把 ``getStudentHomework`` 的 ``studentHomeworkList`` 转成 ``HomeworkTarget``。

    列表项没有 examUrl / schoolId：``id``=stuExamId，``examId``=试卷 id，
    ``courseId`` 在条目里，``schoolId`` / ``recruitId`` 由调用方补齐。
    """
    if items is None:
        rows: list[Any] = []
    elif isinstance(items, Mapping):
        rows = list(items.get("studentHomeworkList") or items.get("list") or [])
    else:
        rows = list(items)

    found: list[HomeworkTarget] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        stu = str(row.get("id") or row.get("studentExamId") or row.get("stuExamId") or "")
        exam = str(row.get("examId") or "")
        if not stu or not exam or stu in seen:
            continue
        try:
            meet_type = int(row.get("meetCourseType") or 0)
        except (TypeError, ValueError):
            meet_type = 0
        row_kind = kind or ("meet" if meet_type == 1 else "chapter")
        cid = str(row.get("courseId") or course_id or "")
        target = HomeworkTarget(
            recruit_id=str(recruit_id),
            exam_id=exam,
            stu_exam_id=stu,
            school_id=str(school_id or ""),
            course_id=cid,
            name=str(row.get("examName") or row.get("name") or exam),
            chapter_id=row.get("chapterId"),
            deadline=str(row.get("endDate") or row.get("deadline") or ""),
            kind=row_kind,
            meet_course_type=meet_type,
            state=row.get("state"),
        )
        if not target.school_id or not target.course_id:
            # five_tuple 完整性由调用方保证；这里先跳过缺关键字段的脏数据
            if not target.school_id or not target.course_id:
                continue
        target.exam_url = exam_page_url(target)
        seen.add(stu)
        found.append(target)
    return found


def merge_homework_targets(*groups: Sequence[HomeworkTarget]) -> list[HomeworkTarget]:
    """按 stuExamId 去重合并；先出现的优先（通常 videolist 在前）。"""
    found: list[HomeworkTarget] = []
    seen: set[str] = set()
    for group in groups:
        for target in group:
            key = str(target.stu_exam_id)
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(target)
    return found


# --------------------------------------------------------------------------- #
# 试卷模型
# --------------------------------------------------------------------------- #
TYPE_LABELS = {1: "single", 2: "multiple", 14: "judge"}
TYPE_NAMES_CN = {
    1: "单选题",
    2: "多选题",
    3: "填空题",
    4: "问答题",
    14: "判断题",
}
JUDGE_TRUE = {"正确", "对", "是", "√", "t", "true", "y", "yes", "a"}
JUDGE_FALSE = {"错误", "错", "否", "×", "x", "f", "false", "n", "no", "b"}


@dataclass
class Option:
    id: str
    label: str
    text: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "text": self.text}


@dataclass
class Question:
    eid: str
    stem: str
    type_id: int = 1
    type_name: str = ""
    options: list[Option] = field(default_factory=list)
    score: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def type_key(self) -> str:
        return TYPE_LABELS.get(self.type_id, "single")

    @property
    def is_choice(self) -> bool:
        return bool(self.options)

    def as_dict(self) -> dict[str, Any]:
        return {
            "eid": self.eid,
            "type": self.type_key,
            "typeName": self.type_name or TYPE_NAMES_CN.get(self.type_id, ""),
            "stem": self.stem,
            "options": [option.as_dict() for option in self.options],
            "score": self.score,
        }


def _strip_html(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def parse_questions(rt: Mapping[str, Any] | None) -> list[Question]:
    """把 ``doHomework`` 响应的 ``rt`` 解析成扁平题目列表。"""
    if not isinstance(rt, Mapping):
        return []
    exam_base = rt.get("examBase")
    if not isinstance(exam_base, Mapping):
        return []
    questions: list[Question] = []
    for part in exam_base.get("workExamParts") or []:
        if not isinstance(part, Mapping):
            continue
        dtos = part.get("questionDtos") or part.get("que") or []
        for raw in dtos:
            if not isinstance(raw, Mapping):
                continue
            qtype = raw.get("questionType")
            type_id = 1
            type_name = ""
            if isinstance(qtype, Mapping):
                try:
                    type_id = int(qtype.get("id") or 1)
                except (TypeError, ValueError):
                    type_id = 1
                type_name = str(qtype.get("name") or "")
            options: list[Option] = []
            for index, option in enumerate(raw.get("questionOptions") or []):
                if not isinstance(option, Mapping):
                    continue
                options.append(
                    Option(
                        id=str(option.get("id") or ""),
                        label=chr(ord("A") + index),
                        text=_strip_html(option.get("content") or option.get("contentText") or ""),
                    )
                )
            questions.append(
                Question(
                    eid=str(raw.get("eid") or ""),
                    stem=_strip_html(raw.get("name") or raw.get("stem") or ""),
                    type_id=type_id,
                    type_name=type_name or TYPE_NAMES_CN.get(type_id, ""),
                    options=options,
                    score=str(raw.get("questionScore") or ""),
                    raw=dict(raw),
                )
            )
    return [question for question in questions if question.eid]


def resolve_option_ids(question: Question, answers: Sequence[str]) -> str:
    """把 AI 给的答案（字母 / 选项 id / 选项原文）换成服务端要的选项 id 串。

    * 单选 / 判断 → ``"564907126"``
    * 多选 → ``"564907140,564907141"``（服务端会升序归一）
    * 没有选项的题（填空 / 问答）→ 原样返回答案文本
    """
    if not question.options:
        values = [str(item).strip() for item in answers if str(item).strip()]
        if not values:
            raise ZhihuishuExamError(f"题目 {question.eid} 的答案为空")
        return ",".join(values) if question.type_id == 2 else values[0]

    by_label = {option.label.upper(): option for option in question.options}
    by_id = {option.id: option for option in question.options}
    by_text = {option.text.strip(): option for option in question.options}

    def pick_one(value: Any) -> str:
        token = str(value if not isinstance(value, Mapping) else (value.get("label") or value.get("id") or ""))
        token = token.strip().strip("。.,，；;、") or ""
        if not token:
            raise ZhihuishuExamError(f"题目 {question.eid} 的答案为空")
        # 1) 选项 id
        if token in by_id:
            return by_id[token].id
        # 2) 显示字母 A/B/C/D（可带 "选项A"/"A." 之类噪声）
        letters = re.findall(r"[AaBbCcDdEeFfGgHh]", token) if len(token) <= 6 else []
        letter = token.strip("()（） .、").upper()
        if letter in by_label:
            return by_label[letter].id
        if len(letters) == 1 and letters[0].upper() in by_label:
            return by_label[letters[0].upper()].id
        # 3) 选项原文
        if token in by_text:
            return by_text[token].id
        # 4) 判断题语义（"正确/错误"、"对/错"、"√/×" 映射到两个选项）
        narrowed = token.lower()
        if len(question.options) == 2:
            wanted = True if narrowed in JUDGE_TRUE else False if narrowed in JUDGE_FALSE else None
            if wanted is not None:
                for option in question.options:
                    text = option.text.strip().lower()
                    truth = True if text in JUDGE_TRUE else False if text in JUDGE_FALSE else None
                    if truth is wanted:
                        return option.id
        raise ZhihuishuExamError(
            f"题目 {question.eid} 的 AI 答案 {token!r} 无法映射到选项"
            f"（可用：{', '.join(option.label for option in question.options)}）"
        )

    picked: list[str] = []
    for item in answers:
        if isinstance(item, str) and "," in item and question.type_id == 2:
            parts = [part for part in re.split(r"[,，;；、\s]+", item) if part]
        elif isinstance(item, str) and len(item.strip()) > 1 and question.type_id == 2 and re.fullmatch(
            r"[A-Ha-h]{2,8}", item.strip()
        ):
            parts = list(item.strip())
        else:
            parts = [item]
        for part in parts:
            option_id = pick_one(part)
            if option_id not in picked:
                picked.append(option_id)
    if not picked:
        raise ZhihuishuExamError(f"题目 {question.eid} 的答案为空")
    return ",".join(picked)


# --------------------------------------------------------------------------- #
# 会话层
# --------------------------------------------------------------------------- #
REQUIRED_COOKIES = ("jt-cas", "o_session_id")
OPTIONAL_COOKIES = ("JSESSIONID", "SERVERID", "acw_tc", "CASLOGC", "locale")


def _iter_cookie_pairs(payload: Any) -> Iterable[tuple[str, str]]:
    """把各种 cookie 载体（requests jar / 列表 / 字典 / 原始头）压平成 (name, value)。"""
    if payload is None:
        return
    if isinstance(payload, str):
        for chunk in payload.split(";"):
            if "=" in chunk:
                name, value = chunk.split("=", 1)
                if name.strip():
                    yield name.strip(), value.strip()
        return
    if isinstance(payload, Mapping):
        nested = payload.get("cookies") if "cookies" in payload else None
        if nested is not None:
            yield from _iter_cookie_pairs(nested)
            return
        for name, value in payload.items():
            if isinstance(value, Mapping):
                # requests cookie jar / 浏览器导出：{"value": ..., "domain": ...}
                if "value" in value:
                    yield str(name), str(value.get("value") or "")
            elif isinstance(value, (list, tuple)):
                continue
            else:
                yield str(name), str(value)
        return
    if isinstance(payload, Iterable):
        for item in payload:
            if isinstance(item, Mapping):
                name = item.get("name")
                if name:
                    yield str(name), str(item.get("value") or "")
            elif getattr(item, "name", None) is not None and hasattr(item, "value"):
                # requests.cookiejar.Cookie / http.cookiejar.Cookie
                yield str(item.name), str(item.value or "")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                yield str(item[0]), str(item[1])


def cookie_header(payload: Any, *, require: bool = True) -> str:
    """拼出 studentexam-api 可用的 Cookie 头；``require`` 时校验网关必需项。"""
    jar: dict[str, str] = {}
    for name, value in _iter_cookie_pairs(payload):
        if value != "" or name not in jar:
            jar[name] = value
    if require:
        missing = [name for name in REQUIRED_COOKIES if not jar.get(name)]
        if missing:
            raise ZhihuishuSessionError(
                "智慧树考试会话缺少 cookie：" + ",".join(missing)
                + "（jt-cas 约 1 天过期，需重新登录或从浏览器重新导出）"
            )
    ordered: list[str] = [name for name in (*REQUIRED_COOKIES, *OPTIONAL_COOKIES) if jar.get(name)]
    ordered += [name for name in jar if name not in ordered and jar[name] != ""]
    return "; ".join(f"{name}={jar[name]}" for name in ordered)


def load_cookie_payload(path: str | os.PathLike[str] | None = None) -> Any:
    """读取环境变量 ``ZHS_COOKIE`` / ``ZHS_COOKIE_FILE``、或 JSON 文件里的 cookie。

    支持的 JSON 形态：``{"cookies": {...}}``、``{name: value}``、
    ``[{name, value, domain}, ...]``（即项目 ``utils.cookie_jar_to_list`` 的产物）。
    """
    raw = os.environ.get("ZHS_COOKIE", "").strip()
    if raw:
        return raw
    candidate = path or os.environ.get("ZHS_COOKIE_FILE") or ""
    if not candidate:
        return None
    file_path = Path(candidate)
    if not file_path.is_file():
        raise ZhihuishuSessionError(f"cookie 文件不存在：{file_path}")
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ZhihuishuSessionError(f"cookie 文件解析失败：{exc}") from exc


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or "<head" in head


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
# lookHomework 的 msg 是**数字业务码**，不是会话标记（活体打表 work/zhs_look_msg2_matrix.js）：
#   msg=1        recruitId 不属于当前登录账号（同 cookie 全表都会变成 1）
#   msg=2        该卷存在，但不属于 lookHomework 可查看的类型（见面课/视频课作业）
#   msg=传入参数异常  examId / stuExamId 对不上或缺失
# 会话失效的真实信号只有：302 跳 passport、401、200 但回 HTML、cookie 缺 o_session_id。
BUSINESS_MSG_HINTS = {
    "1": "报名号（recruitId）不属于当前登录账号：cookie 里的登录身份与这条课程/作业对不上",
    "2": "该卷存在，但 lookHomework 不开放这类作业（见面课/视频课作业），改用 doHomework 取题",
    "3": "作业已提交或不允许再次查看",
}
BUSINESS_MISSING_PARAM = "传入参数异常"


def business_reason(endpoint: str, data: Mapping[str, Any]) -> str:
    """把网关的 status/msg 翻成人能看懂的原因（不做任何会话判定）。"""
    raw = data.get("msg")
    message = raw if raw else (data.get("message") or data)
    token = str(raw if raw is not None else "").strip()
    if token == BUSINESS_MISSING_PARAM:
        return ("传入参数异常：examId / stuExamId / recruitId 有误或缺失"
                "（examId 与 stuExamId 必须来自同一份作业）")
    hint = BUSINESS_MSG_HINTS.get(token)
    if not hint:
        return f"{message}"
    text = f"msg={token}，{hint}"
    if endpoint.strip("/") == EP_LOOK_HOMEWORK.strip("/") and token in ("1", "2"):
        text += "（这与登录会话无关，重建会话无效）"
    return text


def current_account(cookies: Mapping[str, Any]) -> str:
    """从 jt-cas / CASLOGC 读出当前 cookie 到底是哪个账号，用于排查 msg=1。"""
    claims: dict[str, Any] = {}
    try:
        segment = str(cookies.get("jt-cas") or "").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (IndexError, ValueError, TypeError):
        claims = {}
    uid = str(claims.get("uid") or claims.get("sub") or "").strip() if isinstance(claims, dict) else ""
    name = ""
    try:
        profile = json.loads(unquote(str(cookies.get("CASLOGC") or "")))
        if isinstance(profile, dict):
            name = str(profile.get("realName") or "").strip()
            uid = uid or str(profile.get("userId") or "").strip()
    except (ValueError, TypeError):
        pass
    if not uid and not name:
        return ""
    return f"当前 cookie 账号：{name + ' ' if name else ''}uid={uid}"



SUBMIT_FAIL_HINTS = (
    "不能", "已提交", "禁止", "拒绝", "失败", "异常", "错误", "超时",
    "未开始", "不在", "过期", "结束",
)


def ensure_submit_ok(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """校验交卷响应，确认服务端真的接受了这次提交。

    实测两种 200 响应，只有 rt 能区分：
      成功 studentexam ``{"status":"200","rt":{"msg":"提交成功","score":"10","statu":"0"}}``
      失败 studentexam ``{"status":"200","rt":{"msg":"不能重复提交","statu":"-1"}}``
      成功 taurus      ``{"status":"200","msg":"交卷成功","rt":0}``（rt 不是对象）
    旧代码不校验，于是把「不能重复提交」当成「已提交」显示给用户。
    """
    if not isinstance(data, Mapping):
        raise ZhihuishuExamError(f"交卷响应不是对象，无法确认提交结果：{str(data)[:200]}")
    rt = data.get("rt")
    outer_msg = str(data.get("msg") or "")
    if not isinstance(rt, Mapping):
        # taurus 微服务把结果放在外层 msg，rt 只回 0
        if ("交卷成功" in outer_msg or "提交成功" in outer_msg) and not any(
            h in outer_msg for h in SUBMIT_FAIL_HINTS
        ):
            return {"msg": outer_msg}
        raise ZhihuishuExamError(
            f"交卷响应缺少 rt，无法确认是否真的提交成功（msg={outer_msg or '空'}）："
            "这次提交没有生效，请到网页端确认卷面状态"
        )
    statu = "" if rt.get("statu") is None else str(rt.get("statu"))
    msg = str(rt.get("msg") or "")
    ok = statu in ("0", "1") or (not statu and "成功" in msg)
    bad = any(hint in msg for hint in SUBMIT_FAIL_HINTS) and "成功" not in msg
    if not ok or bad:
        raise ZhihuishuExamError(
            f"交卷被服务端拒绝（statu={statu or '空'}，msg={msg or '空'}）："
            "这次提交没有生效，请勿当作已完成；去网页端确认卷面状态后再重试"
        )
    return dict(rt)


def _as_number(value: Any) -> float | None:
    """把接口里的 "70" / 70 / None 统一成 float；认不出就返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_number(value: Any) -> float | None:
    """把接口里的 "70" / 70 / None 统一成 float；认不出就返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class ZhihuishuExamClient:
    """studentexam-api 客户端：取卷 / 读回 / 逐题保存 / 交卷。"""

    def __init__(
        self,
        cookies: Any = None,
        *,
        cookie_file: str | os.PathLike[str] | None = None,
        proxies: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        user_agent: str = DEFAULT_UA,
        session: requests.Session | None = None,
        refresh_session: Callable[[], Any] | None = None,
    ) -> None:
        payload = cookies if cookies is not None else load_cookie_payload(cookie_file)
        if payload is None:
            raise ZhihuishuSessionError("缺少智慧树考试会话 cookie（jt-cas / o_session_id）")
        self.cookie_header = cookie_header(payload)
        self.timeout = float(timeout)
        self.proxies = {k: v for k, v in (proxies or {}).items() if v}
        self.session = session or requests.Session()
        self.refresh_session = refresh_session
        self._session_refresh_attempted = False
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cookie": self.cookie_header,
                "Origin": ORIGIN,
                "Referer": ORIGIN + "/",
                "User-Agent": user_agent,
            }
        )
        self.last_wire: dict[str, Any] = {}

    # ---------------- 底层传输 ---------------- #
    def _decode(
        self, response: requests.Response, label: str, api_path: str = "",
    ) -> dict[str, Any]:
        text = response.text or ""
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if response.status_code == 401 or (isinstance(data, Mapping) and str(data.get("code")) == "401"):
            raise ZhihuishuSessionError(
                "智慧树考试网关拒绝（401 Forbidden）：jt-cas / o_session_id 缺失或已过期，请重新登录"
            )
        if data is None:
            if _looks_like_html(text):
                raise ZhihuishuSessionError(
                    "智慧树考试网关返回了 HTML 首页（考试应用会话无效或未建立）："
                    f"{label} 的响应是 HTML 而不是 JSON。最常见原因是请求被 302 到 "
                    "passport.zhihuishu.com 后被自动跟随，即 o_session_id 已过期或不是 "
                    ".zhihuishu.com 域下的最新值（jt-cas 通常仍然有效；实测 jt-cas+过期 "
                    "o_session_id → 302，jt-cas+有效 o_session_id → 200 JSON）。"
                    "处理：从浏览器重新导出 cookies.json。"
                )
            raise ZhihuishuExamError(f"{label} 返回非 JSON 内容：{text[:160]}")
        if not isinstance(data, Mapping):
            raise ZhihuishuExamError(f"{label} 返回了意外结构：{str(data)[:160]}")
        status = str(data.get("status") or "")
        endpoint = api_path.split("?", 1)[0].strip("/")
        if status and status not in ("200", "0"):
            reason = business_reason(endpoint, data)
            if str(data.get("msg") or "").strip() == "1":
                who = current_account(dict(_iter_cookie_pairs(
                    getattr(self, "cookie_header", ""))))
                if who:
                    reason += f"（{who}；换账号请重新导出 cookies.json）"
            raise ZhihuishuExamError(
                f"{label} 业务失败（status={status}）：{reason}"
            )
        if "rt" not in data and not data.get("msg") and "code" in data:
            raise ZhihuishuExamError(f"{label} 业务失败：{data}")
        self.last_wire = {
            "label": label,
            "httpStatus": response.status_code,
            "bytes": len(response.content or b""),
            "json": data,
        }
        return dict(data)

    def _raise_on_sso_redirect(self, response, label: str) -> None:
        """网关回 302 → passport 就等价于「考试会话过期」，比跟随成 HTML 首页好判得多。

        实测（本轮 A/B）：
          * jt-cas + **过期** o_session_id → 302 Location=passport.zhihuishu.com
          * jt-cas + **有效** o_session_id → 200 application/json
        requests 默认跟随 302，于是把 passport 登录页 HTML 当成业务响应，触发误导性的
        「返回了 HTML 首页」。这里改为不跟随、直接判 302。
        """
        status = getattr(response, "status_code", 0)
        if status in (301, 302, 303, 307, 308):
            location = ""
            try:
                location = (getattr(response, "headers", {}) or {}).get("Location") or ""
            except Exception:  # noqa: BLE001 测试桩可能没有 headers
                location = ""
            if "passport.zhihuishu.com" in location:
                raise ZhihuishuSessionError(
                    f"{label}：考试网关把请求重定向到了登录页（HTTP {status} → passport.zhihuishu.com）。jt-cas 仍在有效期内，但考试子系统会话 o_session_id 已失效。"
                    "处理：从浏览器重新导出 cookies.json（取 .zhihuishu.com 域下最新的 "
                    "o_session_id），或在桌面端重新登录智慧树后再试。"
                )
            raise ZhihuishuSessionError(
                f"{label}：考试网关返回重定向（HTTP {status} → {location[:80]}），会话无效"
            )

    def _request(self, method: str, api_path: str, label: str, *, base: str | None = None, **kwargs):
        url = (base or GATEWAY) + api_path

        def send():
            # 不跟随重定向：302 → passport 才是「会话过期」的真实信号
            try:
                return getattr(self.session, method)(
                    url, timeout=self.timeout, proxies=self.proxies,
                    allow_redirects=False, **kwargs
                )
            except TypeError:  # 测试桩/替代 session 可能不接受 allow_redirects
                return getattr(self.session, method)(
                    url, timeout=self.timeout, proxies=self.proxies, **kwargs
                )

        response = send()
        self._raise_on_sso_redirect(response, label)
        try:
            return self._decode(response, label, api_path)
        except ZhihuishuSessionError:
            endpoint = api_path.split("?", 1)[0].strip("/")
            allowed = {path.strip("/") for path in READ_ONLY_ENDPOINTS}
            if (endpoint not in allowed or self.refresh_session is None
                    or self._session_refresh_attempted):
                raise
            self._session_refresh_attempted = True
            refreshed = self.refresh_session()
            self.cookie_header = cookie_header(refreshed)
            self.session.headers["Cookie"] = self.cookie_header
            response = send()
            self._raise_on_sso_redirect(response, label)
            return self._decode(response, label, api_path)

    def post(self, api_path: str, params: Mapping[str, Any], *, label: str = "") -> dict[str, Any]:
        body = {"secretStr": secret_str(params)}
        return self._request(
            "post", api_path, label or api_path,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    @staticmethod
    def slider_blocked(data: Mapping[str, Any] | None) -> bool:
        """是否是「滑块验证未通过，不能答题」这类可被 taurus 旁路绕过的风控拒绝。"""
        if not isinstance(data, Mapping):
            return False
        status = str(data.get("status"))
        if status not in ("-1", "403"):
            return False
        msg = str(data.get("msg") or "")
        return ("滑块" in msg) or ("验证未通过" in msg)

    @staticmethod
    def slider_error(exc: BaseException) -> bool:
        """``_decode`` 抛出的业务失败里，是否属于「滑块验证未通过」这类可旁路的风控。"""
        text = str(exc or "")
        return ("滑块" in text) or ("验证未通过" in text)

    def post_taurus(self, api_path: str, params: Mapping[str, Any], *, label: str = "") -> dict[str, Any]:
        """同 body 走 taurusExam（绕过 studentExam 侧风控）。"""
        return self._request(
            "post", api_path, (label or api_path) + "(taurus)",
            base=TAURUS_GATEWAY,
            data={"secretStr": secret_str(params)},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def get(self, api_path: str, params: Mapping[str, Any], *, label: str = "") -> dict[str, Any]:
        payload = dict(params)
        payload.setdefault("dateFormate", int(time.time() * 1000))
        query = urlencode(
            {"secretStr": secret_str(params), "dateFormate": payload["dateFormate"]}
        )
        return self._request("get", api_path + "?" + query, label or api_path)

    # ---------------- 只读业务 ---------------- #
    def homework_list(
        self,
        recruit_id: str,
        *,
        course_id: str = "",
        flag: str = "2",
        page_size: int = 100,
        page_num: int = 0,
    ) -> dict[str, Any]:
        """``/student/getStudentHomework``。

        ``flag`` 口径（已验证）：
          * ``"1"`` = 见面课测验
          * ``"2"`` = 作业 / 章节平时测试
          * 缺省或非 1/2 → ``studentHomeworkList`` 空且 ``rt.next=-1``（假成功）
        ``courseId`` 必填，否则同样空列表。
        """
        payload: dict[str, Any] = {
            "recruitId": str(recruit_id),
            "flag": str(flag),
            "pageSize": int(page_size),
            "pageNum": int(page_num),
        }
        if course_id not in (None, ""):
            payload["courseId"] = str(course_id)
        data = self.post(EP_HOMEWORK_LIST, payload, label=f"作业列表 flag={flag}")
        return data.get("rt") or {}

    def course_name(self, course_id: str, recruit_id: str) -> str:
        data = self.post(
            EP_COURSE_NAME,
            {"courseId": str(course_id), "recruitId": str(recruit_id)},
            label="课程名",
        )
        return str(data.get("rt") or "")

    def recruit(self, recruit_id: str) -> dict[str, Any]:
        data = self.post(EP_SINGLE_RECRUIT, {"recruitId": str(recruit_id)}, label="学分课信息")
        return data.get("rt") or {}

    def check_lock(self, target: HomeworkTarget) -> dict[str, Any]:
        data = self.post(EP_CHECK_LOCK, target.answer_tuple(), label="作业锁状态")
        return data.get("rt") or {}

    def has_answer(self, target: HomeworkTarget) -> bool:
        data = self.post(EP_HAS_ANSWER, target.answer_tuple(), label="是否已作答")
        return str(data.get("rt") or "0") not in ("0", "", "None", "False", "false")

    # ---------------- 重做（已交卷的卷子重新拿到可作答的卷面） ---------------- #
    def homework_status(self, target: HomeworkTarget) -> dict[str, Any]:
        """读一份卷子的「是否已交卷 / 卷面分」。纯只读。

        实测口径（work/probe_redo_state2.json，2026-11-04）：
          * ``lookHomework`` 的 ``rt.score`` = 卷面分（已判分才有值；如 70/80），
            ``rt.examBase.totalScore`` = 卷面总分（如 110/140）；
            但对见面课 / 视频课作业它回 status=-1/msg=2，拿不到分。
          * ``doHomework`` 的 ``rt.state == "4"`` 表示已交卷（"1" = 未交卷），
            但它不回分（score 恒 null）。

        ``source`` 告诉你分数是从哪条链路来的：``lookHomework`` 才可信；
        ``doHomework`` 只说明「已交卷」，分数未知。
        """
        try:
            look = self.post(EP_LOOK_HOMEWORK, target.five_tuple(), label="卷面状态")
        except ZhihuishuSessionError:
            raise
        except ZhihuishuExamError:
            pass
        else:
            rt = look.get("rt") or {}
            base = rt.get("examBase") or {}
            score = _as_number(rt.get("score"))
            return {
                "submitted": score is not None,
                "source": "lookHomework",
                "score": score,
                "totalScore": _as_number(base.get("totalScore")),
                "state": rt.get("state"),
                "examName": str(base.get("name") or ""),
            }
        paper = self.post(EP_PAPER, target.five_tuple(), label="卷面状态")
        rt = paper.get("rt") or {}
        base = rt.get("examBase") or {}
        state = str(rt.get("state") or "")
        return {
            "submitted": state == "4",
            "source": "doHomework",
            "score": _as_number(rt.get("score")),
            "totalScore": _as_number(base.get("totalScore")),
            "state": rt.get("state"),
            "examName": str(base.get("name") or ""),
        }

    def redo_used(self, target: HomeworkTarget) -> int:
        """已用掉的重做次数（redoNum.rt），剩余 = REDO_LIMIT - 返回值。"""
        data = self.post(
            EP_REDO_NUM,
            {
                "recruitId": str(target.recruit_id),
                "examId": str(target.exam_id),
                "stuExamId": str(target.stu_exam_id),
            },
            label="重做次数",
        )
        number = _as_number(data.get("rt"))
        return int(number) if number is not None else 0

    def redo_available(self, target: HomeworkTarget) -> int:
        """还能重做几次（<=0 表示用完）。"""
        return max(0, REDO_LIMIT - self.redo_used(target))

    def apply_redo(self, target: HomeworkTarget, *, confirm: bool = False) -> dict[str, Any]:
        """申请重做 —— 不可逆写操作：消耗 1 次机会，且「重做的分数就是这次作业的分数」
        （覆盖原分，不是取最高分）。

        前端链路（scripts/zhs_chunk9_examList.js 的 applyRework，逐行核对）：
          ``/exam/getIsObject {examId}`` → ``rt == 1`` 才走「直接申请」，
                                         否则前端弹二维码让老师/助教确认，这里直接拒绝；
          ``/student/saveCourseTran {recruitId, examId, description:"", courseId}``
                                         → ``rt == 1`` = 申请成功。
        """
        if not confirm:
            raise ZhihuishuExamError(
                "申请重做会消耗 1 次机会且覆盖原分数，需要显式确认（confirm=True）"
            )
        gate = self.post(EP_GET_IS_OBJECT, {"examId": str(target.exam_id)}, label="重做方式")
        if str(gate.get("rt")) != "1":
            raise ZhihuishuExamError(
                f"该卷不能直接申请重做（getIsObject.rt={gate.get('rt')}：前端此时会走二维码申请）"
            )
        data = self.post(
            EP_SAVE_COURSE_TRAN,
            {
                "recruitId": str(target.recruit_id),
                "examId": str(target.exam_id),
                "description": "",
                "courseId": str(target.course_id),
            },
            label="申请重做",
        )
        if str(data.get("rt")) != "1":
            raise ZhihuishuExamError(f"申请重做失败：{data}")
        return data

    def paper(self, target: HomeworkTarget) -> dict[str, Any]:
        """取卷，返回 ``{rt, questions, examBase}``。"""
        data = self.post(EP_PAPER, target.five_tuple(), label="取卷")
        rt = data.get("rt") or {}
        questions = parse_questions(rt)
        if not questions:
            raise ZhihuishuExamError(
                "取卷成功但没有题目：可能尚未开始 / 已交卷 / 不在作答窗口（rt.state="
                f"{rt.get('state')}）"
            )
        return {
            "rt": rt,
            "questions": questions,
            "examBase": rt.get("examBase") or {},
            "deadline": str(rt.get("examEndTime") or ""),
            "state": rt.get("state"),
        }

    def readback(self, target: HomeworkTarget, questions: Sequence[Question] | None = None) -> dict[str, Any]:
        """读回服务端已保存的答案：``{eid: {answer, ...}}``。"""
        if questions is None:
            questions = self.paper(target)["questions"]
        eids = [question.eid for question in questions]
        if not eids:
            return {}
        params = {**target.answer_tuple(), "questionIds": ",".join(eids)}
        data = self.post(EP_READBACK, params, label="读回答案")
        rt = data.get("rt")
        return rt if isinstance(rt, Mapping) else {}

    # ---------------- 写入业务 ---------------- #
    def save_answer(
        self,
        target: HomeworkTarget,
        question: Question,
        answer: str,
        *,
        question_type: int | None = None,
    ) -> dict[str, Any]:
        """单题保存（服务端禁止批量）。``answer`` 必须是选项 id 串。"""
        if "," in answer and question.type_id != 2:
            raise ZhihuishuExamError(f"题目 {question.eid} 是单选/判断题，不能提交多个选项")
        payload = {
            "examId": str(target.exam_id),
            "recruitId": str(target.recruit_id),
            "stuExamId": str(target.stu_exam_id),
            "eid": question.eid,
            "answer": str(answer),
            "schoolId": str(target.school_id),
            "deviceId": "",
            "examType": "",
            "fromType": 3,
            "dataIds": "",
            "questionType": int(question_type if question_type is not None else question.type_id),
        }
        body = {"stuExamAnswer": json.dumps([payload], ensure_ascii=False), "recruitId": str(target.recruit_id)}
        try:
            data = self.post(EP_SAVE_ANSWER, body, label="保存答案")
        except ZhihuishuExamError as exc:
            if not self.slider_error(exc):
                raise
            data = self.post_taurus(EP_SAVE_ANSWER, body, label="保存答案")
        else:
            if self.slider_blocked(data):
                data = self.post_taurus(EP_SAVE_ANSWER, body, label="保存答案")
        rt = data.get("rt") or {}
        if isinstance(rt, Mapping) and str(rt.get("statu")) not in ("1", "None"):
            raise ZhihuishuExamError(f"题目 {question.eid} 保存被拒：{rt}")
        return data

    def temporary_save(self, target: HomeworkTarget, achieve_count: int = 0) -> dict[str, Any]:
        return self.post(
            EP_TEMPORARY_SAVE,
            {
                "recruitId": str(target.recruit_id),
                "examId": str(target.exam_id),
                "stuExamId": str(target.stu_exam_id),
                "achieveCount": int(achieve_count),
            },
            label="暂存",
        )

    def submit(self, target: HomeworkTarget, achieve_count: int = 0, *, confirm: bool = False) -> dict[str, Any]:
        """交卷 —— 不可逆（直接判分），必须显式 ``confirm=True``。"""
        if not confirm:
            raise ZhihuishuExamError("交卷不可逆（会立即判分），需要显式确认（confirm=True）")
        body = {
            "recruitId": str(target.recruit_id),
            "examId": str(target.exam_id),
            "stuExamId": str(target.stu_exam_id),
            "achieveCount": int(achieve_count),
        }
        try:
            data = self.post(EP_SUBMIT, body, label="交卷")
        except ZhihuishuExamError as exc:
            if not self.slider_error(exc):
                raise
            data = self.post_taurus(EP_SUBMIT, body, label="交卷")
        else:
            if self.slider_blocked(data):
                data = self.post_taurus(EP_SUBMIT, body, label="交卷")
        # HTTP 200 + status="200" 不等于交卷成功：必须看 rt.statu。
        # 实测失败样例 {"rt":{"msg":"不能重复提交","statu":"-1"}}。
        ensure_submit_ok(data)
        return data


# --------------------------------------------------------------------------- #
# AI 作答
# --------------------------------------------------------------------------- #
def openai_answerer(ai_config: Mapping[str, Any], *, on_retry: Callable[[str], None] | None = None):
    """复用学习通那套 OpenAI 兼容答题器（同一 prompt / 解析 / 退避重试）。"""
    from app.chaoxing_exam import ExamAnswerClient

    client = ExamAnswerClient(
        api_base=str(ai_config.get("api_base") or ""),
        api_key=str(ai_config.get("api_key") or ""),
        model_name=str(ai_config.get("model_name") or ""),
        timeout=float(ai_config.get("timeout") or 45.0),
        max_retries=int(ai_config.get("api_retries") or 4),
        retry_backoff=float(ai_config.get("api_retry_backoff") or 1.5),
        on_retry=on_retry,
    )

    def answer(question: Mapping[str, Any]) -> list[str]:
        return client.answer(dict(question))

    return answer


def _cache_key(target: HomeworkTarget, question: Question) -> str:
    digest = hashlib.sha1(f"{question.type_id}|{question.stem}".encode("utf-8")).hexdigest()[:20]
    return f"{target.exam_id}:{digest}"


class AnswerCache:
    """按题干哈希缓存答案，重跑同一份卷子不再重复调 AI。"""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else None
        self.data: dict[str, dict[str, Any]] = {}
        if self.path and self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = {str(k): v for k, v in loaded.items() if isinstance(v, dict)}
            except (OSError, ValueError):
                self.data = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.data.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.data[key] = value
        self.flush()

    def flush(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 执行器
# --------------------------------------------------------------------------- #
@dataclass
class HomeworkResult:
    target: HomeworkTarget
    exam_name: str = ""
    saved: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    ai_only: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    submitted: bool = False
    submit_response: dict[str, Any] = field(default_factory=dict)
    readback: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    submit_confirmation_denied: bool = False
    redo: dict[str, Any] = field(default_factory=dict)

    @property
    def achieved(self) -> int:
        return len(self.saved) + len(self.skipped)

    def as_dict(self) -> dict[str, Any]:
        return {
            "examName": self.exam_name,
            "target": self.target.five_tuple(),
            "saved": self.saved,
            "skipped": self.skipped,
            "aiOnly": self.ai_only,
            "pending": self.pending,
            "failed": self.failed,
            "submitted": self.submitted,
            "submitMsg": str(self.submit_response.get("msg") or ""),
            "submitScore": str(self.submit_response.get("score") or ""),
            "submitResponse": self.submit_response,
            "achieved": self.achieved,
            "cancelled": self.cancelled,
            "submitConfirmationDenied": self.submit_confirmation_denied,
            "redo": dict(self.redo),
        }


def _event(message: str, level: str = "info", current: int | None = None, total: int | None = None):
    try:
        from app.models import ProgressEvent

        return ProgressEvent(message, level=level, current=current, total=total)
    except Exception:  # pragma: no cover - standalone 使用时退化
        return {"message": message, "level": level, "current": current, "total": total}


class HomeworkRunner:
    """一场作业的完整流程：取卷 → 读回 → 逐题作答保存 → 校验 →（可选）交卷。

    不解密、不签名、不启动浏览器；只吃 ``ZhihuishuExamClient`` 的 HttpSession。
    """

    def __init__(
        self,
        client: ZhihuishuExamClient,
        *,
        answerer: Callable[[Mapping[str, Any]], Sequence[str]] | None = None,
        verified_answerer: Callable[[Question], Sequence[str] | None] | None = None,
        answer_cache: AnswerCache | str | os.PathLike[str] | None = None,
        delay: float = 0.8,
        only_missing: bool = True,
        overwrite_existing_with_verified: bool = True,
        auto_redo: bool = False,
        redo_when: str = "not_full",
        redo_max: int = REDO_LIMIT,
        confirm_ai_only_submit: Callable[[Sequence[str]], bool] | None = None,
        progress: Callable[[Any], None] | None = None,
        cancel_event: Event | None = None,
        max_retries: int = 2,
    ) -> None:
        self.client = client
        self.answerer = answerer
        self.verified_answerer = verified_answerer
        if isinstance(answer_cache, AnswerCache) or answer_cache is None:
            self.cache = answer_cache
        else:
            self.cache = AnswerCache(answer_cache)
        self.delay = max(0.0, float(delay))
        self.only_missing = bool(only_missing)
        self.overwrite_existing_with_verified = bool(overwrite_existing_with_verified)
        self.auto_redo = bool(auto_redo)
        self.redo_when = str(redo_when or "not_full").strip().lower()
        self.redo_max = max(0, int(redo_max))
        self.confirm_ai_only_submit = confirm_ai_only_submit
        self.progress = progress
        self.cancel_event = cancel_event
        self.max_retries = max(0, int(max_retries))

    def _say(self, message: str, level: str = "info", current: int | None = None, total: int | None = None) -> None:
        if self.progress:
            try:
                self.progress(_event(message, level=level, current=current, total=total))
            except Exception:
                pass

    def _cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def run(self, target: HomeworkTarget, *, submit: bool = False) -> HomeworkResult:
        result = HomeworkResult(target=target)
        if self.auto_redo:
            # 开刷前先把「已交卷但没满分」的卷子重做掉，否则后面写的答案会被服务端拒绝
            self._maybe_redo(target, result)
        self._say(f"读取作业：{target.describe()}", level="info", current=0, total=0)
        paper = self.client.paper(target)
        exam_base = paper.get("examBase") or {}
        result.exam_name = str(exam_base.get("name") or target.name or "")
        questions: list[Question] = list(paper["questions"])
        deadline = paper.get("deadline") or ""
        self._say(
            f"《{result.exam_name}》共 {len(questions)} 题"
            f"（{exam_base.get('totalScore') or '?'} 分）"
            + (f"，截止 {deadline}" if deadline else "")
        )

        existing: dict[str, Any] = {}
        if self.only_missing:
            try:
                existing = self.client.readback(target, questions)
                if existing:
                    self._say(f"服务端已有 {len(existing)} 题答案，仅补未作答的题")
            except ZhihuishuExamError as exc:
                self._say(f"读回已存答案失败，将全部重答：{exc}", level="warning")

        total = len(questions)
        for index, question in enumerate(questions, start=1):
            if self._cancelled():
                result.cancelled = True
                self._say("已取消", level="warning")
                break
            saved = existing.get(question.eid) or {}
            saved_answer = str((saved or {}).get("answer") or "").strip()
            try:
                verified = self.verified_answerer(question) if self.verified_answerer else None
                verified_answer = resolve_option_ids(question, verified) if verified else ""
                if saved_answer:
                    if self.overwrite_existing_with_verified and verified_answer:
                        if not self._same_answer(question, verified_answer, saved_answer):
                            self.client.save_answer(target, question, verified_answer)
                            result.saved[question.eid] = verified_answer
                            self._say(
                                f"[{index}/{total}] 用答案图覆盖旧答案：{question.stem[:24]}",
                                current=index,
                                total=total,
                            )
                            continue
                        result.skipped[question.eid] = saved_answer
                        self._say(
                            f"[{index}/{total}] 已作答且与答案图一致，跳过：{question.stem[:24]}",
                            current=index,
                            total=total,
                        )
                        continue
                    result.skipped[question.eid] = saved_answer
                    self._say(f"[{index}/{total}] 已作答，跳过：{question.stem[:24]}", current=index, total=total)
                    continue
                cache_key = _cache_key(target, question)
                cached = self.cache.get(cache_key) if self.cache else None
                if (self.answerer is None and verified is None
                        and not (cached and cached.get("answer"))):
                    result.pending.append(question.eid)
                    self._say(
                        f"[{index}/{total}] 未配置 AI，人工作答：{question.stem[:40]}",
                        level="warning",
                        current=index,
                        total=total,
                    )
                    continue
                answer = self._answer_one(target, question, verified=verified)
                if answer:
                    result.saved[question.eid] = answer
                    if verified is None:
                        result.ai_only.append(question.eid)
                    self._say(f"[{index}/{total}] 已保存：{question.stem[:24]}", current=index, total=total)
                else:
                    result.pending.append(question.eid)
                    self._say(f"[{index}/{total}] 题库未命中且未配置 AI：{question.stem[:40]}", level="warning")
            except ZhihuishuExamError as exc:
                result.failed[question.eid] = str(exc)
                result.pending.append(question.eid)
                self._say(f"[{index}/{total}] 失败：{exc}", level="error", current=index, total=total)

        # 校验：以服务端读回为准
        try:
            result.readback = self.client.readback(target, questions) or {}
        except ZhihuishuExamError as exc:
            self._say(f"校验读回失败：{exc}", level="warning")

        if submit and not result.cancelled:
            if result.pending:
                self._say(
                    f"还有 {len(result.pending)} 题没有答案，已跳过交卷（避免提交空白卷）",
                    level="warning",
                )
            elif result.ai_only and not self._confirm_ai_only_submit(result):
                if self._cancelled():
                    result.cancelled = True
                    self._say("交卷确认已取消，未交卷", level="warning")
                else:
                    result.submit_confirmation_denied = True
                    self._say(
                        f"有 {len(result.ai_only)} 道题未命中答案图或已验证题库，未获得交卷确认；答案已保存但未交卷",
                        level="warning",
                    )
            else:
                achieve = len(result.readback) if result.readback else result.achieved
                self._say(f"提交作业（achieveCount={achieve}）", level="warning")
                try:
                    submitted = self.client.submit(target, achieve_count=achieve, confirm=True)
                except ZhihuishuExamError as exc:
                    # 交卷失败绝不能标记 submitted：桌面端会显示「已提交」骗人
                    result.failed["__submit__"] = str(exc)
                    self._say(f"交卷失败，这次提交没有生效：{exc}", level="error")
                else:
                    result.submit_response = dict((submitted or {}).get("rt") or {})
                    result.submitted = True
                    score = str(result.submit_response.get("score") or "")
                    self._say(
                        f"作业已提交，服务端回分 {score} 分" if score
                        else "作业已提交（服务端未回分，请到网页端确认卷面）",
                        level="success",
                    )
        return result

    def _maybe_redo(self, target: HomeworkTarget, result: HomeworkResult) -> None:
        """开刷前判断这张卷是不是「已交卷但没满分」，是就自动消耗 1 次重做机会换新卷。

        判据全部来自实测（work/probe_redo_state2.json）：
          * 已交卷 ⇔ lookHomework.rt.score 有值；拿不到分时退回 doHomework.rt.state == 4
          * 已满分 ⇔ score >= examBase.totalScore（满足就不浪费机会）
          * 可重做 ⇔ REDO_LIMIT - redoNum.rt > 0
        任何一步失败都只报警告、按原卷继续 —— 绝不能因为重做失败而中断整门课。
        """
        session_error = ZhihuishuSessionError
        status_fn = getattr(self.client, "homework_status", None)
        used_fn = getattr(self.client, "redo_used", None)
        apply_fn = getattr(self.client, "apply_redo", None)
        if status_fn is None or used_fn is None or apply_fn is None:
            # 只实现了最小接口的客户端（测试替身 / 后续精简版）没有重做能力：
            # 静默跳过，绝不能让自动重做把整门课带崩。
            result.redo = {"checked": False, "applied": False, "error": "当前客户端不支持重做接口"}
            self._say("当前客户端不支持重做接口，跳过自动重做", level="warning")
            return
        try:
            status = status_fn(target)
        except session_error:
            raise
        except ZhihuishuExamError as exc:
            result.redo = {"checked": False, "applied": False, "error": str(exc)}
            self._say(f"读取卷面状态失败，跳过自动重做：{exc}", level="warning")
            return

        info: dict[str, Any] = {
            "checked": True,
            "submitted": bool(status.get("submitted")),
            "score": status.get("score"),
            "totalScore": status.get("totalScore"),
            "source": status.get("source"),
            "when": self.redo_when,
            "applied": False,
        }
        result.redo = info
        if not info["submitted"]:
            self._say("该卷没有交卷记录，不需要申请重做")
            return

        score, total = status.get("score"), status.get("totalScore")
        full = False
        if score is not None and total:
            try:
                full = float(score) >= float(total)
            except (TypeError, ValueError):
                full = False
        info["full"] = full
        if full and self.redo_when != "always":
            self._say(f"该卷已满分（{score}/{total}），不浪费重做机会")
            return
        if self.redo_when == "off":
            self._say("已关闭自动重做，按原卷继续", level="warning")
            return
        if score is None and self.redo_when != "always":
            # 已交卷但读不到卷面分（见面课/视频课作业 lookHomework 不开放分数）：
            # 无法判断是否满分，贸然重做会把可能已满分的卷子覆盖掉 —— 默认不动。
            info["skippedReason"] = "score_unknown"
            self._say(
                "该卷已交卷但拿不到卷面分，无法判断是否满分，已跳过自动重做（需要强制重做请把策略设为 always）",
                level="warning",
            )
            return

        try:
            used = used_fn(target)
        except session_error:
            raise
        except ZhihuishuExamError as exc:
            self._say(f"查询重做次数失败，跳过自动重做：{exc}", level="warning")
            return
        remain = max(0, self.redo_max - used)
        info.update({"usedBefore": used, "remainBefore": remain})
        if remain <= 0:
            self._say(
                f"重做次数已用完（上限 {self.redo_max} 次），按原卷继续", level="warning"
            )
            return

        if score is None:
            head = "分数未知（该卷不开放查看分数）"
        else:
            head = f"{score}/{total if total else '?'} 分，未满分"
        self._say(
            f"该卷已交卷（{head}），剩余 {remain} 次重做机会 → 自动申请重做",
            level="warning",
        )
        try:
            apply_fn(target, confirm=True)
        except session_error:
            raise
        except ZhihuishuExamError as exc:
            self._say(f"申请重做失败，按原卷继续：{exc}", level="error")
            return
        info.update({"applied": True, "usedAfter": used + 1, "remainAfter": remain - 1})
        self._say(
            f"已申请重做（本次消耗 1 次，剩余 {remain - 1} 次）：试卷已重置，重新取卷",
            level="success",
        )
        if self.delay:
            time.sleep(max(1.0, self.delay))

    @staticmethod
    def _same_answer(question: Question, left: str, right: str) -> bool:
        if question.type_id == 2:
            return {part.strip() for part in left.split(",") if part.strip()} == {
                part.strip() for part in right.split(",") if part.strip()
            }
        return left.strip() == right.strip()

    def _confirm_ai_only_submit(self, result: HomeworkResult) -> bool:
        if self.confirm_ai_only_submit is None:
            return False
        try:
            return bool(self.confirm_ai_only_submit(tuple(result.ai_only)))
        except Exception as exc:
            self._say(f"交卷二次确认失败，已停止交卷：{exc}", level="warning")
            return False

    def _answer_one(
        self, target: HomeworkTarget, question: Question,
        *, verified: Sequence[str] | None = None,
    ) -> str:
        key = _cache_key(target, question)
        labels: Sequence[str] | None = None
        cached = self.cache.get(key) if self.cache else None
        if verified:
            answer = resolve_option_ids(question, verified)
            self._say("使用答案图或已验证题库答案")
        elif cached and cached.get("answer"):
            answer = str(cached["answer"])
            labels = cached.get("labels") or None
        elif self.answerer is None:
            return ""
        else:
            labels = list(self.answerer(question.as_dict()))  # type: ignore[misc]
            answer = resolve_option_ids(question, labels)
            if self.cache:
                self.cache.put(
                    key,
                    {"answer": answer, "labels": list(labels), "type": question.type_id},
                )
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                self.client.save_answer(target, question, answer)
                if self.delay:
                    time.sleep(self.delay)
                return answer
            except ZhihuishuExamError as exc:
                last_error = exc
                if attempt > self.max_retries:
                    break
                if self.delay:
                    time.sleep(max(self.delay, 1.0) * attempt)
        raise ZhihuishuExamError(str(last_error) if last_error else "保存答案失败")


# --------------------------------------------------------------------------- #
# CLI（python -m app.zhihuishu_exam ...）
# --------------------------------------------------------------------------- #
def _build_client(args: Any) -> ZhihuishuExamClient:
    return ZhihuishuExamClient(cookie_file=args.cookie_file, timeout=args.timeout)


def _target_from_args(args: Any) -> HomeworkTarget:
    if getattr(args, "exam_url", ""):
        return HomeworkTarget.from_exam_url(args.exam_url)
    mapping = {
        "recruitId": args.recruit_id,
        "examId": args.exam_id,
        "studentExamId": args.stu_exam_id,
        "schoolId": args.school_id,
        "courseId": args.course_id,
    }
    return HomeworkTarget.from_mapping(mapping)


def _print_questions(questions: Sequence[Question]) -> None:
    for index, question in enumerate(questions, start=1):
        print(f"\n[{index}] {question.type_name}（{question.type_key}）{question.score} 分  eid={question.eid}")
        print("    " + question.stem.replace("\n", "\n    "))
        for option in question.options:
            print(f"      {option.label}. {option.text}  [{option.id}]")


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="智慧树作业 / 平时测试 纯 HTTP 客户端")
    parser.add_argument("command", choices=["paper", "readback", "save", "answer", "submit", "list", "raw"])
    parser.add_argument("--cookie-file", default="work/zhs_exam_cookie.json", help="cookie JSON 路径（或设 ZHS_COOKIE）")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--exam-url", default="", help="形如 #/webExamList/dohomework/<recruit>/<stu>/<exam>/<course>/<school>/0")
    parser.add_argument("--recruit-id", default="")
    parser.add_argument("--exam-id", default="")
    parser.add_argument("--stu-exam-id", default="")
    parser.add_argument("--school-id", default="")
    parser.add_argument("--course-id", default="")
    parser.add_argument("--videolist", default="", help="list：从学习页 videolist JSON 提取平时测试目标")
    parser.add_argument("--eid", default="", help="save：题目 eid")
    parser.add_argument("--answer", default="", help="save：选项 id 串（多选用逗号）")
    parser.add_argument("--question-type", type=int, default=0, help="save：questionType，默认取题目自身")
    parser.add_argument("--path", default="", help="raw：接口路径，如 /answer/hasAnswer")
    parser.add_argument("--params", default="{}", help="raw：明文参数 JSON")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--api-base", default=os.environ.get("AI_API_BASE", "https://api.openai.com/v1"))
    parser.add_argument("--api-key", default=os.environ.get("AI_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("AI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--submit", action="store_true", help="answer：全部作答后交卷")
    parser.add_argument("--i-know-what-im-doing", dest="confirm", action="store_true", help="submit：确认不可逆交卷")
    args = parser.parse_args(argv)

    if args.command == "list":
        if not args.videolist:
            print("list 需要 --videolist <学习页 videolist 响应 JSON>（项目内由 Fucker.videoList 拉取）")
            return 2
        payload = json.loads(Path(args.videolist).read_text(encoding="utf-8"))
        targets = targets_from_videolist(payload, recruit_id=args.recruit_id, course_id=args.course_id)
        for target in targets:
            print(target.describe() + (f"  截止 {target.deadline}" if target.deadline else ""))
            print("   " + target.exam_url)
        return 0

    client = _build_client(args)
    if args.command == "raw":
        data = client.post(args.path, json.loads(args.params))
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    target = _target_from_args(args)
    if args.command == "paper":
        paper = client.paper(target)
        if args.json:
            print(json.dumps({"examBase": paper["examBase"], "deadline": paper["deadline"]}, ensure_ascii=False, indent=1))
        else:
            exam_base = paper["examBase"]
            print(
                f"《{exam_base.get('name')}》/ {exam_base.get('courseName')} "
                f"题数={exam_base.get('problemNum')} 总分={exam_base.get('totalScore')} 截止={paper['deadline']}"
            )
            _print_questions(paper["questions"])
        return 0
    if args.command == "readback":
        data = client.readback(target)
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    if args.command == "save":
        if not args.eid or not args.answer:
            print("save 需要 --eid 与 --answer")
            return 2
        paper = client.paper(target)
        question = next((item for item in paper["questions"] if item.eid == args.eid), None)
        if question is None:
            print("该 eid 不在这份卷子里")
            return 2
        answer = resolve_option_ids(question, [args.answer]) if question.options else args.answer
        data = client.save_answer(target, question, answer, question_type=args.question_type or None)
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    if args.command == "submit":
        if not args.confirm:
            print("交卷不可逆（会立即判分）。确认要交就加 --i-know-what-im-doing。")
            return 2
        paper = client.paper(target)
        data = client.readback(target, paper["questions"])
        achieve = len(data)
        print(json.dumps(client.submit(target, achieve_count=achieve, confirm=True), ensure_ascii=False, indent=1))
        return 0
    # answer
    if not args.api_key:
        print("answer 需要 --api-key（或环境变量 AI_API_KEY）来驱动 AI 作答")
        return 2
    runner = HomeworkRunner(
        client,
        answerer=openai_answerer(
            {
                "api_base": args.api_base,
                "api_key": args.api_key,
                "model_name": args.model,
                "api_retries": 4,
                "api_retry_backoff": 1.5,
            }
        ),
        answer_cache="work/zhs_homework_answer_cache.json",
        delay=args.delay,
        confirm_ai_only_submit=lambda question_ids: input(
            f"有 {len(question_ids)} 道题未命中答案图/题库，仅由 AI 或答案缓存作答。"
            "仍要交卷吗？[y/N] "
        ).strip().lower() in {"y", "yes"},
        progress=lambda event: print(
            f"  {getattr(event, 'message', event)}", flush=True
        ),
    )
    result = runner.run(target, submit=bool(args.submit and args.confirm))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=1))
    return 0 if not result.failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


# --------------------------------------------------------------------------- #
# 考试会话自举（jt-cas → CAS service ticket → o_session_id）
# --------------------------------------------------------------------------- #
#
# 2026-11 实测结论（work/zhs_hw_gologin_probe.js / zhs_hw_cas_probe.js）：
#   * exam 网关（studentexam-api）只认 cookie 会话，不认裸 jt-cas：
#       只有 jt-cas            → getLoginUserInfo 回 loginstatus:false，业务接口 200 但回 HTML 首页
#       jt-cas + 有效 o_session_id → 真正的 JSON
#   * o_session_id 是 exam 子系统自己的 CAS 会话标识，只能由 SSO 换票建立：
#       GET https://studentexam-api.zhihuishu.com/studentExam/gateway/f/v1/gologin/login?fromurl=<考试页>
#           无 ticket → 302 https://passport.zhihuishu.com?service=<gologin/login?fromurl=...>
#       passport 需要 .zhihuishu.com 的 CASTGC(TGT)，换出后回调 gologin/login?ticket=ST-...
#       → 该跳 Set-Cookie o_session_id / JSESSIONID，会话建立
#   * 直接用 getLoginUserInfo 自举出来的 o_session_id 是**匿名会话**（值等于 JSESSIONID），不可用。
#
SSO_GOLOGIN = "https://studentexam-api.zhihuishu.com/studentExam/gateway/f/v1/gologin/login"
PASSPORT_LOGIN = "https://passport.zhihuishu.com/login"
EXAM_PAGE = "https://onlineexamh5new.zhihuishu.com/stuExamWeb.html"

# 会话探测接口：只有真会话才会回 JSON（假会话回 84KB HTML 首页）
EP_PROBE = "/exam/listAllRecruits"


def exam_page_url(target: HomeworkTarget) -> str:
    """拼出该份作业的 H5 地址（只用于 SSO 的 fromurl 参数）。"""
    return (
        f"{EXAM_PAGE}#/webExamList/dohomework/{target.recruit_id}"
        f"/{target.stu_exam_id}/{target.exam_id}/{target.course_id}/{target.school_id}/0"
    )


def _absorb_cookies(jar: dict[str, str], cookies: Any) -> None:
    """把 requests 会话 / 列表 / 字典里的 cookie 并入 ``jar``（后者覆盖前者）。"""
    for name, value in _iter_cookie_pairs(cookies):
        if name and (value != "" or name not in jar):
            jar[name] = value


def build_cookie_dict(
    cookies: Any = None,
    path: str | os.PathLike[str] | None = None,
    *,
    fallback: bool = True,
) -> dict[str, str]:
    """把任意 cookie 载体（fucker 的 ``session.cookies`` / JSON 文件 / 原始头）拍平成 dict。"""
    jar: dict[str, str] = {}
    if cookies is not None:
        _absorb_cookies(jar, cookies)
    if fallback and (not jar or REQUIRED_COOKIES[1] not in jar):
        payload = load_cookie_payload(path)
        if payload is not None:
            _absorb_cookies(jar, payload)
    return jar


def bootstrap_exam_session(
    cookies: Any,
    *,
    fromurl: str = "",
    proxies: Mapping[str, str] | None = None,
    timeout: float = 15.0,
    max_hops: int = 6,
    log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """用 ``jt-cas`` + ``CASTGC`` 走 CAS 换票，拿到 exam 子系统可用的 ``o_session_id``。

    返回合并后的 cookie dict。拿不到真会话时抛 :class:`ZhihuishuSessionError`。
    """
    say = log or (lambda *_a, **_k: None)
    jar: dict[str, str] = {}
    _absorb_cookies(jar, cookies)
    if not jar.get("jt-cas"):
        raise ZhihuishuSessionError("考试会话自举失败：缺少 jt-cas（请先登录智慧树）")
    if not jar.get("CASTGC"):
        raise ZhihuishuSessionError(
            "考试会话自举失败：cookie 里没有 CASTGC（缺失或值为空串），无法走 CAS 换票拿 "
            "o_session_id。智慧树网页版登录后通常不再下发 CASTGC，所以这条路在桌面端基本"
            "不可用。可行做法：从浏览器重新导出 cookies.json，直接带上 .zhihuishu.com "
            "域下有效的 o_session_id（浏览器能正常打开考试页即可）。"
        )

    fromurl = fromurl or EXAM_PAGE + "#/webExamList/dohomework/0/0/0/0/0/0"
    url = f"{SSO_GOLOGIN}?fromurl={quote(fromurl, safe='')}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": DEFAULT_UA,
        "Referer": EXAMPAGE_REFERER if False else EXAM_PAGE + "/",  # noqa: E501  (保持简单字符串)
    }
    session = requests.Session()
    session.headers.update(headers)
    session.max_redirects = max_hops + 2
    try:
        for hop in range(max_hops):
            session.cookies.clear()
            for name, value in jar.items():
                session.cookies.set(name, value)
            resp = session.get(url, allow_redirects=False, proxies=dict(proxies or {}), timeout=timeout)
            _absorb_cookies(jar, session.cookies)
            location = resp.headers.get("Location") or ""
            say(f"SSO 第 {hop + 1} 跳：{resp.status_code} {url.split('?')[0]}")
            if resp.status_code not in (301, 302, 303, 307, 308) or not location:
                break
            nxt = urljoin(url, location)
            if "passport.zhihuishu.com" in nxt and not jar.get("CASTGC"):
                raise ZhihuishuSessionError(
                    "考试会话自举失败：passport 要求登录（CASTGC 缺失或已被注销）。"
                    "请在桌面端重新登录智慧树一次。"
                )
            url = nxt
            if "gologin/login" in url and "ticket=" in url:
                continue
    finally:
        session.close()

    _absorb_cookies(jar, session.cookies)
    return jar
