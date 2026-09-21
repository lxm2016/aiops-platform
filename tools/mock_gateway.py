# -*- coding: utf-8 -*-
"""模拟院内短信/电话告警平台, 用于本地验证告警通知链路是否真的把消息发出去了。

启动: python tools/mock_gateway.py  (监听 8899)
它会把收到的告警内容打印出来, 并始终返回 {"code":0,"msg":"success"}。
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 8899

RECEIVED = []


class Handler(BaseHTTPRequestHandler):
    def _echo(self, body: str, method: str):
        print(f"[网关收到 {method}] {self.path}", flush=True)
        print(f"   内容: {body[:400]}", flush=True)
        RECEIVED.append({"method": method, "path": self.path, "body": body})
        payload = b'{"code":0,"msg":"success"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode("utf-8", "ignore")
        self._echo(body, "POST")

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        self._echo(str(q), "GET")

    def log_message(self, *args):
        pass   # 安静一点


if __name__ == "__main__":
    print(f"模拟告警网关已启动: http://127.0.0.1:{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
