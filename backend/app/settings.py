"""Desktop preferences, sharing config.json with the existing CLI."""

from copy import deepcopy
import json
import os
import math
import re
import secrets
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlparse
from app.paths import data_root


DEFAULT_AI = {
    "enabled": True,
    "use_zhidao_ai": True,
    "openai": {
        "api_base": "https://api.openai.com",
        "api_key": "",
        "model_name": "gpt-4o-mini",
    },
    "use_stream": True,
    "ppt_processing": {
        "provide_to_ai": False,
        "moonShot": {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "",
            "delete_after_convert": True,
        },
    },
}

DEFAULT_GENERAL = {
    "save_cookies": True, "cookie_path": "cookies.json", "speed": None,
    "threshold": 0.91, "limit": 0, "proxies": {"http": "", "https": ""},
    "image_path": "", "pushplus": {"enable": False, "token": ""},
    "bark": {"enable": False, "token": ""},
}

DEFAULT_EXAM_ASSIST = {
    "enabled": False,
    "api_base": "https://api.openai.com/v1",
    "api_key": "",
    "model_name": "gpt-4o-mini",
    "timeout": 45,
    "max_chars": 12000,
}

DEFAULT_EXAM_AUTO = {
    "enabled": False,
    "api_base": "https://api.openai.com/v1",
    "api_key": "",
    "model_name": "gpt-4o-mini",
    "timeout": 45,
    "captcha_timeout": 300,
    "answer_delay": 0.8,
    "auto_submit": True,
    "api_retries": 4,
    "api_retry_backoff": 1.5,
}

DEFAULT_ZHS_HOMEWORK = {
    # 智慧树「平时测试 / 作业」自动作答。能力本体在 app/zhihuishu_exam.py。
    # api_base / api_key / model_name 留空时沿用上面 ai.openai 的配置。
    "enabled": False,
    "api_base": "",
    "api_key": "",
    "model_name": "",
    "timeout": 45,
    "answer_delay": 0.8,
    # 交卷不可逆（服务端立即判分），默认只作答不交卷。
    "auto_submit": False,
    "api_retries": 4,
    "api_retry_backoff": 1.5,
    "only_missing": False,
    "overwrite_with_verified": True,
}

DEFAULT_ZHIHUISHU = {
    "homework": DEFAULT_ZHS_HOMEWORK,
}


DEFAULT_CHAOXING = {
    "browser": "msedge", "save_session": True, "login_timeout": 300,
    "playback_rate": 1.0, "muted": True, "stall_timeout": 300,
    "exam_assist": DEFAULT_EXAM_ASSIST,
    "exam_auto": DEFAULT_EXAM_AUTO,
}


def zhihuishu_settings(config: dict) -> dict:
    value = config.get("zhihuishu", {})
    if not isinstance(value, dict):
        raise ValueError("智慧树配置必须是 JSON 对象")
    return merge_settings(DEFAULT_ZHIHUISHU, value)


def chaoxing_settings(config: dict) -> dict:
    value = config.get("chaoxing", {})
    if not isinstance(value, dict):
        raise ValueError("学习通配置必须是 JSON 对象")
    return merge_settings(DEFAULT_CHAOXING, value)


