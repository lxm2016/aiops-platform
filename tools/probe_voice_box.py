# -*- coding: utf-8 -*-
"""电话告警盒子(告警服务模块) 接口探测器 —— 深度版

适用: 融智云物联 MFM-920 系列「智能告警服务系统」等同类设备。
厂商一般不公开文档, 但这类设备的 WEB 管理页面本身就是调 HTTP 接口驱动的 ——
把首页和它的 JS 抓下来, 就能把真实接口路径扒出来, 比翻文档快。

**零依赖**: 只用 Python 标准库, 任意机器的 python3 都能跑。

------------------------------------------------------------------
用法
------------------------------------------------------------------
  # 基础探测(只扫默认端口, 不拨号, 安全)
  python3 probe_voice_box.py --ip 172.16.0.214

  # 深度探测 ★推荐★ : 多端口 + HTTPS + 登录 + 跟随链接 + TCP/Modbus 探测
  python3 probe_voice_box.py --ip 172.16.0.214 --deep

  # 深度 + 带账号密码登录(页面要登录才看得到接口时)
  python3 probe_voice_box.py --ip 172.16.0.214 --deep --user admin --pass admin

  # ★最有效★ 浏览器自己登录, 把 Cookie 复制给脚本(绕开一切登录难题)
  #   浏览器 F12 → Network → 点任意请求 → Request Headers → 复制 cookie 整行
  python3 probe_voice_box.py --ip 172.16.0.214 --deep --cookie "PHPSESSID=abc123"

  # 只探测某个 TCP 端口的原始响应(如 TCP Client API 8000)
  python3 probe_voice_box.py --ip 172.16.0.214 --tcp 8000

  # 确认接口后用真实号码打一次(会真的打电话)
  python3 probe_voice_box.py --ip 172.16.0.214 --call --path /api/xxx --phone 13800138000

------------------------------------------------------------------
输出解读
------------------------------------------------------------------
  [1] 每个端口返回什么 —— 确认 IP/端口对不对、要不要登录
  [2] 登录探测 —— 页面要登录时自动尝试常见账号, 并打印表单字段名
  [3] 抓页面/内联脚本/JS —— 提取真实接口路径, 并推断 WEB 根目录
  [4] 扫 WEB 根目录下的管理页面 —— 登录后的菜单页里常藏着接口地址
  [5] 候选接口探测 —— 逐个试, 自动忽略"返回内容与首页相同的兜底路由"
  [6] TCP 原始探测 —— 看 8000 这类端口到底吐什么字节
  [7] 结论与下一步
"""
import argparse
import http.cookiejar
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ---------------------------------------------------------------------------
# 零依赖 HTTP: 标准库 urllib + Cookie + 关掉系统代理 + 关证书校验
# ---------------------------------------------------------------------------
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),                      # 不用系统代理(内网地址会被劫走)
    urllib.request.HTTPCookieProcessor(_JAR),             # 支持登录后带会话
    urllib.request.HTTPSHandler(context=_SSL_CTX),
)

DEFAULT_PORTS = [80, 8080, 8000, 8001, 8888, 5000, 502, 443]
COMMON_CREDS = [("admin", "admin"), ("admin", "123456"), ("admin", "admin123"),
                ("admin", "1234"), ("admin", "888888"), ("user", "user"),
                ("admin", "password"), ("root", "root")]


class Resp:
    def __init__(self, code, text, url, headers=None):
        self.status_code = code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def hdr(self, name):
        try:
            return self.headers.get(name) or ""
        except Exception:
            return ""


def decode_bytes(b: bytes) -> str:
    """国产设备 WEB 页面常用 GBK, 依次尝试。"""
    for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "ignore")


def request(url, method="GET", data=None, timeout=8, headers=None):
    """失败/404 都返回 Resp, 不抛异常, 方便批量探测。"""
    try:
        hd = {"User-Agent": UA}
        hd.update(_HEADERS)          # 命令行传进来的 Cookie / Token
        if headers:
            hd.update(headers)
        if method == "POST" and data is not None:
            body = urllib.parse.urlencode(data).encode()
            hd.setdefault("Content-Type", "application/x-www-form-urlencoded")
            req = urllib.request.Request(url, data=body, method="POST", headers=hd)
        else:
            req = urllib.request.Request(url, method=method, headers=hd)
        with _OPENER.open(req, timeout=timeout) as r:
            return Resp(r.status, decode_bytes(r.read(65536)), url, r.headers)
    except urllib.error.HTTPError as e:
        try:
            txt = decode_bytes(e.read(4096))
        except Exception:
            txt = ""
        return Resp(e.code, txt, url, getattr(e, "headers", None))
    except Exception as e:
        return Resp(-1, f"{type(e).__name__}: {e}", url)


def joinurl(base: str, path: str) -> str:
    return urllib.parse.urljoin(base + "/", path.lstrip("/"))


def check_url(url: str) -> bool:
    """防呆: Git Bash/MSYS 会把 --path /xxx 转成 Windows 本地路径。"""
    if not url.startswith(("http://", "https://")):
        print(f"  ✗ 接口地址拼出来不是 http 地址: {url}")
        print("    若你在 Git Bash / Cygwin 下执行, 路径别写成 / 开头,")
        print("    或加环境变量 MSYS_NO_PATHCONV=1")
        return False
    return True


def title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return m.group(1).strip()[:80] if m else ""


def section(n, t):
    print(f"\n{'=' * 68}\n[{n}] {t}\n{'=' * 68}")


