"""智慧树答案图读取与可复用题库。所有平台调用均为只读。"""

from __future__ import annotations

import base64
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Event
from typing import Any, Callable, Mapping
import struct
import zlib

from app.models import AdapterError, ProgressEvent
from app.zhihuishu_exam import HomeworkTarget, Question, ZhihuishuExamError

_TEMPLATE_HEX = {
    "A": "018003c003c0066006600e700c300c30081018181ff8300c300c700e600ee007",
    "B": "ffc0fff8e00ee00ee00ee00ee078fff0fff8e03ee007e007e007e00ffff8fff0",
    "C": "01ff07ff3800300060006000c000c000c000c000e0006000700038010fff03fc",
    "D": "ffc0fff0c01cc00ec006c007c003c003c003c003c006c006c00ec01cfff0ff80",
    "E": "ffffffffe000e000e000e000e000fffefffee000e000e000e000e000ffffffff",
    "F": "ffffffffe000e000e000e000e000fffcfffce000e000e000e000e000e000e000",
}
_TEMPLATES = {name: int(value, 16) for name, value in _TEMPLATE_HEX.items()}


def _text(value: Any) -> str:
    text = re.sub(r"<br\s*/?>", " ", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def question_key(raw: Mapping[str, Any]) -> str | None:
    """与原答案库的纯文本指纹兼容；包含图片时不推测其语义。"""
    options = raw.get("questionOptions") or []
    values = [raw.get("name"), *(o.get("content") for o in options if isinstance(o, Mapping))]
    if any(re.search(r"<img\b", str(v or ""), re.I) for v in values):
        return None
    if not options or any(not isinstance(o, Mapping) for o in options):
        return None
    texts = [_text(o.get("content")) for o in options]
    if not all(texts) or len(set(texts)) != len(texts):
        return None
    # JavaScript Array.sort compares UTF-16 code units.
    texts.sort(key=lambda s: s.encode("utf-16-be", "surrogatepass"))
    type_info = raw.get("questionType") or {}
    name = str(type_info.get("name") or "") if isinstance(type_info, Mapping) else ""
    key = name + "\x01" + _text(raw.get("name")) + "\x01" + "|".join(texts)
    return sha256(key.encode("utf-8")).hexdigest()[:24]


def _png_pixels(data: bytes) -> tuple[int, int, list[bool]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG")
    pos, compressed, header = 8, bytearray(), None
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise ValueError("truncated PNG")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            compressed.extend(body)
        elif kind == b"IEND":
            break
        pos += length + 12
    if not header:
        raise ValueError("missing PNG header")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if (not width or not height or width * height > 4_000_000 or depth != 8 or not channels
            or compression or filtering or interlace):
        raise ValueError("unsupported PNG format")
    stride = width * channels
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(compressed, (stride + 1) * height + 1)
    if len(raw) != (stride + 1) * height or not decompressor.eof:
        raise ValueError("invalid PNG data")
    previous = bytearray(stride)
    dark = []
    offset = 0
    for _ in range(height):
        method = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += stride + 1
        if method > 4:
            raise ValueError("unknown PNG filter")
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            above = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if method == 1:
                delta = left
            elif method == 2:
                delta = above
            elif method == 3:
                delta = (left + above) // 2
            elif method == 4:
                pa, pb, pc = abs(above - corner), abs(left - corner), abs(left + above - 2 * corner)
                delta = left if pa <= pb and pa <= pc else above if pb <= pc else corner
            else:
                delta = 0
            line[i] = (line[i] + delta) & 255
        for x in range(width):
            i = x * channels
            value = line[i] if color in (0, 4) else 0.299 * line[i] + 0.587 * line[i + 1] + 0.114 * line[i + 2]
            dark.append(value < 200)
        previous = line
    return width, height, dark


def read_answer_letters(encoded: str) -> str:
    """以原工具的 A–D 字模识别 PNG，低置信时返回空字符串。"""
    try:
        if not encoded.startswith("data:image/png;base64,"):
            return ""
        data = base64.b64decode(encoded.split(",", 1)[1], validate=True)
        if len(data) > 4 * 1024 * 1024:
            return ""
        width, height, dark = _png_pixels(data)
        bands: list[tuple[int, int]] = []
        start = None
        for y in range(height):
            active = any(dark[y * width:(y + 1) * width])
            if active and start is None:
                start = y
            if not active and start is not None:
                bands.append((start, y - 1))
                start = None
        if start is not None:
            bands.append((start, height - 1))
        if not bands:
            return ""
        y0, y1 = bands[-1]
        runs: list[tuple[int, int]] = []
        start = None
        for x in range(width):
            active = any(dark[y * width + x] for y in range(y0, y1 + 1))
            if active and start is None:
                start = x
            if not active and start is not None:
                runs.append((start, x - 1))
                start = None
        if start is not None:
            runs.append((start, width - 1))
        letters = []
        for x0, x1 in runs:
            w, h = x1 - x0 + 1, y1 - y0 + 1
            if h < 12 or w < 8:
                # 即使是较小字形也可能是漏切的答案字母，不使用部分答案。
                return ""
            bits = 0
            for gy in range(16):
                sy = y0 + int((gy + 0.5) * h / 16)
                for gx in range(16):
                    sx = x0 + int((gx + 0.5) * w / 16)
                    bits = (bits << 1) | int(dark[sy * width + sx])
            best = min(_TEMPLATES, key=lambda name: (bits ^ _TEMPLATES[name]).bit_count())
            if (bits ^ _TEMPLATES[best]).bit_count() > 40:
                return ""
            letters.append(best)
        return "".join(letters)
    except (ValueError, OSError, zlib.error, struct.error):
        return ""



def _numeric_question_id(value: Any) -> str | None:
    """Accept only platform numeric question ids (reject null/eid/base64)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or not text.isdigit():
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    if number < 1_000_000:
        return None
    return text


def _options_fingerprint(raw: Mapping[str, Any] | None) -> str | None:
    """Stable fingerprint of option ids; option ids survive redo when question.id does not."""
    if not isinstance(raw, Mapping):
        return None
    options = raw.get("questionOptions") or raw.get("options") or []
    if not isinstance(options, list) or not options:
        return None
    ids: list[str] = []
    for option in options:
        if isinstance(option, Mapping):
            oid = option.get("id")
            if oid is None:
                oid = option.get("optionId")
        else:
            oid = option
        text = str(oid or "").strip()
        if not text:
            return None
        ids.append(text)
    if len(set(ids)) != len(ids):
        return None
    return "|".join(sorted(ids))


def _default_qid_index_path() -> Path:
    return Path(__file__).resolve().parents[1] / "work" / "zhs_qid_index.json"


def _load_qid_index(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else _default_qid_index_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {"version": 1, "byOptions": {}, "byKey": {}}
    if not isinstance(data, dict):
        return {"version": 1, "byOptions": {}, "byKey": {}}
    by_options = data.get("byOptions")
    by_key = data.get("byKey")
    return {
        "version": int(data.get("version") or 1),
        "byOptions": by_options if isinstance(by_options, dict) else {},
        "byKey": by_key if isinstance(by_key, dict) else {},
    }


def _save_qid_index(data: Mapping[str, Any], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else _default_qid_index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": int(data.get("version") or 1),
        "byOptions": dict(data.get("byOptions") or {}),
        "byKey": dict(data.get("byKey") or {}),
    }
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as stream:
            name = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=1)
        os.replace(name, target)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return target


def _remember_question_ids(questions: list[Mapping[str, Any]], *, index_path: Path | None = None) -> int:
    """Persist numeric question.id ↔ option fingerprint (and question_key) for redo fallback."""
    index = _load_qid_index(index_path)
    by_options = index.setdefault("byOptions", {})
    by_key = index.setdefault("byKey", {})
    saved = 0
    for raw in questions:
        if not isinstance(raw, Mapping):
            continue
        qid = _numeric_question_id(raw.get("id"))
        if not qid:
            continue
        fp = _options_fingerprint(raw)
        key = question_key(raw)
        if fp:
            prev = by_options.get(fp)
            if prev != qid:
                by_options[fp] = qid
                saved += 1
        if key:
            prev = by_key.get(key)
            if prev != qid:
                by_key[key] = qid
                if not fp:
                    saved += 1
    if saved:
        _save_qid_index(index, index_path)
    return saved


def _resolve_question_id(raw: Mapping[str, Any], *, index: Mapping[str, Any] | None = None) -> tuple[str | None, str]:
    """Return (questionId for getAnswerImgInfo, source). Prefer live id; else hist index."""
    raw_id = raw.get("id")
    numeric = _numeric_question_id(raw_id)
    if numeric:
        return numeric, "live"
    # Non-null non-numeric id (unit tests / unexpected shapes): keep old pass-through.
    if raw_id is not None:
        text = str(raw_id).strip()
        if text and text.lower() != "null":
            return text, "raw"
    data = index if isinstance(index, Mapping) else _load_qid_index()
    by_options = data.get("byOptions") if isinstance(data.get("byOptions"), Mapping) else {}
    by_key = data.get("byKey") if isinstance(data.get("byKey"), Mapping) else {}
    fp = _options_fingerprint(raw)
    if fp and fp in by_options:
        hist = _numeric_question_id(by_options.get(fp))
        if hist:
            return hist, "hist-options"
    key = question_key(raw)
    if key and key in by_key:
        hist = _numeric_question_id(by_key.get(key))
        if hist:
            return hist, "hist-key"
    return None, "missing"



def _questions(rt: Any) -> list[dict[str, Any]]:
    base = rt.get("examBase") if isinstance(rt, Mapping) else None
    parts = base.get("workExamParts") if isinstance(base, Mapping) else []
    return [q for part in (parts or []) if isinstance(part, Mapping)
            for q in (part.get("questionDtos") or []) if isinstance(q, dict)]


def _valid_choice(raw: Mapping[str, Any], letters: str) -> bool:
    info = raw.get("questionType")
    try:
        kind = int(info.get("id")) if isinstance(info, Mapping) else 0
    except (TypeError, ValueError):
        return False
    return (kind in (1, 2, 14) and bool(letters) and
            (kind == 2 or len(letters) == 1))


class AnswerImageLookup:
    """把当前测试的答案图按题面指纹映射到当前卷选项标签。"""

    def __init__(self, items: list[Mapping[str, Any]] | None = None) -> None:
        items = items or []
        counts = Counter(question_key(item.get("raw") or {}) for item in items)
        self._answers: dict[str, list[str]] = {}
        for item in items:
            raw = item.get("raw")
            key = question_key(raw) if isinstance(raw, Mapping) else None
            letters = str(item.get("letters") or "")
            options = raw.get("questionOptions") if isinstance(raw, Mapping) else None
            if (not key or counts[key] != 1 or item.get("error") or not letters
                    or not isinstance(options, list)):
                continue
            selected = []
            valid = True
            for letter in letters:
                index = ord(letter) - ord("A")
                if index < 0 or index >= len(options) or not isinstance(options[index], Mapping):
                    valid = False
                    break
                selected.append(_text(options[index].get("content")))
            if valid and selected and len(set(selected)) == len(selected):
                self._answers[key] = selected

    @property
    def count(self) -> int:
        return len(self._answers)

    def answer_labels(self, question: Question) -> list[str] | None:
        key = question_key(question.raw)
        selected = self._answers.get(key) if key else None
        if not selected:
            return None
        labels = {_text(option.text): option.label for option in question.options}
        if len(labels) != len(question.options) or any(text not in labels for text in selected):
            return None
        return [labels[text] for text in selected]



def fetch_answer_images(client: Any, target: HomeworkTarget, *,
                        progress: Callable[[ProgressEvent], None] | None = None,
                        cancel_event: Event | None = None,
                        qid_index_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """只读拉取并识别一份平时测试答图；不提交答案或交卷。

    重做/退回态下 doHomework 常返回 question.id=null（只剩 eid），此时
    getAnswerImgInfo 需要历史数字 questionId。本函数会：
      1) 有数字 id 时写入 work/zhs_qid_index.json（按选项 id 指纹）；
      2) id 缺失时按选项指纹 / question_key 回落历史 qid 再拉图。
    """
    if cancel_event and cancel_event.is_set():
        return {"items": [], "total": 0, "cancelled": True, "histFallback": 0}
    if progress:
        progress(ProgressEvent("正在读取试卷答案图…"))
    # 部分课/付费类作业对 lookHomework 会被拦业务码拒绝（status=-1,msg=2）。
    # 这不是会话失效：同一账号同一单元卷对 doHomework 仍能拿到题目（实证 10 题，见
    # work/zhs_look_msg2_matrix.js）。策略：先试 lookHomework；业务码拒绝再回落 doHomework。
    try:
        look = client.post("/student/lookHomework", target.five_tuple(), label="查看作业")
    except ZhihuishuExamError as exc:
        if "msg=2" not in str(exc) and "msg=1" not in str(exc):
            raise
        if progress:
            progress(ProgressEvent(f"查看作业被拒（{exc}），改用取卷接口 doHomework",
                                   level="warning"))
        look = client.post("/student/doHomework", target.five_tuple(), label="取卷")
    questions = _questions(look.get("rt"))
    if not questions:
        raise ZhihuishuExamError("该试卷未返回题目，无法读取答图")

    index_path = Path(qid_index_path) if qid_index_path is not None else None
    remembered = _remember_question_ids(questions, index_path=index_path)
    index = _load_qid_index(index_path)
    resolved: list[tuple[str | None, str]] = [
        _resolve_question_id(q, index=index) for q in questions
    ]
    hist_fallback = sum(1 for qid, source in resolved if qid and source.startswith("hist-"))
    if remembered and progress:
        progress(ProgressEvent(f"已缓存 {remembered} 个数字题号供重做回落"))
    if hist_fallback and progress:
        progress(ProgressEvent(
            f"重做态缺数字题号，已按选项指纹回落 {hist_fallback} 题历史 qid",
            level="warning",
        ))

    missing_qid = sum(1 for qid, source in resolved if not qid)
    if missing_qid and progress:
        progress(ProgressEvent(
            f"有 {missing_qid} 题缺少数值 questionId 且无历史回落"
            f"（常见于见面课/视频课：lookHomework=msg2，平台不下发数字题号，"
            f"getAnswerImgInfo 不可用；将自动改走 AI/题库，不是桌面链路残缺）",
            level="warning",
        ))

    sort_info = []
    for q, (qid, _source) in zip(questions, resolved):
        options_id_str = ",".join(
            str(o.get("id") or "") for o in q.get("questionOptions") or [] if isinstance(o, Mapping)
        )
        sort_info.append({"questionId": qid, "optionsIdStr": options_id_str})
    if cancel_event and cancel_event.is_set():
        return {"items": [], "total": len(questions), "cancelled": True, "histFallback": hist_fallback}
    response = client.post("/answer/getAnswerImgInfo", {
        "optionSortInfo": json.dumps(sort_info, ensure_ascii=False, separators=(",", ":")),
        "recruitId": target.recruit_id, "examId": target.exam_id,
    }, label="获取答图")
    images = response.get("rt")
    images = images if isinstance(images, Mapping) else {}
    items = []
    for index_i, (q, (qid, source)) in enumerate(zip(questions, resolved), 1):
        if cancel_event and cancel_event.is_set():
            break
        data_url = images.get(str(qid)) if qid else ""
        if not isinstance(data_url, str):
            data_url = ""
        letters = read_answer_letters(data_url) if data_url else ""
        options = q.get("questionOptions") or []
        if any(ord(letter) - ord("A") >= len(options) for letter in letters) or len(set(letters)) != len(letters):
            letters = ""
        if not qid:
            error = ("缺少数值题号且无历史回落（见面课/视频课 look=msg2 时平台不下发 question.id，答图接口不可用；非 C# 链路问题）")
        elif letters:
            error = ""
        elif data_url:
            error = "答图识别失败或置信度不足"
        else:
            error = "未返回答案图"
        if letters and not _valid_choice(q, letters):
            error = "题型不受支持或答案数量不匹配"
            letters = ""
        items.append({
            "questionId": str(qid or q.get("id") or ""),
            "questionIdSource": source,
            "question": _text(q.get("name")),
            "letters": letters,
            "imageBase64": data_url.partition(",")[2] if data_url.startswith("data:image/png;base64,") else "",
            "error": error,
            "raw": q,
        })
        if progress:
            progress(ProgressEvent(f"答图 {index_i}/{len(questions)}", current=index_i, total=len(questions)))
    return {"items": items, "total": len(questions),
            "cancelled": bool(cancel_event and cancel_event.is_set()),
            "histFallback": hist_fallback}




class AnswerBank:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.data: dict[str, Any] = {"version": 1, "entries": {}}
        else:
            try:
                data = json.loads(content)
            except ValueError as exc:
                raise AdapterError(f"答案库损坏，已停止以保护原文件：{self.path}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
                raise AdapterError(f"答案库格式无效，已停止以保护原文件：{self.path}")
            self.data = data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                name = stream.name
                json.dump(self.data, stream, ensure_ascii=False, indent=1)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def answer_labels(self, question: Question) -> list[str] | None:
        """返回当前卷标签（不是旧卷 option ID）；无法可靠映射时返回 None。"""
        key = question_key(question.raw)
        record = self.data["entries"].get(key) if key else None
        if not isinstance(record, Mapping) or record.get("verified") is not True:
            return None
        # 旧脚本曾通过不唯一的题干或顺序猜测评分归属，历史记录不能自动视为可信。
        if not ((record.get("oracle") == "answerImage" and record.get("imageRecognition") == "complete-v1")
                or (record.get("oracle") == "answerImage" and record.get("crossChecked") is True)
                or record.get("verification") == "unique-score-v1"):
            return None
        stored = record.get("options")
        if not isinstance(stored, list) or not all(isinstance(o, Mapping) for o in stored):
            return None
        old = {str(o.get("id") or ""): _text(o.get("text")) for o in stored}
        new = {_text(o.text): o.label for o in question.options}
        if not old or len(old) != len(stored) or len(set(old.values())) != len(old) or "" in old.values():
            return None
        if len(new) != len(question.options) or set(new) != set(old.values()):
            return None
        ids = [v.strip() for v in str(record.get("answer") or "").split(",") if v.strip()]
        if not ids or len(set(ids)) != len(ids) or any(i not in old for i in ids):
            return None
        return [new[old[i]] for i in ids]


def sync_answer_bank(client: Any, target: HomeworkTarget, path: str | os.PathLike[str], *,
                     progress: Callable[[ProgressEvent], None] | None = None,
                     cancel_event: Event | None = None) -> dict[str, Any]:
    """只读收集答案图，将可识别的选项写入本地题库。"""
    bank = AnswerBank(path)
    result = fetch_answer_images(client, target, progress=progress, cancel_event=cancel_event)
    if result["cancelled"]:
        return {"saved": 0, "skipped": 0, "bankPath": str(bank.path),
                "items": [], "cancelled": True}
    keys = Counter(question_key(item["raw"]) for item in result["items"])
    saved = 0
    items = []
    for item in result["items"]:
        raw = item["raw"]
        key = question_key(raw)
        letters = item["letters"]
        options = raw.get("questionOptions") or []
        error = item["error"]
        if not key or keys[key] != 1:
            error = "图片题、重复题或选项无法唯一匹配"
        if not error and letters:
            ids = [str(options[ord(letter) - ord("A")].get("id") or "") for letter in letters]
            if not all(ids) or len(set(ids)) != len(ids):
                error = "选项 ID 无效"
            else:
                bank.data["entries"][key] = {
                    "questionType": (raw.get("questionType") or {}).get("name", ""),
                    "stem": _text(raw.get("name")),
                    "options": [{"id": str(o.get("id") or ""), "text": _text(o.get("content"))} for o in options],
                    "answer": ",".join(ids), "answerLetters": letters,
                    "verified": True, "oracle": "answerImage", "imageRecognition": "complete-v1",
                    "source": {"examId": target.exam_id, "studentExamId": target.stu_exam_id},
                }
                saved += 1
        items.append({"question": item["question"], "letters": letters, "error": error})
    if saved:
        bank.save()
    return {"saved": saved, "skipped": len(items) - saved, "bankPath": str(bank.path),
            "items": items, "cancelled": result["cancelled"]}
