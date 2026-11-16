#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io
import os
import sys
from requests.cookies import RequestsCookieJar, create_cookie
from datetime import timedelta


def _pil():
    """延迟导入 Pillow：只有终端里画二维码时才需要，桌面/桥接路径不再强制装 Pillow。"""
    try:
        from PIL import Image, ImageOps
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于运行环境
        raise ModuleNotFoundError(
            "终端展示二维码需要 Pillow（pip install pillow）；"
            "该依赖只用于 showImage，不影响接口调用与桌面端桥接。"
        ) from exc
    return Image, ImageOps




def HMS(*args, **kw):
    return str(timedelta(*args, **kw))

def strToClass(class_name: str, module: str="__main__"):
    return getattr(sys.modules[module], class_name)

def showImage(img, show_in_terminal=False, ensure_unicode=False):
    Image, ImageOps = _pil()
    if show_in_terminal:
        if ensure_unicode:
            terminalShowImage_unicode(img)
        else:
            terminalShowImage_tty(img)
    else:
        import threading
        img = Image.open(io.BytesIO(img))
        threading.Thread(target=img.show).run()
    print("Scan QR code")

def terminalShowImage_unicode(img):
    Image, ImageOps = _pil()
    img = Image.open(io.BytesIO(img))
    qr = img.resize((47,47), Image.Resampling.NEAREST)
    qr = ImageOps.grayscale(qr)
    chars = [bytes((code,)).decode("cp437") for code in (255, 223, 220, 219)]
    new_line = '\n'
    col, row = qr.size
    def getPos(x, y):
        below = (y+1 < row) and qr.getpixel((x, y+1)) < 128
        return (qr.getpixel((x, y)) < 128) + below*2
    qr_str = ""
    for i in range(0, row, 2):
        qr_str += chars[0]
        for j in range(col):
            qr_str += chars[getPos(j, i)]
        qr_str += chars[0] + new_line
    print(chars[0]*49)
    print(qr_str, end='')
    print(chars[0]*49)

def terminalShowImage_tty(img):
    Image, ImageOps = _pil()
    img = Image.open(io.BytesIO(img))
    qr = img.resize((47,47), Image.Resampling.NEAREST)
    qr = ImageOps.grayscale(qr)
    white = '\033[0;37;47m  '
    black = '\033[0;37;40m  '
    new_line = '\033[0m\n'
    col, row = qr.size
    qr_str = white * 49 + new_line
    for i in range(row):
        qr_str += white
        for j in range(col):
            qr_str += white if qr.getpixel((j, i)) > 128 else black
        qr_str += white + new_line
    qr_str += white * 49 + new_line
    print(qr_str)

def getDir():
    return os.path.realpath(os.environ.get("FRC_BACKEND_DATA_DIR") or os.path.dirname(__file__))

def getConfigPath():
    return os.path.join(getDir(), 'config.json')

def getRealPath(path: str):
    path = os.path.expanduser(path)
    path = os.path.expandvars(path)
    path = os.path.normpath(path)
    if path != os.path.abspath(path):
        path = os.path.join(getDir(), path)
    return os.path.realpath(path)

def versionCmp(v1:str, v2:str) -> int:
    v1 = v1.split('.')
    v2 = v2.split('.')
    for i1, i2 in zip(v1, v2):
        dt = int(i1) - int(i2)
        if dt:
            return dt
    return len(v1) - len(v2)

def wipeLine():
    try:
        width = os.get_terminal_size().columns
    except OSError:  # Fix for PyCharm in Windows
        width = 80
    print('\r' + ' ' * (width), end = '\r', flush=True)