# ---------------------------------------------------------------------------
# 页面/JS 里的接口路径
# ---------------------------------------------------------------------------
CANDIDATE_PATHS = [
    "/", "/index.html", "/api", "/api/", "/api/version", "/api/status", "/api/info",
    "/api/call", "/api/call/send", "/api/voice", "/api/voice/call",
    "/api/phone", "/api/phone/call", "/api/dial", "/api/tts",
    "/call", "/voice", "/dial",
    "/api/alarm", "/api/alarm/send", "/api/alarm/trigger", "/api/alarm/call",
    "/alarm", "/alarm/send", "/alarm/call", "/trigger",
    "/api/sms", "/api/sms/send", "/sms", "/sms/send", "/api/send",
    "/api/notify", "/notify", "/send", "/api/push", "/api/message",
    "/cmd", "/api/cmd",
    # 融智云这类盒子实测走 /cgi-bin/ 目录
    "/cgi-bin/", "/cgi-bin/about", "/cgi-bin/oem.cfg", "/cgi-bin/info",
    "/cgi-bin/status", "/cgi-bin/version", "/cgi-bin/control.php",
    "/cgi-bin/msg_log.php", "/cgi-bin/system.php", "/cgi-bin/system_set.php",
    "/cgi-bin/system_status.php", "/cgi-bin/call.php", "/cgi-bin/phone.php",
    "/cgi-bin/alarm.php", "/cgi-bin/sms.php", "/cgi-bin/tts.php",
    "/cgi-bin/voice.php", "/cgi-bin/dial.php", "/cgi-bin/send.php",
    "/cgi-bin/notify.php", "/cgi-bin/play.php", "/cgi-bin/record.php",
    "/cgi-bin/log.php", "/cgi-bin/test.php", "/cgi-bin/restart",
    "/cgi_bin/expire", "/cgi_bin/about", "/2/oem.cfg", "/login", "/logout",
    "/2/HTML/control_panel/control_panel_menu.cfg",
    "/2/HTML/control_panel/msg_log_menu.cfg",
    "/2/HTML/control_panel/system_setting_menu.cfg",
    # 文档/接口说明页(厂商常内置)
    "/api/doc", "/api-docs", "/doc", "/docs", "/swagger", "/swagger.json",
    "/help", "/readme", "/README.html", "/api.html", "/interface.html",
]

JS_URL_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+\.js(?:\?[^"']*)?)["']""", re.I)
LINK_RE = re.compile(r"""<a[^>]+href\s*=\s*["']([^"'#]+)["']""", re.I)
SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.I | re.S)

# 静态资源后缀: 提取路径时用来区分"接口"和"文件"
STATIC_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
              ".woff", ".woff2", ".ttf", ".eot", ".map", ".mp3", ".wav")

# 登录后管理页面常见文件名 —— 接口调用就写在这些页面里
PAGE_NAMES = [
    "login.html", "index.html", "main.html", "home.html", "frame.html",
    "alarm.html", "alarmList.html", "alarmlist.html", "alarm_set.html",
    "call.html", "phone.html", "voice.html", "sms.html", "message.html",
    "set.html", "setting.html", "config.html", "sys.html", "system.html",
    "test.html", "log.html", "record.html", "user.html", "help.html",
    "about.html", "api.html", "interface.html", "doc.html",
]

# 接口目录下常见的接口名 —— 已知 /cgi-bin/ 时, 拿这批名字去枚举最省力
# (前 10 个是从真实设备 Network 面板里看到的)
API_NAMES = [
    "about", "oem.cfg", "control.php", "msg_log.php", "system.php",
    "system_set.php", "system_status.php", "system_time.php", "network.php",
    "call.php", "phone.php", "tel.php", "dial.php", "voice.php", "tts.php",
    "alarm.php", "sms.php", "message.php", "send.php", "notify.php",
    "play.php", "audio.php", "record.php", "log.php", "history.php",
    "contact.php", "group.php", "user.php", "config.php", "setting.php",
    "status.php", "state.php", "info.php", "version.php", "reboot.php",
    "test.php", "file.php", "upload.php", "action.php", "cmd.php", "do.php",
    "api.php", "operate.php", "task.php", "event.php", "relay.php", "io.php",
    "call", "phone", "alarm", "sms", "dial", "tts", "voice", "send",
    "notify", "status", "control", "setting", "config", "log", "test",
    # 从真实设备(EdgeOS v3.7.3 / MFM-920E)上扒到的命名习惯
    "restart", "expire", "login", "logout", "menu.cfg", "oem.cfg",
    "dialout", "callout", "outcall", "autocall", "playvoice", "sendmsg",
    "sendsms", "phone_book.php", "contact.php", "msg.php", "signal.php",
    "sim.php", "relay.php", "io.php", "voice_file.php", "tts_play.php",
]

# 这台设备"路径不存在"返回 500 而不是 404 —— 两种都当"没有"
NOT_FOUND_CODES = {404, 500, 501, 400}


def norm_path(p: str) -> str:
    """统一成 / 开头的绝对路径: ./2/oem.cfg -> /2/oem.cfg ; cgi-bin/restart -> /cgi-bin/restart"""
    p = (p or "").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p:
        return p
    if not p.startswith("/") and not p.startswith(("http://", "https://")):
        p = "/" + p
    return p

# 运行时附加请求头(登录 Cookie / Token 等)
_HEADERS: dict = {}


def decode_auth_cookie(cookie: str) -> str:
    """auth=YWRtaW46YWRtaW4%3D -> admin:admin

    这类盒子的会话 Cookie 就是 base64(账号:密码) 再 URL 编码 —— 意味着**不用登录**,
    平台自己就能算出这个 Cookie 直接带上。这里解码出来给用户确认账号。
    """
    if not cookie:
        return ""
    m = re.search(r"auth=([^;\s]+)", cookie, re.I)
    if not m:
        return ""
    raw = urllib.parse.unquote(m.group(1))
    try:
        import base64
        s = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "ignore")
    except Exception:
        return ""
    return s if ":" in s else ""


