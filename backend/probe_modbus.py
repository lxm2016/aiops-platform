#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立 Modbus 探测工具(零第三方依赖, 仅用标准库)。

用于排查"网关能连上(TCP 握手成功)但设备一直无应答"这类动环问题。
本工具的 RTU 帧封装、CRC、解析逻辑与平台 app/services/modbus_service.py
完全一致, 因此探测结果可以直接用来核对平台配置。

典型场景
--------
1) 烟感@172.16.0.238:5308 一直"响应超时", 同网关 5305 的 UPS 却正常。
   -> 说明网关与网络没问题, 问题局限在 5308 这个端口/烟感本身。
   -> 先用 --scan 看这个透传端口上到底有没有 Modbus 从站应答、从站地址是多少:

       python probe_modbus.py --host 172.16.0.238 --port 5308 --mode rtu --scan

2) 找到了从站(比如地址 1), 但平台点位读不到 -> 用 --probe 把常用功能码/地址
   都试一遍, 看哪些寄存器能返回数据, 据此修正平台里的 fc/address:

       python probe_modbus.py --host 172.16.0.238 --port 5308 --mode rtu --slave 1 --probe

3) 标准 Modbus TCP 设备(非透传):

       python probe_modbus.py --host 172.16.0.238 --port 5305 --mode tcp --scan

输出约定
--------
  [OK]   从站正常返回数据
  [EXC]  从站返回了 Modbus 异常帧(说明从站存在, 只是本次请求的功能码/地址不对)
  [TIMEOUT] 连接能建立但 1 秒内无任何字节返回(设备不在总线上 / 串口参数不符 / 不是 Modbus 设备)
"""
import argparse
import socket
import struct
import sys


def _crc16_modbus(data: bytes) -> int:
    """Modbus RTU CRC-16 (多项式 0xA001, 初值 0xFFFF, 低字节在前)。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _build_rtu(slave: int, fc: int, addr: int, count: int) -> bytes:
    pdu = bytes([slave & 0xFF, fc]) + struct.pack(">HH", addr, count)
    return pdu + struct.pack("<H", _crc16_modbus(pdu))


def _build_tcp(tid: int, slave: int, fc: int, addr: int, count: int) -> bytes:
    pdu = bytes([fc]) + struct.pack(">HH", addr, count)
    return struct.pack(">HHHB", tid, 0, len(pdu) + 1, slave) + pdu


def _read_exact(sock: socket.socket, n: int, timeout: float) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise socket.timeout("connection closed")
        buf += chunk
    return buf


def _probe_once(host, port, mode, slave, fc, addr, count, timeout):
    """返回 (status, info)。status ∈ {'OK','EXC','TIMEOUT','ERR'}。"""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return "ERR", f"TCP 连接失败: {e}"

    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        if mode == "rtu":
            req = _build_rtu(slave, fc, addr, count)
        else:
            req = _build_tcp(0x0001, slave, fc, addr, count)
        sock.sendall(req)

        if mode == "rtu":
            head = _read_exact(sock, 3, timeout)
            r_slave, r_fc, third = head[0], head[1], head[2]
            if r_fc & 0x80:
                _read_exact(sock, 2, timeout)  # 异常码 + CRC
                return "EXC", f"异常码 {third} (从站 {r_slave} 存在, 但拒绝该请求)"
            nbytes = third
            body = _read_exact(sock, nbytes + 2, timeout)  # 数据 + CRC
            data = body[:nbytes]
        else:
            head = _read_exact(sock, 7, timeout)  # MBAP
            r_fc = head[7]
            length = struct.unpack(">H", head[4:6])[0]
            body = _read_exact(sock, length - 1, timeout)
            if r_fc & 0x80:
                return "EXC", f"异常码 {body[0] if body else '?'}"
            nbytes = body[0] if body else 0
            data = body[1:1 + nbytes]

        # 把数据整理成可读形式
        if fc in (1, 2):
            bits = []
            for byte in data:
                for i in range(8):
                    bits.append((byte >> i) & 1)
            return "OK", f"bits={bits[:count]}"
        vals = []
        for i in range(0, len(data) - (len(data) % 2), 2):
            vals.append(struct.unpack(">H", data[i:i + 2])[0])
        return "OK", f"regs={vals[:max(count,1)]}"
    except socket.timeout:
        return "TIMEOUT", "连上但无应答(从站不存在 / 串口参数不符 / 非 Modbus 设备)"
    except Exception as e:  # noqa: BLE001
        return "ERR", f"{type(e).__name__}: {e}"
    finally:
        try:
            sock.close()
        except Exception:
            pass