def validate_exam_assist(value: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError("学习通考试辅助配置必须是 JSON 对象")
    if type(value.get("enabled")) is not bool:
        raise ValueError("学习通考试辅助开关需为布尔值")
    for key in ("api_base", "api_key", "model_name"):
        if not isinstance(value.get(key), str):
            raise ValueError(f"学习通考试辅助的 {key} 必须是文本")
    if value["enabled"]:
        try:
            parsed = urlparse(value["api_base"].strip())
            valid = parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("学习通考试辅助 Base URL 必须是有效的 HTTP / HTTPS 地址")
        if not value["api_key"].strip():
            raise ValueError("启用学习通考试辅助后请填写 API Key")
        if not value["model_name"].strip():
            raise ValueError("启用学习通考试辅助后请填写模型名称")
    _number(value["timeout"], "学习通考试辅助超时", 5, 300)
    _number(value["max_chars"], "学习通考试辅助题面长度", 1000, 50000, integer=True)


def validate_exam_auto(value: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError("学习通自动考试配置必须是 JSON 对象")
    if type(value.get("enabled")) is not bool:
        raise ValueError("学习通自动考试开关需为布尔值")
    for key in ("api_base", "api_key", "model_name"):
        if not isinstance(value.get(key), str):
            raise ValueError(f"学习通自动考试的 {key} 必须是文本")
    if type(value.get("auto_submit")) is not bool:
        raise ValueError("学习通自动考试交卷开关需为布尔值")
    if value["enabled"]:
        try:
            parsed = urlparse(value["api_base"].strip())
            valid = parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("学习通自动考试 Base URL 必须是有效的 HTTP / HTTPS 地址")
        if not value["api_key"].strip():
            raise ValueError("启用学习通自动考试后请填写 API Key")
        if not value["model_name"].strip():
            raise ValueError("启用学习通自动考试后请填写模型名称")
    _number(value["timeout"], "学习通自动考试 AI 超时", 5, 300)
    _number(value["captcha_timeout"], "学习通自动考试验证码等待", 30, 1800, integer=True)
    _number(value["answer_delay"], "学习通自动考试作答间隔", 0, 10)
    _number(value.get("api_retries", DEFAULT_EXAM_AUTO["api_retries"]), "学习通自动考试 AI 重试次数", 0, 10, integer=True)
    _number(value.get("api_retry_backoff", DEFAULT_EXAM_AUTO["api_retry_backoff"]), "学习通自动考试 AI 重试间隔", 0.2, 30)
    value["api_retries"] = int(value.get("api_retries", DEFAULT_EXAM_AUTO["api_retries"]))
    value["api_retry_backoff"] = float(value.get("api_retry_backoff", DEFAULT_EXAM_AUTO["api_retry_backoff"]))


def validate_chaoxing(value: dict) -> None:
    if value.get("browser") not in ("msedge", "chrome", "chromium"):
        raise ValueError("学习通浏览器需填写 msedge、chrome 或 chromium")
    for key in ("save_session", "muted"):
        if type(value.get(key)) is not bool:
            raise ValueError("学习通开关需为布尔值")
    _number(value["login_timeout"], "学习通登录等待（秒）", 30, 1800, integer=True)
    _number(value["playback_rate"], "学习通播放速度", 0.5)
    _number(value["stall_timeout"], "学习通无进展等待（秒）", 30, 1800, integer=True)
    validate_exam_assist(value.get("exam_assist", DEFAULT_EXAM_ASSIST))
    validate_exam_auto(value.get("exam_auto", DEFAULT_EXAM_AUTO))

def validate_zhihuishu(value: dict) -> None:
    if not isinstance(value, dict):
        raise ValueError("智慧树配置必须是 JSON 对象")
    homework = value.get("homework", DEFAULT_ZHS_HOMEWORK)
    if not isinstance(homework, dict):
        raise ValueError("智慧树作业配置必须是 JSON 对象")
    if type(homework.get("enabled")) is not bool:
        raise ValueError("智慧树作业 AI 开关需为布尔值")
    if type(homework.get("auto_submit")) is not bool:
        raise ValueError("智慧树作业自动交卷开关需为布尔值")
    if type(homework.get("only_missing")) is not bool:
        raise ValueError("智慧树作业“仅补未作答”开关需为布尔值")
    if type(homework.get("overwrite_with_verified", True)) is not bool:
        raise ValueError("智慧树作业“用已验证答案覆盖旧答案”开关需为布尔值")
    for key in ("api_base", "api_key", "model_name"):
        if not isinstance(homework.get(key, ""), str):
            raise ValueError(f"智慧树作业的 {key} 必须是文本")
    if homework["enabled"] and homework.get("api_base", "").strip():
        try:
            parsed = urlparse(homework["api_base"].strip())
            valid = parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("智慧树作业 Base URL 必须是有效的 HTTP / HTTPS 地址")
    _number(homework["timeout"], "智慧树作业 AI 超时", 5, 300)
    _number(homework["answer_delay"], "智慧树作业作答间隔", 0, 10)
    _number(homework.get("api_retries", DEFAULT_ZHS_HOMEWORK["api_retries"]),
            "智慧树作业 AI 重试次数", 0, 10, integer=True)
    _number(homework.get("api_retry_backoff", DEFAULT_ZHS_HOMEWORK["api_retry_backoff"]),
            "智慧树作业 AI 重试间隔", 0.2, 30)
    homework["api_retries"] = int(homework.get("api_retries", DEFAULT_ZHS_HOMEWORK["api_retries"]))
    homework["api_retry_backoff"] = float(homework.get("api_retry_backoff", DEFAULT_ZHS_HOMEWORK["api_retry_backoff"]))
    value["homework"] = homework


DEFAULT_KETANGPAI = {
    "CONFIG_VERSION": 2, "WEBSOCKET_SERVER_PORT": 11451,
    "WEBSOCKET_ENABLE_TLS": False, "TLS_CERT_PATH": "", "TLS_KEY_PATH": "",
    "WEBSOCKET_SERVER_PATH": "", "ENABLE_TELEGRAM_BOT": False,
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": 0,
    "TELEGRAM_BOT_PRIVACY_MODE": True, "TELEGRAM_BOT_PRIVACY_MODE_WHITELIST": [],
    "TELEGRAM_BOT_REPLY_INVALID_QRCODE": False,
    "TELEGRAM_BOT_API_URL": "https://api.telegram.org", "USERS": [], "CLASSES": [],
    # Never submit an unrelated hard-coded location for attendance.
    "DEFAULT_LATITUDE": "", "DEFAULT_LONGITUDE": "",
    "签到_CHECK_INTERVAL_SECONDS": 15, "MIN_DELAY_SECONDS": 15, "MAX_DELAY_SECONDS": 45,
    "FAKE_IP_PREFIX": ["58.32.12.", "103.28.212.", "202.101.31.", "124.74.9."],
    "ENABLE_IGNORE_QRCODE_EXPIRE": False,
    "ENABLE_互动答题_CHECK": False, "互动答题_CHECK_INTERVAL_SECONDS": 15,
    "互动答题_CHECK_DELAY_SECONDS": 300,
}


def general_settings(config: dict) -> dict:
    values = {key: config[key] for key in DEFAULT_GENERAL if key in config}
    # The CLI also accepts null to use the system proxy.
    if values.get("proxies") is None:
        values.pop("proxies", None)
    if values.get("speed") == 0:
        values["speed"] = None
    result = merge_settings(DEFAULT_GENERAL, values)
    for service in ("pushplus", "bark"):
        if service not in config and config.get(f"{service}_token"):
            result[service] = {"enable": True, "token": config[f"{service}_token"]}
    return result


def ketangpai_settings(config: dict) -> dict:
    value = config.get("ketangpai", {})
    if not isinstance(value, dict):
        raise ValueError("课堂派配置必须是 JSON 对象")
    return merge_settings(DEFAULT_KETANGPAI, value)


def _number(value, label, minimum, maximum=None, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < minimum or (maximum is not None and value > maximum)
            or (integer and type(value) is not int)):
        raise ValueError(f"{label}数值无效，请检查取值范围")


def validate_general(value: dict) -> None:
    if type(value["save_cookies"]) is not bool:
        raise ValueError("保存登录状态必须为布尔值")
    if value["speed"] is not None:
        _number(value["speed"], "播放速度", 0.1)
    _number(value["threshold"], "完成阈值", 0.01)
    _number(value["limit"], "每日时限", 0, integer=True)
    for key in ("cookie_path", "image_path"):
        if not isinstance(value[key], str):
            raise ValueError("存储路径必须是文本")
    if not value["cookie_path"].strip():
        raise ValueError("请填写登录状态保存路径")
    proxies = value["proxies"]
    if not isinstance(proxies, dict):
        raise ValueError("代理配置必须是 JSON 对象")
    for key in ("http", "https"):
        url = proxies.get(key, "")
        try:
            valid = isinstance(url, str) and (not url or (
                urlparse(url).scheme in ("http", "https", "socks5", "socks5h") and urlparse(url).hostname))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("代理地址无效，请填写完整的 HTTP / HTTPS / SOCKS5 地址")
    for service in ("pushplus", "bark"):
        options = value[service]
        if (not isinstance(options, dict) or type(options.get("enable")) is not bool
                or not isinstance(options.get("token"), str)):
            raise ValueError(f"{service} 通知配置无效")
        if options["enable"] and not options["token"].strip():
            raise ValueError(f"请填写 {service} Token")


def validate_ketangpai(value: dict, *, ready: bool = False) -> None:
    if value.get("CONFIG_VERSION") != 2:
        raise ValueError("课堂派配置版本无效，请使用当前设置页重新保存")
    for key, default in DEFAULT_KETANGPAI.items():
        if type(default) in (bool, str, list) and type(value.get(key)) is not type(default):
            raise ValueError(f"课堂派 {key} 的类型无效")
    for key, minimum, maximum in (
        ("WEBSOCKET_SERVER_PORT", 1, 65535), ("签到_CHECK_INTERVAL_SECONDS", 1, None),
        ("MIN_DELAY_SECONDS", 0, None), ("MAX_DELAY_SECONDS", 0, None),
        ("互动答题_CHECK_INTERVAL_SECONDS", 1, None), ("互动答题_CHECK_DELAY_SECONDS", 0, None),
    ):
        _number(value[key], key, minimum, maximum, integer=True)
    if value["MIN_DELAY_SECONDS"] > value["MAX_DELAY_SECONDS"]:
        raise ValueError("最小签到延迟不能大于最大签到延迟")
    path = value["WEBSOCKET_SERVER_PATH"]
    if not path.startswith("/") or path == "/" or re.search(r"[\s?#]", path):
        raise ValueError("WebSocket 路径需以 / 开头，包含私密字符串且不能含空格、? 或 #")
    if value["WEBSOCKET_ENABLE_TLS"] and not all(value[k].strip() for k in ("TLS_CERT_PATH", "TLS_KEY_PATH")):
        raise ValueError("启用 TLS 后请填写证书和私钥路径")
    _number(value["TELEGRAM_CHAT_ID"], "Telegram Chat ID", -9007199254740991, 9007199254740991, integer=True)
    for chat in value["TELEGRAM_BOT_PRIVACY_MODE_WHITELIST"]:
        _number(chat, "Telegram 白名单 ID", -9007199254740991, 9007199254740991, integer=True)
    if value["ENABLE_TELEGRAM_BOT"]:
        if not value["TELEGRAM_BOT_TOKEN"].strip() or not value["TELEGRAM_CHAT_ID"]:
            raise ValueError("启用 Telegram 后请填写 Bot Token 和 Chat ID")
        try:
            parsed = urlparse(value["TELEGRAM_BOT_API_URL"])
            valid = parsed.scheme in ("http", "https") and parsed.hostname
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Telegram API URL 无效")
    def coordinates(latitude, longitude, optional=False):
        if optional and not latitude and not longitude:
            return
        try:
            _number(float(latitude), "纬度", -90, 90)
            _number(float(longitude), "经度", -180, 180)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("GPS 经纬度无效，纬度应为 -90~90，经度应为 -180~180") from None
    coordinates(value["DEFAULT_LATITUDE"], value["DEFAULT_LONGITUDE"], optional=True)
    for user in value["USERS"]:
        if not isinstance(user, dict) or any(not isinstance(user.get(k), str) or not user[k].strip()
                                               for k in ("friendlyName", "username", "password")):
            raise ValueError("课堂派账号需填写名称、用户名和密码")
    intervals = []
    for course in value["CLASSES"]:
        if not isinstance(course, dict) or any(not isinstance(course.get(k), str) or not course[k].strip()
                                                 for k in ("friendlyName", "classId")):
            raise ValueError("课程需填写名称和 Class ID")
        _number(course.get("dayOfWeek"), "星期", 0, 6, integer=True)
        times = []
        for key in ("startTime", "endTime"):
            time = course.get(key, "")
            if not isinstance(time, str) or not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", time):
                raise ValueError("课程时间需使用 24 小时制 HH:mm 格式")
            h, m = map(int, time.split(":"))
            times.append(h * 60 + m)
        start, end = times
        if start >= end:
            raise ValueError("课程结束时间需晚于开始时间，跨日课程请拆成两条")
        if any(day == course["dayOfWeek"] and start <= old_end and end >= old_start
               for day, old_start, old_end in intervals):
            raise ValueError("同一天的课程监测时间不能重叠")
        intervals.append((course["dayOfWeek"], start, end))
        coordinates(course.get("latitude", ""), course.get("longitude", ""), optional=True)
    prefixes = value["FAKE_IP_PREFIX"]
    if not prefixes or any(not isinstance(p, str) or not re.fullmatch(r"(?:\d{1,3}\.){3}", p)
                           or any(int(n) > 255 for n in p.rstrip(".").split(".")) for p in prefixes):
        raise ValueError("IP 前缀应为三个 0~255 数字段并以 . 结尾，每行一项")
    if len(prefixes) < len(value["USERS"]):
        raise ValueError("IP 前缀数量不能少于账号数量")
    if ready and (not value["USERS"] or not value["CLASSES"]):
        raise ValueError("请在设置 → 课堂派中至少添加一个账号和一条课程时间表并保存")


def merge_settings(base: dict, changes: dict) -> dict:
    result = deepcopy(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def ai_settings(config: dict) -> dict:
    ai = config.get("ai", {})
    if not isinstance(ai, dict):
        raise ValueError("config.json 的 ai 字段必须是 JSON 对象")
    return merge_settings(DEFAULT_AI, ai)


def validate_ai(ai: dict) -> None:
    """Validate active providers without including credentials in errors."""
    def boolean(obj, key):
        if type(obj.get(key)) is not bool:
            raise ValueError(f"AI 设置 {key} 必须为 true 或 false")

    def provider(obj, url_key, label, active):
        if not isinstance(obj, dict):
            raise ValueError(f"{label}配置必须为 JSON 对象")
        for key in (url_key, "api_key"):
            if not isinstance(obj.get(key), str):
                raise ValueError(f"{label}的 {key} 必须是文本")
        if active:
            try:
                parsed = urlparse(obj[url_key].strip())
                valid = (parsed.scheme in ("http", "https") and parsed.hostname
                         and not parsed.username and not parsed.password
                         and not parsed.query and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{label} Base URL 必须是有效的 HTTP / HTTPS 地址")
            if obj["api_key"].strip() in ("", "sk-"):
                raise ValueError(f"请填写{label} API Key")

    for key in ("enabled", "use_zhidao_ai", "use_stream"):
        boolean(ai, key)
    external = ai["enabled"] and not ai["use_zhidao_ai"]
    provider(ai.get("openai"), "api_base", "自定义 AI", external)
    model = ai["openai"].get("model_name")
    if not isinstance(model, str) or (external and not model.strip()):
        raise ValueError("请填写自定义 AI 的模型名称")
    ppt = ai.get("ppt_processing")
    if not isinstance(ppt, dict):
        raise ValueError("课件解析配置必须为 JSON 对象")
    boolean(ppt, "provide_to_ai")
    provider(ppt.get("moonShot"), "base_url", "Moonshot 课件解析",
             ai["enabled"] and ppt["provide_to_ai"])
    boolean(ppt["moonShot"], "delete_after_convert")


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_root() / "config.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            config = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("config.json 格式错误，请修复文件后重新载入；原文件未修改") from exc
        if not isinstance(config, dict):
            raise ValueError("config.json 必须是 JSON 对象；原文件未修改")
        return config

    def load(self) -> dict:
        config = self._read()
        ktp = ketangpai_settings(config)
        if not ktp["WEBSOCKET_SERVER_PATH"]:
            ktp["WEBSOCKET_SERVER_PATH"] = "/" + secrets.token_urlsafe(24)
        return {"ai": ai_settings(config), "general": general_settings(config),
                "ketangpai": ktp, "chaoxing": chaoxing_settings(config),
                "zhihuishu": zhihuishu_settings(config)}

    def save(self, settings: dict) -> dict:
        if not isinstance(settings, dict) or not settings or any(
                key not in ("ai", "general", "ketangpai", "chaoxing", "zhihuishu") or not isinstance(value, dict)
                for key, value in settings.items()):
            raise ValueError("设置必须包含平台或 AI 配置对象")
        config = self._read()
        if "ai" in settings:
            ai = merge_settings(ai_settings(config), settings["ai"])
            validate_ai(ai)
            config["ai"] = ai
        if "general" in settings:
            if any(k not in DEFAULT_GENERAL for k in settings["general"]):
                raise ValueError("通用配置含未知字段")
            general = merge_settings(general_settings(config), settings["general"])
            validate_general(general)
            config.update(general)
        if "ketangpai" in settings:
            ktp = merge_settings(ketangpai_settings(config), settings["ketangpai"])
            validate_ketangpai(ktp)
            config["ketangpai"] = ktp
        if "chaoxing" in settings:
            options = merge_settings(chaoxing_settings(config), settings["chaoxing"])
            validate_chaoxing(options)
            config["chaoxing"] = options
        if "zhihuishu" in settings:
            zhs = merge_settings(zhihuishu_settings(config), settings["zhihuishu"])
            validate_zhihuishu(zhs)
            config["zhihuishu"] = zhs
        # Keep CLI migrations enabled on an existing or newly created config.
        config.setdefault("config_version", "1.0.0")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".settings-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(config, stream, ensure_ascii=False, indent=4)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return self.load()