def sniff_prefixes(paths, html="") -> list:
    """统计"接口目录"前缀: /cgi-bin/about -> /cgi-bin/

    这台设备同时存在 /cgi-bin/ 和 /cgi_bin/(下划线, 设备自己拼错了) 两种写法,
    两个都要试。另外 cgi/api/cmd 这类目录优先于页面目录(如 /2/)。
    """
    cnt = {}
    cand = list(paths) + re.findall(r"""["'](/[A-Za-z0-9_\-]+/)""", html or "")
    for p in cand:
        seg = norm_path(p).split("?")[0]
        if is_static(seg):
            continue
        parts = [x for x in seg.split("/") if x]
        if len(parts) >= 2:
            key = f"/{parts[0]}/"
            cnt[key] = cnt.get(key, 0) + 1
            # 接口目录下还有子目录(如 /cgi-bin/control_panel/call), 也单独记一份
            if len(parts) >= 3 and re.search(r"cgi[-_]?bin|api|cmd|action", parts[0], re.I):
                k2 = f"/{parts[0]}/{parts[1]}/"
                cnt[k2] = cnt.get(k2, 0) + 1

    def rank(k):
        return (0 if re.search(r"cgi[-_]?bin|api|cmd|action", k, re.I) else 1, -cnt[k])

    out = sorted(cnt, key=rank)[:2]
    for must in ("/cgi-bin/", "/cgi_bin/"):        # 两种拼写都补上
        if must not in out:
            out.insert(0, must)
    return out[:3]


def add_headers(spec_cookie=None, spec_header=None):
    """把命令行传的 Cookie / Header 解析进全局请求头。"""
    if spec_cookie:
        _HEADERS["Cookie"] = spec_cookie.strip()
    if spec_header:
        for item in spec_header:
            if ":" in item:
                k, v = item.split(":", 1)
                _HEADERS[k.strip()] = v.strip()
    if _HEADERS:
        print(f"已附加请求头: {list(_HEADERS.keys())}")


def extract_paths(text: str) -> set:
    """从 HTML/JS 里尽可能捞出所有路径。

    之前的正则只认带 api/call/alarm 等关键词的路径, 结果这台设备一个都没命中 ——
    登录接口和业务接口的名字往往很朴素(如 /2/login、/setting/getAlarm),
    所以这里放宽到"所有引号内的绝对路径", 再靠静态后缀过滤掉资源文件。
    """
    if not text:
        return set()
    paths = set()
    patterns = [
        r"""url\s*[:=]\s*["']([^"']+)["']""",              # datagrid/ ajax 的 url
        r"""\$\.(?:post|get|ajax)\s*\(\s*["']([^"']+)["']""",
        r"""action\s*=\s*["']([^"']+)["']""",                # form action
        r"""(?:href|src)\s*=\s*["']([^"']+)["']""",
        r"""["'](\.?/(?:[A-Za-z0-9_\-./]|%[0-9A-Fa-f]{2})+)["']""",  # 引号内的绝对路径(含 ./ 开头)
        r"""(?:open|send)\s*\(\s*["']([A-Za-z]*\s*/[^"']+)["']""",  # XHR open
    ]
    for rx in patterns:
        for m in re.findall(rx, text, re.I):
            m = (m or "").strip()
            if not m or len(m) > 90:
                continue
            if m.startswith(("http://", "https://", "//", "javascript:", "mailto:",
                             "data:", "#", "{", "?")):
                continue
            paths.add(norm_path(m))
    return paths


def is_static(p: str) -> bool:
    return p.lower().split("?")[0].endswith(STATIC_EXT)


# 名字像"发消息/打电话"的接口 —— 命中就把上下文代码打出来
HOT_RE = re.compile(r"call|send|msg|notify|phone|sms|tts|voice|dial|alarm|contact", re.I)


def show_context(body: str, pat: str, width=300) -> list:
    """把接口在页面代码里出现处的上下文抠出来 —— 参数名一眼就能看到。"""
    out = []
    for m in re.finditer(pat, body or ""):
        s = max(0, m.start() - width)
        e = min(len(body), m.end() + width)
        frag = re.sub(r"\s+", " ", body[s:e]).strip()
        out.append(frag)
    return out[:3]


def guess_web_root(js_urls) -> str:
    """从 JS 路径猜 WEB 根目录: http://ip/2/HTML/js/jquery.js -> /2/HTML/"""
    for u in js_urls:
        path = urllib.parse.urlparse(u).path
        i = path.lower().find("/js/")
        if i >= 0:
            return path[:i + 1]            # /2/HTML/
        d = path.rsplit("/", 1)[0]
        if d:
            return d + "/"
    return "/"


def looks_like_login(html: str) -> bool:
    if re.search(r"type\s*=\s*[\"']password[\"']", html or "", re.I):
        return True
    return bool(re.search(r"(登录|登陆|login|signin)", html or "", re.I)
                and re.search(r"<form", html or "", re.I))


def parse_login_form(html: str):
    """返回 (form_action, [字段名...], hidden字段dict)。"""
    for fm in re.findall(r"<form[^>]*>(.*?)</form>", html or "", re.I | re.S):
        if not re.search(r"type\s*=\s*[\"']password[\"']", fm, re.I):
            continue
        action_m = re.search(r"""<form[^>]+action\s*=\s*["']([^"']+)["']""", html, re.I)
        fields, hidden = [], {}
        for tag in re.findall(r"<input[^>]*>", fm, re.I):
            attrs = dict(re.findall(r"""(\w+)\s*=\s*["']([^"']*)["']""", tag))
            name = attrs.get("name")
            if not name:
                continue
            fields.append(name)
            if (attrs.get("type") or "").lower() == "hidden":
                hidden[name] = attrs.get("value", "")
        return (action_m.group(1) if action_m else ""), fields, hidden
    return "", [], {}


