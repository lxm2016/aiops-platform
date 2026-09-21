# -*- coding: utf-8 -*-
"""模拟 Modbus TCP 网关, 用来验证 probe_modbus.py。"""
import socket
import struct
import sys
import threading

def pad(vals, n=64):
    return (list(vals) + [0] * n)[:n]


# 每台设备: {从站地址: {功能码: [寄存器值...]}}
DEVICES = {
    1: {
        3: pad([256, 634, 0, 0, 1]),                       # 温湿度: 25.6℃ 63.4%
        4: pad([]),
        1: pad([]),
        2: pad([]),
    },
    9: {                                                   # 烟感
        3: pad([]),
        4: pad([]),
        1: pad([]),
        2: pad([0, 0, 1]),                                 # 第3路报警
    },
}

# 水浸 + UPS 那台(5302/5305 分两个端口)
DEVICES_5302 = {
    1: {3: pad([]), 4: pad([]), 1: pad([1]), 2: pad([])},   # 水浸: 线圈0=1 报警
}

# 科士达 YMK UPS 输入寄存器(FC04), wire 地址 = 协议编号-1 (30001 -> 30000)
# 数值模拟真实读数: 输入 231.8V / 输出 219.3V / 电池 216.2V / 容量 100% / 后备 32.6min
_UPS = {
    30000: 2318, 30001: 2322, 30002: 2309, 30003: 499,          # 输入电压/频率
    30004: 429, 30005: 409, 30006: 409, 30007: 98,              # 输入电流/功因
    30010: 2193, 30011: 2193, 30012: 2189, 30013: 500,          # 输出电压/频率
    30014: 425, 30015: 329, 30016: 353,                         # 输出电流
    30017: 81, 30018: 77, 30019: 80, 30020: 412,                # 输出功率/负载率
    30026: 2189, 30027: 2189, 30028: 2185, 30029: 500,          # 旁路
    30030: 2162, 30031: 0, 30032: 45, 30034: 12,                # 电池电压/电流
    30036: 100, 30037: 326, 30038: 280, 30039: 264, 30040: 0,   # 容量/后备/温度
}
UPS_FC4 = [0] * 30100
for _a, _v in _UPS.items():
    UPS_FC4[_a] = _v

DEVICES_5305 = {
    1: {
        3: pad([220, 7500, 0, 0, 50]),   # 保留旧 FC03 数据(兼容旧点位)
        4: UPS_FC4,                       # 科士达协议 FC04 @ 30000+
        1: pad([]), 2: pad([]),
    },
}


def build_response(fc, addr, count, table):
    if fc not in table:
        return bytes([fc | 0x80, 0x01])
    vals = table[fc]
    if addr + count > len(vals):
        return bytes([fc | 0x80, 0x02])
    chunk = vals[addr:addr + count]
    if fc in (1, 2):
        nbytes = (count + 7) // 8
        bits = 0
        for i, v in enumerate(chunk):
            if v:
                bits |= (1 << i)
        return bytes([fc, nbytes]) + bits.to_bytes(nbytes, "little")
    body = b"".join(struct.pack(">H", v & 0xFFFF) for v in chunk)
    return bytes([fc, len(body)]) + body


def serve(port, devices):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(5)

    def handle(conn):
        try:
            while True:
                head = conn.recv(7)
                if len(head) < 7:
                    return
                tid, pid, length, unit = struct.unpack(">HHHB", head)
                pdu = b""
                while len(pdu) < length - 1:
                    pdu += conn.recv(length - 1 - len(pdu))
                fc, addr, count = struct.unpack(">BHH", pdu[:5])
                table = devices.get(unit, {})
                resp_pdu = build_response(fc, addr, count, table)
                conn.sendall(struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, unit) + resp_pdu)
        except Exception:
            pass
        finally:
            conn.close()

    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def serve_rtu(port, devices):
    """透传模式的串口服务器: 客户端发裸 RTU 帧(8字节), 回裸 RTU 应答。

    请求帧: [从站, 功能码, 地址hi, 地址lo, 数量hi, 数量lo, crc_lo, crc_hi]
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(5)

    def crc16(data):
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        return crc

    def handle(conn):
        try:
            while True:
                req = b""
                while len(req) < 8:
                    chunk = conn.recv(8 - len(req))
                    if not chunk:
                        return
                    req += chunk
                unit, fc = req[0], req[1]
                addr, count = struct.unpack(">HH", req[2:6])
                rx_crc = struct.unpack("<H", req[6:8])[0]
                if crc16(req[:6]) != rx_crc:
                    continue                       # CRC 错直接丢弃(与真实设备一致)
                table = devices.get(unit, {})
                resp_pdu = build_response(fc, addr, count, table)
                body = bytes([unit]) + resp_pdu
                conn.sendall(body + struct.pack("<H", crc16(body)))
        except Exception:
            pass
        finally:
            conn.close()

    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    for port, dev in ((5001, DEVICES), (5002, DEVICES), (5302, DEVICES_5302), (5305, DEVICES_5305)):
        threading.Thread(target=serve, args=(port, dev), daemon=True).start()
    # 6001: RTU 透传口, 总线协议同 5001(从站 1 温湿度 / 9 烟感)
    threading.Thread(target=serve_rtu, args=(6001, DEVICES), daemon=True).start()
    # 6002: RTU 透传口, 挂科士达 UPS(从站 1, FC04 @ 30000+)
    threading.Thread(target=serve_rtu, args=(6002, DEVICES_5305), daemon=True).start()
    print("mock modbus 已启动: 5001/5002/5302/5305 (TCP) + 6001/6002 (RTU透传)", flush=True)
    threading.Event().wait()
