import base64
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from threading import Event
import unittest
import zlib

from app.models import AdapterError
from app.zhihuishu_answer_bank import (
    AnswerBank, _TEMPLATE_HEX, fetch_answer_images, question_key,
    read_answer_letters, sync_answer_bank,
)
from app.zhihuishu_exam import (
    HomeworkTarget, ZhihuishuExamError, parse_questions,
)


def image(unknown=False, small=False, two_letters=False):
    width, height = 48, 20
    canvas = bytearray([255] * (width * height * 3))
    mask = int(_TEMPLATE_HEX["A"], 16)
    for y in range(16):
        for x in range(16):
            if mask & (1 << (255 - y * 16 - x)):
                i = ((y + 1) * width + x + 1) * 3
                canvas[i:i + 3] = b"\0\0\0"
            if unknown:
                i = ((y + 1) * width + x + 24) * 3
                canvas[i:i + 3] = b"\0\0\0"
            if two_letters and mask & (1 << (255 - y * 16 - x)):
                i = ((y + 1) * width + x + 24) * 3
                canvas[i:i + 3] = b"\0\0\0"
    if small:
        for y in range(1, 17):
            for x in range(24, 27):
                i = (y * width + x) * 3
                canvas[i:i + 3] = b"\0\0\0"
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    data = b"".join(b"\0" + canvas[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def image_with_letters(letters):
    width, height = 18 * len(letters), 20
    canvas = bytearray([255] * (width * height * 3))
    for index, letter in enumerate(letters):
        mask = int(_TEMPLATE_HEX[letter], 16)
        for y in range(16):
            for x in range(16):
                if mask & (1 << (255 - y * 16 - x)):
                    i = ((y + 1) * width + index * 18 + x + 1) * 3
                    canvas[i:i + 3] = b"\0\0\0"

    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))

    data = b"".join(b"\0" + canvas[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(data)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def question(eid="eid", ids=("1", "2")):
    return {
        "id": "qid", "eid": eid, "name": "测试题", "questionScore": "2",
        "questionType": {"id": 1, "name": "单选题"},
        "questionOptions": [{"id": ids[0], "content": "选项一"}, {"id": ids[1], "content": "选项二"}],
    }


class FakeClient:
    def __init__(self, q, data_url):
        self.question = q
        self.data_url = data_url
        self.calls = []

    def post(self, path, params, *, label):
        self.calls.append((path, params))
        if path == "/student/lookHomework":
            return {"rt": {"examBase": {"workExamParts": [{"questionDtos": [self.question]}]}}}
        if path == "/answer/getAnswerImgInfo":
            return {"rt": {"qid": self.data_url}}
        raise AssertionError("unexpected platform write or request")


class AnswerBankTests(unittest.TestCase):
    def setUp(self):
        self.target = HomeworkTarget("r", "exam", "s", "h", "c")

    def test_image_decoding_refuses_low_confidence_partial_answer(self):
        self.assertEqual(read_answer_letters(image()), "A")
        self.assertEqual(read_answer_letters(image(unknown=True)), "")
        self.assertEqual(read_answer_letters(image(small=True)), "")

    def test_image_decoding_supports_five_and_six_option_multi_select(self):
        self.assertEqual(read_answer_letters(image_with_letters("ABCDE")), "ABCDE")
        self.assertEqual(read_answer_letters(image_with_letters("ABCDEF")), "ABCDEF")

        raw = question()
        raw["questionType"] = {"id": 2, "name": "多选题"}
        raw["questionOptions"] = [
            {"id": str(index), "content": f"选项{index}"} for index in range(6)
        ]
        result = fetch_answer_images(FakeClient(raw, image_with_letters("ACDF")), self.target)
        self.assertEqual(result["items"][0]["letters"], "ACDF")
        self.assertFalse(result["items"][0]["error"])

    def test_unsupported_type_and_multiple_answers_for_single_choice_are_rejected(self):
        raw = question()
        raw["questionType"] = {"id": 3, "name": "填空题"}
        self.assertTrue(fetch_answer_images(FakeClient(raw, image()), self.target)["items"][0]["error"])
        raw["questionType"] = {"id": 1, "name": "单选题"}
        self.assertEqual(read_answer_letters(image(two_letters=True)), "AA")
        self.assertTrue(fetch_answer_images(FakeClient(raw, image(two_letters=True)), self.target)["items"][0]["error"])

    def test_fetch_and_sync_are_read_only_and_can_reuse_changed_option_ids(self):
        client = FakeClient(question(), image())
        fetched = fetch_answer_images(client, self.target)
        self.assertEqual(fetched["items"][0]["letters"], "A")
        self.assertTrue(fetched["items"][0]["imageBase64"])
        self.assertEqual(json.loads(client.calls[-1][1]["optionSortInfo"]), [
            {"questionId": "qid", "optionsIdStr": "1,2"},
        ])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "answer-bank.json"
            result = sync_answer_bank(client, self.target, path)
            self.assertEqual((result["saved"], result["skipped"]), (1, 0))
            changed = question(ids=("x", "y"))
            changed["questionOptions"].reverse()
            parsed = parse_questions({"examBase": {"workExamParts": [{"questionDtos": [changed]}]}})[0]
            self.assertEqual(AnswerBank(path).answer_labels(parsed), ["B"])
        self.assertTrue(all(path in ("/student/lookHomework", "/answer/getAnswerImgInfo") for path, _ in client.calls))

    def test_legacy_unsafe_image_entry_is_not_reused(self):
        raw = question()
        parsed = parse_questions({"examBase": {"workExamParts": [{"questionDtos": [raw]}]}})[0]
        entry = {"verified": True, "oracle": "answerImage", "answer": "1",
                 "options": [{"id": "1", "text": "选项一"}, {"id": "2", "text": "选项二"}]}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bank.json"
            path.write_text(json.dumps({"entries": {question_key(raw): entry}}), encoding="utf-8")
            self.assertIsNone(AnswerBank(path).answer_labels(parsed))
            entry.pop("oracle")
            path.write_text(json.dumps({"entries": {question_key(raw): entry}}), encoding="utf-8")
            self.assertIsNone(AnswerBank(path).answer_labels(parsed))

    def test_corrupt_bank_is_not_overwritten(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bank.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(AdapterError):
                sync_answer_bank(FakeClient(question(), image()), self.target, path)
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")

    def test_image_questions_and_cancellation_are_not_imported(self):
        raw = question()
        raw["name"] = '<img src="example.png">'
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bank.json"
            result = sync_answer_bank(FakeClient(raw, image()), self.target, path)
            self.assertEqual((result["saved"], result["skipped"]), (0, 1))
            self.assertFalse(path.exists())
            cancelled = Event()
            cancelled.set()
            client = FakeClient(question(), image())
            self.assertTrue(sync_answer_bank(client, self.target, path, cancel_event=cancelled)["cancelled"])
            self.assertFalse(client.calls)

    def test_look_homework_msg2_falls_back_to_do_homework(self):
        # 见面课作业 lookHomework 回 -1/msg=2（业务码），必须改走 doHomework 取题，
        # 而不是当成会话失效去重建会话（实测 work/zhs_look_msg2_matrix.js）。
        class Msg2Client(FakeClient):
            def post(self, path, params, *, label):
                self.calls.append((path, params))
                if path == "/student/lookHomework":
                    raise ZhihuishuExamError(
                        "查看作业 业务失败（status=-1）：msg=2，该卷存在，但 lookHomework "
                        "不开放这类作业（见面课/视频课作业），改用 doHomework 取题"
                        "（这与登录会话无关，重建会话无效）")
                if path == "/student/doHomework":
                    return {"rt": {"examBase": {"workExamParts": [
                        {"questionDtos": [self.question]}]}}}
                if path == "/answer/getAnswerImgInfo":
                    return {"rt": {"qid": self.data_url}}
                raise AssertionError("unexpected platform write or request")

        events = []
        client = Msg2Client(question(), image())
        result = fetch_answer_images(client, self.target, progress=events.append)
        self.assertEqual(result["total"], 1)
        self.assertEqual([p for p, _ in client.calls],
                         ["/student/lookHomework", "/student/doHomework",
                          "/answer/getAnswerImgInfo"])
        self.assertTrue(any("doHomework" in e.message for e in events))

    def test_look_homework_other_business_error_is_not_retried(self):
        class DeniedClient(FakeClient):
            def post(self, path, params, *, label):
                self.calls.append((path, params))
                raise ZhihuishuExamError("查看作业 业务失败（status=-1）：传入参数异常：examId / stuExamId")

        client = DeniedClient(question(), image())
        with self.assertRaisesRegex(ZhihuishuExamError, "传入参数异常"):
            fetch_answer_images(client, self.target)
        self.assertEqual([p for p, _ in client.calls], ["/student/lookHomework"])



    def test_null_question_id_falls_back_to_hist_qid_index(self):
        # redo/退回态 doHomework 常返回 question.id=null，需按选项指纹回落历史数字 qid
        from tempfile import TemporaryDirectory
        raw = question()
        raw["id"] = None
        raw["questionOptions"] = [
            {"id": "521432003", "content": "选项一"},
            {"id": "521432005", "content": "选项二"},
        ]
        with TemporaryDirectory() as directory:
            index_path = Path(directory) / "qid-index.json"
            index_path.write_text(json.dumps({
                "version": 1,
                "byOptions": {"521432003|521432005": "1110815716"},
                "byKey": {},
            }), encoding="utf-8")

            class RedoClient(FakeClient):
                def post(self, path, params, *, label):
                    self.calls.append((path, params))
                    if path == "/student/lookHomework":
                        raise ZhihuishuExamError(
                            "查看作业 业务失败：status=-1，msg=2：该卷不存在")
                    if path == "/student/doHomework":
                        return {"rt": {"examBase": {"workExamParts": [
                            {"questionDtos": [self.question]}]}}}
                    if path == "/answer/getAnswerImgInfo":
                        sort_info = json.loads(params["optionSortInfo"])
                        self.last_sort = sort_info
                        return {"rt": {"1110815716": self.data_url}}
                    raise AssertionError(path)

            client = RedoClient(raw, image())
            result = fetch_answer_images(client, self.target, qid_index_path=index_path)
            self.assertEqual(client.last_sort, [
                {"questionId": "1110815716", "optionsIdStr": "521432003,521432005"},
            ])
            self.assertEqual(result["histFallback"], 1)
            self.assertEqual(result["items"][0]["letters"], "A")
            self.assertEqual(result["items"][0]["questionIdSource"], "hist-options")
            self.assertFalse(result["items"][0]["error"])

    def test_live_numeric_id_is_remembered_into_qid_index(self):
        from tempfile import TemporaryDirectory
        raw = question()
        raw["id"] = 1110815716
        raw["questionOptions"] = [
            {"id": "521432003", "content": "选项一"},
            {"id": "521432005", "content": "选项二"},
        ]
        with TemporaryDirectory() as directory:
            index_path = Path(directory) / "qid-index.json"
            client = FakeClient(raw, image())
            # FakeClient returns image under key "qid"; remap for numeric id
            class LiveClient(FakeClient):
                def post(self, path, params, *, label):
                    self.calls.append((path, params))
                    if path == "/student/lookHomework":
                        return {"rt": {"examBase": {"workExamParts": [
                            {"questionDtos": [self.question]}]}}}
                    if path == "/answer/getAnswerImgInfo":
                        sort_info = json.loads(params["optionSortInfo"])
                        self.last_sort = sort_info
                        return {"rt": {str(sort_info[0]["questionId"]): self.data_url}}
                    raise AssertionError(path)

            client = LiveClient(raw, image())
            result = fetch_answer_images(client, self.target, qid_index_path=index_path)
            self.assertEqual(client.last_sort[0]["questionId"], "1110815716")
            self.assertEqual(result["histFallback"], 0)
            self.assertEqual(result["items"][0]["questionIdSource"], "live")
            saved = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["byOptions"]["521432003|521432005"], "1110815716")



if __name__ == "__main__":
    unittest.main()