def try_login(base, html, user, pwd, timeout, user_field=None, pass_field=None):
    """尝试登录 WEB 页面。需要账号时先探测表单字段名。"""
    action, fields, hidden = parse_login_form(html)
    if not fields:
        print("    未找到密码表单, 跳过登录")
        return False
    print(f"    表单字段: {fields}")
    target = joinurl(base, action) if action else base + "/"
    if not check_url(target):
        return False
    uf = user_field or next((f for f in fields
                             if re.search(r"user|name|account|admin", f, re.I)), fields[0])
    pf = pass_field or next((f for f in fields
                             if re.search(r"pass|pwd|secret", f, re.I)),
                            fields[-1] if len(fields) > 1 else fields[0])
    data = dict(hidden)
    data[uf] = user
    data[pf] = pwd
    r = request(target, "POST", data, timeout=timeout)
    ok = (r.status_code < 400 and not looks_like_login(r.text)
          and (r.hdr("Set-Cookie") or "logout" in r.text.lower()
               or "退出" in r.text or r.status_code == 302))
    print(f"    尝试 {user}/{pwd} -> HTTP {r.status_code} {'成功' if ok else '失败'}")
    return ok



def collect_links(html, base, limit=25):
    urls = []
    for href in LINK_RE.findall(html or ""):
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        u = joinurl(base, href)
        if u.startswith(("http://", "https://")) and u not in urls:
            urls.append(u)
    return urls[:limit]


def probe_port(ip, port, timeout):
    """探测单个端口的 http / https, 返回 (Resp, base_url)。"""
    out = []
    for scheme in ("http", "https"):
        base = f"{scheme}://{ip}:{port}"
        r = request(base + "/", timeout=timeout)
        out.append((base, r))
        if r.status_code >= 200 and r.status_code < 400:
            return base, r, out
    return None, out[0][1], out


def tcp_probe(ip, port, timeout=5, send=None):
    """原始 TCP 探测: 连上后读设备主动发来的字节(banner)。"""
    print(f"  TCP {ip}:{port} ...")
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
    except Exception as e:
        print(f"    ✗ 连接失败: {type(e).__name__}: {e}")
        return
    print("    ✓ 端口开放")
    try:
        if send:
            payload = (send.encode() if isinstance(send, str) else send)
            print(f"    发送: {payload!r}")
            s.sendall(payload)
        s.settimeout(timeout)
        data = s.recv(1024)
        if data:
            print(f"    收到 {len(data)} 字节:")
            print(f"      hex: {data.hex(' ')[:200]}")
            try:
                print(f"      文本: {decode_bytes(data)[:200]!r}")
            except Exception:
                pass
        else:
            print("    连接后无数据返回(设备在等我们发东西, 需要协议文档)")
    except socket.timeout:
        print(f"    等待 {timeout} 秒无响应(设备不主动推送, 需按其协议发包)")
    except Exception as e:
        print(f"    读取失败: {type(e).__name__}: {e}")
    finally:
        try:
            s.close()
        except Exception:
            pass


