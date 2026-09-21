# -*- coding: utf-8 -*-
"""本地预览: 静态托管 frontend/dist, 并把 /api 反代到后端, 方便用浏览器验收页面。"""
import http.server
import os
import socketserver
import urllib.error
import urllib.request

DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                    "aiops-platform", "frontend", "dist"))
BACKEND = "http://127.0.0.1:8123"
PORT = 8100


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIST, **kw)

    def log_message(self, *a):
        pass

    def _proxy(self, method):
        body = None
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            body = self.rfile.read(n)
        req = urllib.request.Request(BACKEND + self.path, data=body, method=method)
        for h in ("Content-Type", "Authorization", "Accept"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except Exception as e:
            data, code, ctype = str(e).encode(), 502, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _devlogin(self):
        """同源引导页: 用 admin/admin123 换 token 写进 localStorage, 再跳目标页。
        只为本地无头截图验收用, 不属于交付产物。"""
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(self.path).query)
        to = (qs.get("to") or ["/"])[0]
        probe = qs.get("probe", [""])[0]
        probe_js = ""
        if probe:
            # 诊断模式: 先用页面自己的 fetch 打一遍接口, 把结果写进 document.title
            probe_js = f"""
  try {{
    const t0 = performance.now();
    const rr = await fetch({probe!r}, {{headers: {{Authorization: 'Bearer ' + j.access_token}}}});
    const txt = await rr.text();
    document.title = 'probe:' + rr.status + ':' + (performance.now()-t0).toFixed(0) + 'ms:len' + txt.length;
  }} catch (e) {{ document.title = 'probe-ERR:' + (e && e.message || e); }}
  await new Promise(r => setTimeout(r, 300));
"""
        html = f"""<!doctype html><meta charset="utf-8"><title>devlogin</title>
<script>
(async () => {{
  const r = await fetch('/api/auth/login', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{username: 'admin', password: 'admin123'}})
  }});
  const j = await r.json();
  localStorage.setItem('token', j.access_token);
  document.title = 'token-ok';
{probe_js}  if (!{bool(qs.get("stay"))}) location.replace({to!r});
}})();
</script>"""
        b = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    # 无头浏览器的 --virtual-time-budget 会把虚拟时钟快进, axios 的 20s 超时
    # 会被瞬间触发导致"加载失败"(实际后端毫秒级就返回了)。
    # 预览场景下把 >=4s 的定时器屏蔽掉, 只影响超时兜底, 不影响 UI 动画。
    # ?_expand=1 时顺带把设备表第一行展开, 截"点位明细"用。
    _NO_LONG_TIMER = ("<script>window.setTimeout=(function(o){return function(f,t){"
                      "return t>=4000?0:o(f,t)}})(window.setTimeout);</script>")
    _EXPAND = ("<script>if(location.search.indexOf('_expand=1')>=0){"
               "var n=0;var iv=setInterval(function(){"
               "var b=document.querySelector('.el-table__expand-icon');"
               "if(b){b.click();clearInterval(iv);}"
               "if(++n>40)clearInterval(iv);},250);}</script>")

    # 同步 XHR 先把 token 备好 —— 它在 <head> 里、先于 type=module(总是延迟执行)
    # 运行, 所以 Vue 应用挂载时 localStorage 里已经有 token 了, 不会被踢回登录页。
    # 只为本地无头截图验收用, 不属于交付产物。
    _BOOT_TOKEN = ("<script>(function(){try{if(localStorage.getItem('token'))return;"
                   "var x=new XMLHttpRequest();x.open('POST','/api/auth/login',false);"
                   "x.setRequestHeader('Content-Type','application/json');"
                   "x.send(JSON.stringify({username:'admin',password:'admin123'}));"
                   "var j=JSON.parse(x.responseText);"
                   "localStorage.setItem('token',j.access_token);"
                   "localStorage.setItem('username','admin');}catch(e){}})();</script>")

    def _serve_index(self):
        idx = os.path.join(DIST, "index.html")
        with open(idx, "rb") as f:
            html = f.read().decode("utf-8")
        html = html.replace("<head>", "<head>" + self._BOOT_TOKEN, 1)
        html = html.replace("</head>", self._NO_LONG_TIMER + self._EXPAND + "</head>")
        b = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api"):
            return self._proxy("GET")
        if self.path.startswith("/_devlogin"):
            return self._devlogin()
        # SPA: 找不到的路径一律回 index.html, 前端路由自己处理
        path = self.translate_path(self.path)
        if (os.path.isdir(path) or not os.path.exists(path)
                or path.lower().endswith(".html")):
            return self._serve_index()
        return super().do_GET()

    def do_POST(self):
        return self._proxy("POST")

    def do_PUT(self):
        return self._proxy("PUT")

    def do_DELETE(self):
        return self._proxy("DELETE")


socketserver.TCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"预览服务已启动: http://127.0.0.1:{PORT}  (dist={DIST})", flush=True)
    httpd.serve_forever()