def progressBar (iteration, total, prefix = '', suffix = '', decimals = 1,
                      length = None, fill = '#', progressbar_view:bool = True):
    """
    ### Call in a loop to create terminal progress bar
    * `iteration`   - Required  : current iteration (Int)
    * `total`       - Required  : total iterations (Int)
    * `prefix`      - Optional  : prefix string (Str)
    * `suffix`      - Optional  : suffix string (Str)
    * `decimals`    - Optional  : positive number of decimals in percent complete (Int)
    * `length`      - Optional  : character length of bar (Int)
    * `fill`        - Optional  : bar fill character (Str)
    """
    if not progressbar_view:
        return False
    if not length:
        try:
            length = os.get_terminal_size().columns - 4
        except OSError:  # Fix for PyCharm in Windows
            length = 76
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    prefix = f"{prefix} |"
    suffix = f"| {percent}% {suffix}"
    bar_len = length - len(prefix) - len(suffix)
    filled_len = int(bar_len * iteration // total)
    bar = fill * filled_len + ' ' * (bar_len - filled_len)
    print(f"\r{prefix}{bar}{suffix}", end = '\r')
    if iteration >= total:
        wipeLine()

# Converting requests.cookies.RequestCookieJar to list
def cookie_jar_to_list(cookie_jar: RequestsCookieJar):
    cookies_list = []
    for cookie in cookie_jar:
        cookie_dict = {
            'name': cookie.name,
            'value': cookie.value,
            'domain': cookie.domain,
            'port': cookie.port,
            'path': cookie.path,
            'expires': cookie.expires,
            'secure': cookie.secure,
            'rest': cookie.__dict__.get('_rest', {}),
            'version': cookie.version,
            'comment': cookie.comment,
            'comment_url': cookie.comment_url,
            'rfc2109': cookie.rfc2109,
            'discard': cookie.discard,
        }
        cookies_list.append(cookie_dict)
    return cookies_list

def get_cookie_value(cookies, name: str):
    """Read a cookie value from a RequestsCookieJar or a plain dict.

    A real browser session routinely carries the same cookie name on two
    domains at once (``CASLOGC`` on ``passport.zhihuishu.com`` *and* on
    ``.zhihuishu.com``).  ``RequestsCookieJar.__getitem__`` raises
    ``CookieConflictError`` in that situation, which used to abort the
    whole QR login with "There are multiple cookies with name, ...".
    Resolve the conflict deterministically instead of exploding:
    root domain (leading dot, e.g. ``.zhihuishu.com``) wins, then the
    longest domain, then the longest path.
    """
    if not cookies:
        return None
    if isinstance(cookies, dict):
        return cookies.get(name)
    try:
        return cookies[name]
    except KeyError:
        return None
    except Exception as exc:  # requests.cookies.CookieConflictError
        if type(exc).__name__ != "CookieConflictError":
            raise
    matches = [c for c in list(cookies) if getattr(c, "name", None) == name]
    if not matches:
        return None

    def _rank(cookie):
        domain = getattr(cookie, "domain", "") or ""
        path = getattr(cookie, "path", "") or ""
        return (domain.startswith("."), len(domain), len(path))

    return max(matches, key=_rank).value


# Converting list to requests.cookies.RequestCookieJar
def list_to_cookie_jar(cookies_list: list[dict]):
    cookie_jar = RequestsCookieJar()
    for cookie_dict in cookies_list:
        cookie = create_cookie(
            name=cookie_dict['name'],
            value=cookie_dict['value'],
            domain=cookie_dict.get('domain', ''),
            port=cookie_dict.get('port', None),
            path=cookie_dict.get('path', '/'),
            expires=cookie_dict.get('expires', None),
            secure=cookie_dict.get('secure', False),
            rest=cookie_dict.get('rest', {'HttpOnly': None}),
            version=cookie_dict.get('version', 0),
            comment=cookie_dict.get('comment', None),
            comment_url=cookie_dict.get('comment_url', None),
            rfc2109=cookie_dict.get('rfc2109', False),
            discard=cookie_dict.get('discard', True),
        )
        cookie_jar.set_cookie(cookie)
    return cookie_jar