def modbus_probe(ip, port=502, timeout=5):
    """发一条标准 Modbus TCP 读保持寄存器请求, 看设备答不答。"""
    print(f"  Modbus TCP {ip}:{port} ...")
    req = bytes([0x00, 0x01, 0x00, 0x00, 0x00, 0x06, 0x01, 0x03, 0x00, 0x00, 0x00, 0x02])
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
    except Exception as e:
        print(f"    ✗ 连接失败(大概率没开 Modbus TCP): {type(e).__name__}: {e}")
        return
    try:
        s.sendall(req)
        s.settimeout(timeout)
        data = s.recv(256)
        if data and len(data) >= 8 and data[2] == 0 and data[3] == 0:
            print(f"    ✓ 收到标准 Modbus 响应: {data.hex(' ')[:120]}")
            print("      → 这台设备支持 Modbus TCP, 可用写寄存器触发预制告警")
        elif data:
            print(f"    收到非标准响应: {data.hex(' ')[:120]}")
        else:
            print("    无响应")
    except socket.timeout:
        print(f"    等待 {timeout} 秒无响应")
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
def deep(ip, ports, user, pwd, timeout, user_field, pass_field):
    section(1, "逐个端口探测(找 WEB 管理页在哪)")
    print(f"  {'地址':<32} {'状态':<6} {'Server':<16} 标题 / 说明")
    print("  " + "-" * 66)
    best_base, best_resp = None, None
    http_ok_ports = []
    for p in ports:
        base, r, _ = probe_port(ip, p, timeout)
        if r.status_code < 0:
            print(f"  {f'http://{ip}:{p}':<32} {'--':<6} {'':<16} 无响应(端口关闭或被防火墙拦)")
            continue
        http_ok_ports.append(p)
        note = title_of(r.text) or (r.text[:40].replace("\n", " "))
        extra = "  ← 要登录" if looks_like_login(r.text) else ""
        print(f"  {base:<32} {r.status_code:<6} {r.hdr('Server')[:15]:<16} {note[:40]}{extra}")
        if base and best_base is None:
            best_base, best_resp = base, r

    if not best_base:
        print("\n  所有端口都不通。先确认: ping 盒子 IP 通不通? 防火墙有没有放通?")
        print("  设备 IP 可在告警软件的「本地网络」页面查看。")
        return

    base, html = best_base, best_resp.text
    print(f"\n  选定: {base}  (标题: {title_of(html) or '(无)'})")

    # ------------------------------------------------------------------
    section(2, "是否需要登录")
    logged = bool(_HEADERS.get("Cookie"))
    if logged:
        print("  已通过 --cookie 带入会话, 视为已登录, 跳过自动登录。")
        html = request(base + "/", timeout=timeout).text
        print(f"  带 Cookie 取首页 -> 标题: {title_of(html) or '(无)'}")
    if looks_like_login(html) and not logged:
        print("  页面要求登录。接口路径通常只在登录后才能看到。")
        if user and pwd:
            logged = try_login(base, html, user, pwd, timeout, user_field, pass_field)
        else:
            print("  未提供账号密码, 自动尝试常见默认账号:")
            for u, p in COMMON_CREDS:
                if try_login(base, html, u, p, timeout, user_field, pass_field):
                    logged = True
                    print(f"    ✓ 默认账号可用: {u} / {p}")
                    break
        if logged:
            html = request(base + "/", timeout=timeout).text
            print(f"  登录后标题: {title_of(html) or '(无)'}")
        else:
            print("\n  ✗ 登录失败。请这样拿到账号:")
            print("    1) 看设备说明书/机身上的标签;")
            print("    2) 用 --user-field/--pass-field 指定表单字段名")
            print("       (上面已经打印了表单字段, 把它告诉我即可);")
            print("    3) 直接问厂商。")
            print("    未登录也能继续扫, 但可能扫不到接口。")
    elif not logged:
        print("  页面不需要登录(或返回的是非 HTML 内容)。")

    # ------------------------------------------------------------------
    section(3, "抓页面 + 内联脚本 + 引用的 JS, 提取接口路径")
    actions = re.findall(r"""<form[^>]+action\s*=\s*["']([^"']+)["']""", html, re.I)
    if actions:
        print("  HTML 表单 action(往往直接就是接口):")
        for a in dict.fromkeys(actions):
            print(f"    - {a}")

    def harvest(text, tag=""):
        """从一段 HTML/JS 里捞接口路径(自动过滤静态资源)。"""
        got = set()
        for p in extract_paths(text):
            if is_static(p):
                continue
            got.add(p)
        if tag and got:
            print(f"    {tag}: 命中 {len(got)} 个路径")
        return got

    found = set()
    # 3.1 当前页面自己的内联 <script>(登录接口常写在这里)
    inline = "\n".join(SCRIPT_RE.findall(html or ""))
    if inline.strip():
        print(f"  内联 <script> 共 {len(inline)} 字节, 从中提取:")
        for p in sorted(harvest(inline))[:40]:
            print(f"      → {p}")
        found |= harvest(inline)
    found |= harvest(html)

    # 3.2 页面引用的 JS
    js_urls = [joinurl(base, u) for u in JS_URL_RE.findall(html)]
    for extra in ("/js/main.js", "/static/js/app.js", "/js/app.js", "/main.js",
                  "/js/common.js", "/script/main.js"):
        js_urls.append(base + extra)
    js_urls = list(dict.fromkeys(js_urls))

    # 3.3 从 JS 路径反推 WEB 根目录(如 http://ip/2/HTML/js/x.js -> /2/HTML/)
    root = guess_web_root(js_urls)
    print(f"  推断 WEB 根目录: {root}   (依据页面引用的 JS 路径)")
    for sub in ("js", "script", "static/js", "HTML/js"):
        for nm in ("main.js", "app.js", "common.js", "login.js", "alarm.js", "api.js"):
            if not root.endswith(f"/{sub}/"):
                js_urls.append(base + f"{root}{sub}/{nm}")

    seen_js, done_js = 0, set()
    for u in js_urls[:30]:
        if u in done_js:
            continue
        done_js.add(u)
        rr = request(u, timeout=timeout)
        if rr.status_code != 200 or not rr.text.strip():
            continue
        if rr.text.strip() == (html or "").strip():
            continue
        seen_js += 1
        got = harvest(rr.text)
        if got:
            print(f"    JS {u} ({len(rr.text)} 字节)")
            for p in sorted(got)[:25]:
                print(f"      → {p}")
        found |= got
    print(f"  共读取 {seen_js} 个 JS 文件")

    # ------------------------------------------------------------------
    section(4, "扫 WEB 根目录下的管理页面(登录后才能真正看到接口)")
    # 根目录 /2/HTML/ 这类路径是页面自己暴露的, 里面通常还有 alarm.html / set.html 等
    page_urls = []
    for pref in dict.fromkeys([root, "/", f"{root}HTML/"]):
        for nm in PAGE_NAMES:
            page_urls.append(base + (pref if pref.endswith("/") else pref + "/") + nm)
    links = collect_links(html, base)
    page_urls = list(dict.fromkeys(links + page_urls))

    hit_pages = []
    for u in page_urls[:60]:
        rr = request(u, timeout=timeout)
        if rr.status_code != 200:
            continue
        body = (rr.text or "").strip()
        if not body or body == (html or "").strip():
            continue
        if looks_like_login(body) and not logged:
            continue        # 没登录, 拿到的是登录页, 没价值
        got = harvest(body)
        tag = ""
        if re.search(r"(接口|开发|api|doc|说明|协议)", body, re.I):
            tag = " ★含接口/文档字样"
        print(f"    {u}  ({len(body)} 字节, 路径 {len(got)} 个){tag}")
        hit_pages.append(u)
        found |= got
        if got:
            for p in sorted(got)[:25]:
                print(f"      → {p}")
    if not hit_pages:
        print("  没读到任何管理页面(页面名不在常见列表里, 或设备是单页应用)。")
        if not logged:
            print("  这时最有效的办法: 自己在浏览器里登录, 然后把 Cookie 复制过来:")
            print("    F12 → Network → 选任意一条请求 → Request Headers → 复制 cookie 那一行")
            print(f'    python3 {sys.argv[0]} --ip {ip} --deep --cookie "PHPSESSID=xxxxxxxx"')
        else:
            print("  不影响: 接口路径主要来自上面的页面/JS 提取和下一步的目录枚举。")

    # 4b 顺着菜单 cfg 里的 html_url 找到真实管理页, 再从中捞接口
    # 这台设备把菜单结构放在 *_menu.cfg 里, 里面的 html_url 才是真正的功能页
    cfg_hits = [p for p in found if p.lower().endswith(".cfg")]
    if cfg_hits:
        print("\n  顺着菜单 cfg 找真实功能页:")
        menu_pages = []
        for p in sorted(cfg_hits):
            rr = request(joinurl(base, p), timeout=timeout)
            if rr.status_code != 200:
                continue
            urls = re.findall(r'"html_url"\s*:\s*"([^"]+)"', rr.text)
            print(f"    {p}  ->  {len(urls)} 个菜单项")
            for h in urls:
                menu_pages.append(norm_path(h))
        for p in list(dict.fromkeys(menu_pages))[:40]:
            rr = request(joinurl(base, p), timeout=timeout)
            if rr.status_code != 200:
                continue
            body = (rr.text or "")
            if not body.strip() or body.strip() == (html or "").strip():
                continue
            got = harvest(body)
            hotspot = " ★" if re.search(r"call|phone|alarm|sms|tts|voice|dial|send|msg|notify",
                                        p, re.I) else ""
            print(f"    {p}  ({len(body)} 字节, 路径 {len(got)} 个){hotspot}")
            for x in sorted(got)[:20]:
                print(f"      → {x}")
            found |= got

            # 重点: 把"发消息/打电话"类接口的调用代码抠出来, 直接看到参数名
            for nm in sorted(got):
                base_nm = nm.rsplit("/", 1)[-1].split("?")[0]
                if not HOT_RE.search(base_nm) or not base_nm:
                    continue
                for frag in show_context(body, re.escape(base_nm)):
                    print(f"      【{base_nm} 调用上下文】")
                    print(f"      {frag[:600]}")

    if found:
        print("\n  汇总发现的接口路径:")
        for p in sorted(found):
            print(f"    → {p}")
    else:
        print("  未在页面/JS 中提取到接口路径。")

    # ------------------------------------------------------------------
    section(5, "枚举接口目录下的接口名(已知 /cgi-bin/ 时这步最出活)")
    prefixes = sniff_prefixes(found, html)
    if not prefixes and re.search(r"/cgi-bin/", html or ""):
        prefixes = ["/cgi-bin/"]
    if not prefixes:
        prefixes = ["/cgi-bin/", "/cgi/"]     # 这类设备最常见, 盲试一次
    print(f"  待枚举的目录: {prefixes}")
    enum_found = []
    for pref in prefixes:
        for nm in API_NAMES:
            u = base + pref + nm
            rr = request(u, timeout=min(timeout, 4))
            if rr.status_code < 0 or rr.status_code in NOT_FOUND_CODES:
                continue
            body = (rr.text or "").strip()
            if body and body == (html or "").strip():
                continue
            if rr.status_code == 200 and (
                    body.startswith(("{", "[")) or "cgi" not in body.lower()[:200]):
                enum_found.append(pref + nm)
                mark = "★" if re.search(r"call|phone|alarm|sms|tts|voice|dial|send|msg",
                                        nm, re.I) else " "
                print(f"  {mark} {pref + nm:<26} {rr.status_code}  "
                      f"{body.replace(chr(10), ' ')[:70]}")
    if enum_found:
        print(f"\n  目录枚举命中 {len(enum_found)} 个: {', '.join(sorted(enum_found))}")
        found |= set("/" + x.lstrip("/") for x in enum_found)
    else:
        print("  目录枚举没命中(设备可能不允许未带参数的 GET, 或接口名不在常见列表里)。")

    # ------------------------------------------------------------------
    section(6, "逐个探测候选接口(GET, 不带号码, 不会拨号)")
    root_body = (html or "").strip()
    print(f"  {'路径':<28} {'状态':<6} 响应片段")
    print("  " + "-" * 62)
    alive, tried = [], set()
    for p in CANDIDATE_PATHS + sorted(found):
        if len(p) > 70:
            continue
        key = p.split("?")[0]
        if key in tried:
            continue
        tried.add(key)
        rr = request(joinurl(base, p), timeout=timeout)
        if rr.status_code in NOT_FOUND_CODES or rr.status_code < 0:
            continue
        text = (rr.text or "").strip()
        if p != "/" and text and text == root_body:
            continue
        mark = "✓" if rr.status_code < 400 else " "
        print(f"  {mark} {p:<26} {rr.status_code:<6} {text.replace(chr(10), ' ')[:80]}")
        if rr.status_code < 400 and p != "/":
            alive.append(p)

    # ------------------------------------------------------------------
    section(7, "TCP 原始探测(看端口开了但 HTTP 不通的到底吐什么)")
    # 只对"HTTP 没响应"的端口做原始 TCP 探测 —— 这些才藏着非 HTTP 协议
    silent = [p for p in ports if p not in http_ok_ports]
    if not silent:
        print("  所有被扫端口都返回了 HTTP 响应, 无需要做原始 TCP 探测")
    for p in silent[:5]:
        tcp_probe(ip, p, timeout=5)
    if 502 in ports or 502 in silent:
        modbus_probe(ip, 502, timeout=5)

    # ------------------------------------------------------------------
    section(8, "结论与下一步")
    if alive:
        print("  以下路径在设备上存在, 很可能是接口:")
        for p in dict.fromkeys(alive):
            print(f"    {joinurl(base, p)}")
        # 挑一个最像"拨号/发短信"的作为推荐接口
        hot = [p for p in alive if re.search(
            r"call|phone|tel|alarm|sms|tts|voice|dial|send|msg_send|notify|notice", p, re.I)]
        pick = hot[0] if hot else alive[0]
        print("\n  下一步: 确认参数名(号码: phone/mobile/tel/called? 内容: text/content/msg/tts?)")
        print("  然后到平台「告警配置 → 通知渠道 → 电话告警盒子」里填:")
        print(f"    接口地址  : {joinurl(base, pick)}")
        print("    请求体模板: called={called}&tts={tts}   (参数名按文档改)")
        if _HEADERS.get("Cookie"):
            print(f"    请求头    : {{\"Cookie\": \"{_HEADERS['Cookie']}\",")
            print("                 \"X-Requested-With\": \"XMLHttpRequest\",")
            print("                 \"Content-Type\": \"application/x-www-form-urlencoded\"}")
            print("    ↑ 会话 Cookie 必须带上, 否则设备会当成未登录直接拒掉。")
        ck = f" --cookie \"{_HEADERS['Cookie']}\"" if _HEADERS.get("Cookie") else ""
        print("\n  验证能否拨通:")
        print(f"    python3 {sys.argv[0]} --ip {ip} --call \\")
        print(f"        --path {pick} --phone 13800138000{ck}")
    else:
        print("  未探到可用 HTTP 接口。这台盒子大概率属于下面两种情况之一:")
        print("")
        print("  A) HTTP 只是 WEB 管理页, 真正的告警触发走 TCP Client API(软件里")
        print("     显示的 8000 端口)或 RS-485 —— 必须找厂商要协议文档;")
        print("  B) 接口要登录后才可见, 而上面的自动登录没成功 —— 换个姿势重试:")
        print("     ① 浏览器自己登录, 把 Cookie 复制过来(最有效):")
        print("        F12 → Network → 任意请求 → Request Headers → 复制 cookie 整行")
        print(f'        python3 {sys.argv[0]} --ip {ip} --deep --cookie "PHPSESSID=xxxx"')
        print("     ② 用已知账号密码让脚本自动登录:")
        print(f"        python3 {sys.argv[0]} --ip {ip} --deep --user <账号> --pass <密码>")
        print("")
        print("  【最快解决办法】直接找厂商要文档, 联系方式:")
        print("     北京融智云物联科技有限公司  电话/手机: 18810728995")
        print("     客服 QQ: 2607791698   邮箱: wuqiong@rzyiot.com")
        print("     要的东西: 《MFM-920 HTTP API 接口文档》或《TCP Client API 协议》")
        print("")
        print("  【过渡方案】不用接口也能先跑起来: 在设备 WEB 页面里「预制告警通知」")
        print("     (这系列支持预制 250 条、可设多联系人优先级), 拿到触发方式和编号后,")
        print("     平台用通用 HTTP 网关发触发请求即可。")