def scan(host, port, mode, timeout):
    print(f"\n=== 扫描 {host}:{port} ({mode}) 上的 Modbus 从站(1..247) ===")
    fcs = [1, 2, 3, 4]
    found = []
    for slave in range(1, 248):
        hit = None
        for fc in fcs:
            st, info = _probe_once(host, port, mode, slave, fc, 0, 1, timeout)
            if st in ("OK", "EXC"):
                hit = (fc, st, info)
                break
        if hit:
            fc, st, info = hit
            print(f"  从站 {slave:>3}: [{st}] fc{fc} {info}")
            found.append(slave)
        if slave % 40 == 0 and not found:
            sys.stdout.write(f"  ...已扫描 {slave} 个, 暂无可应答从站\n")
    if not found:
        print("  没有任何从站应答。重点检查: 5308 端口是否真接了烟感、串口参数是否匹配、接线/终端电阻。")
    else:
        print(f"\n结论: 该端口上有 {len(found)} 个从站应答: {found}")
        if mode == "rtu":
            print("提示: 透传模式下从站地址由设备自身拨码/配置决定, 把平台设备表的 slave_id 改成上面找到的地址即可。")
    return found


def probe(host, port, mode, slave, timeout):
    print(f"\n=== 针对从站 {slave} 探测功能码 × 地址({host}:{port}, {mode}) ===")
    addrs = [0, 1, 2, 3, 4, 5, 10, 20, 100, 300]
    fcs = [1, 2, 3, 4]
    any_ok = False
    for fc in fcs:
        for addr in addrs:
            count = 1
            st, info = _probe_once(host, port, mode, slave, fc, addr, count, timeout)
            if st in ("OK", "EXC"):
                print(f"  fc{fc} @addr{addr:>4}: [{st}] {info}")
                if st == "OK":
                    any_ok = True
    if not any_ok:
        print("  该从站在所试范围(地址 0~300)内无可用寄存器。")
        print("  可能原因: 实际寄存器地址更大; 或设备并非标准 Modbus(仅干接点/继电器输出)。")
        print("  若是继电器型烟感, 需确认串口服务器是否把它映射成了固定 DI 寄存器(常见 FC01/FC02 @0)。")


def main():
    ap = argparse.ArgumentParser(description="Modbus 动环设备探测工具")
    ap.add_argument("--host", required=True, help="网关/设备 IP")
    ap.add_argument("--port", type=int, required=True, help="TCP 端口")
    ap.add_argument("--mode", choices=["rtu", "tcp"], default="rtu",
                    help="rtu=透传模式裸 RTU 帧(默认); tcp=标准 Modbus TCP")
    ap.add_argument("--slave", type=int, default=1, help="目标从站地址(配合 --probe)")
    ap.add_argument("--scan", action="store_true", help="扫描 1..247 从站")
    ap.add_argument("--probe", action="store_true", help="对指定从站试探功能码×地址")
    ap.add_argument("--timeout", type=float, default=1.0, help="单次探测超时(秒)")
    args = ap.parse_args()

    if args.scan:
        scan(args.host, args.port, args.mode, args.timeout)
    elif args.probe:
        probe(args.host, args.port, args.mode, args.slave, args.timeout)
    else:
        # 默认: 对给定从站做一次 FC02@2 读取(对齐平台烟感默认点位)
        st, info = _probe_once(args.host, args.port, args.mode, args.slave, 2, 2, 1, args.timeout)
        print(f"从站 {args.slave} FC02@addr2: [{st}] {info}")


if __name__ == "__main__":
    main()