def simple(base, timeout):
    """原来的单端口快速探测。"""
    section(1, "抓设备 WEB 管理页面")
    r = request(base + "/", timeout=timeout)
    if r.status_code < 0 or r.status_code >= 500:
        print(f"  ✗ 连不上 {base} —— {r.text[:160]}")
        print("    请确认: 设备和本机在同一网段 / 网线插好 / 防火墙放通 / IP 是否正确")
        print("    (设备 IP 可在告警软件的「本地网络」页面里查看)")
        print("    建议改用深度模式: 加 --deep 会自动扫多个端口")
        sys.exit(1)
    html = r.text
    print(f"  HTTP {r.status_code} | Server: {r.hdr('Server')[:30]} | "
          f"标题: {title_of(html) or '(无)'} | {len(html)} 字节")
    if looks_like_login(html):
        print("  ⚠ 页面要求登录, 接口可能要登录后才可见 —— 加 --deep --user X --pass Y")

    for a in dict.fromkeys(re.findall(r"""<form[^>]+action\s*=\s*["']([^"']+)["']""", html, re.I)):
        print(f"  表单 action: {a}")

    section(2, "下载页面引用的 JS + 内联脚本, 提取接口路径")
    js_urls = [joinurl(base, u) for u in JS_URL_RE.findall(html)]
    root = guess_web_root(js_urls)
    print(f"  推断 WEB 根目录: {root}")
    for extra in ("/js/main.js", "/static/js/app.js", "/js/app.js", "/main.js",
                  "/js/common.js"):
        js_urls.append(base + extra)
        js_urls.append(base + root + extra.lstrip("/"))
    found = set()
    for u in list(dict.fromkeys(js_urls))[:20]:
        rr = request(u, timeout=timeout)
        if rr.status_code != 200 or not rr.text.strip() or rr.text.strip() == html.strip():
            continue
        print(f"  ✓ {u} ({len(rr.text)} 字节)")
        for p in extract_paths(rr.text):
            if not is_static(p):
                found.add(p)
    inline = "\n".join(SCRIPT_RE.findall(html or ""))
    for p in extract_paths(inline) | extract_paths(html):
        if not is_static(p):
            found.add(p)
    print(f"  发现 {len(found)} 个接口路径" + ("(见下方探测)" if found else ""))

    section(3, "探测候选接口(GET, 不带号码 —— 不会拨号)")
    root_body = html.strip()
    print(f"  {'路径':<28} {'状态':<6} 响应片段")
    print("  " + "-" * 62)
    alive = []
    for p in CANDIDATE_PATHS + sorted(found):
        if len(p) > 70:
            continue
        rr = request(joinurl(base, p), timeout=timeout)
        if rr.status_code == 404 or rr.status_code < 0:
            continue
        text = (rr.text or "").strip()
        if p != "/" and text and text == root_body:
            continue
        print(f"  {'✓' if rr.status_code < 400 else ' '} {p:<26} "
              f"{rr.status_code:<6} {text.replace(chr(10), ' ')[:80]}")
        if rr.status_code < 400 and p != "/":
            alive.append(p)

    section(4, "结论")
    if alive:
        print("  疑似接口:")
        for p in dict.fromkeys(alive):
            print(f"    {base}{p}")
        print(f"\n  验证: python3 {sys.argv[0]} --ip {base.split('//')[1].split(':')[0]} "
              f"--call --path {alive[0]} --phone 13800138000")
    else:
        print("  没探到。请用深度模式再跑一次(自动扫多端口 + 尝试登录):")
        print(f"    python3 {sys.argv[0]} --ip {base.split('//')[1].split(':')[0]} --deep")


def do_call(base, path, method, phone, text, timeout):
    section(9, f"实拨验证: {method} {path}")
    if not phone:
        print("  ✗ 缺少 --phone, 已跳过")
        return
    url = joinurl(base, path)
    if not check_url(url):
        return
    params = {"phone": phone, "text": text, "content": text,
              "mobile": phone, "msg": text, "called": phone, "tts": text}
    print(f"  目标: {url}")
    print(f"  号码: {phone}   内容: {text}")
    print("  提示: 这里把常见参数名都带上, 设备只认自己需要的那个。")
    if method == "GET":
        sep = "&" if "?" in url else "?"
        r = request(f"{url}{sep}{urllib.parse.urlencode(params)}", timeout=timeout)
    else:
        r = request(url, "POST", params, timeout=timeout)
    print(f"  HTTP {r.status_code}")
    print(f"  设备返回: {(r.text or '')[:400]}")
    print("\n  请留意手机是否振铃。若返回成功但没打, 说明参数名不对,")
    print("  到平台看「发送记录」里设备返回的原文, 换别名再试。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True, help="告警盒子 IP")
    ap.add_argument("--port", type=int, default=80, help="单端口模式下的端口, 默认 80")
    ap.add_argument("--deep", action="store_true", help="深度探测: 多端口+HTTPS+登录+跟随链接+TCP")
    ap.add_argument("--ports", default="", help="深度模式要扫的端口, 逗号分隔")
    ap.add_argument("--user", help="WEB 登录账号")
    ap.add_argument("--pass", dest="pwd", help="WEB 登录密码")
    ap.add_argument("--user-field", help="登录表单的账号字段名")
    ap.add_argument("--pass-field", help="登录表单的密码字段名")
    ap.add_argument("--cookie", help="浏览器登录后复制的 Cookie, 原样粘进来, 如 \"PHPSESSID=xxxx\"")
    ap.add_argument("--header", action="append", default=[],
                    help="附加请求头, 可重复, 如 --header \"X-Token: abc\"")
    ap.add_argument("--tcp", type=int, help="只做原始 TCP 探测的端口(如 8000)")
    ap.add_argument("--send", help="--tcp 模式下连上后要发送的字节(支持 \\x 转义)")
    ap.add_argument("--path", help="只测这一个路径")
    ap.add_argument("--dump", help="完整打印指定路径的返回内容(用来看页面里的接口调用代码)")
    ap.add_argument("--call", action="store_true", help="真的拨一次电话(验证用)")
    ap.add_argument("--phone", help="--call 时的被叫号码")
    ap.add_argument("--text", default="AIOps 运维平台测试告警, 无需处理")
    ap.add_argument("--method", default="POST", choices=["POST", "GET"])
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()

    print(f"目标设备: {args.ip}")
    print("提示: 连接超时/卡住通常是网络或防火墙问题, 先 ping 一下")
    add_headers(args.cookie, args.header)
    cred = decode_auth_cookie(args.cookie or "")
    if cred:
        print(f"★ Cookie 里的 auth 解出来是: {cred}")
        print("  这台盒子的会话 Cookie = URL编码(base64(账号:密码)), 平台可以自己算出来直接带,")
        print("  不需要走登录流程。")

    if args.tcp:
        section(1, f"原始 TCP 探测 {args.ip}:{args.tcp}")
        tcp_probe(args.ip, args.tcp, timeout=args.timeout,
                  send=args.send.encode().decode("unicode_escape") if args.send else None)
        return

    if args.deep:
        ports = [int(x) for x in args.ports.split(",") if x.strip().isdigit()] or DEFAULT_PORTS
        deep(args.ip, ports, args.user, args.pwd, args.timeout,
             args.user_field, args.pass_field)
        return

    base = f"http://{args.ip}:{args.port}"
    if args.dump:
        section(1, f"完整抓取: {args.dump}")
        url = joinurl(base, args.dump)
        if not check_url(url):
            return
        r = request(url, timeout=args.timeout)
        print(f"  HTTP {r.status_code} | {len(r.text)} 字节 | {url}")
        print("  " + "-" * 64)
        for line in (r.text or "").splitlines():
            print(f"  {line}")
        return

    if args.path:
        section(1, f"只测指定路径: {args.path}")
        url = joinurl(base, args.path)
        if not check_url(url):
            return
        r = request(url, timeout=args.timeout)
        print(f"  HTTP {r.status_code} | 标题: {title_of(r.text) or '(无)'} | {len(r.text)} 字节")
        print(f"  响应片段: {r.text.replace(chr(10), ' ')[:300]}")
        if args.call:
            do_call(base, args.path, args.method, args.phone, args.text, args.timeout)
        return

    simple(base, args.timeout)
    if args.call:
        do_call(base, args.path or "/api/call", args.method, args.phone, args.text, args.timeout)


if __name__ == "__main__":
    main()
