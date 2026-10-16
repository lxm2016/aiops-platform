#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIOps 跨平台监控 Agent 主程序

功能:
    - 定时采集本机 CPU / 内存 / 磁盘 / 网络 / 端口 / 服务信息
    - 以 JSON 格式 POST 上报到服务端 /api/servers/report 接口
    - 请求头携带 X-Agent-Token 用于身份校验

支持平台:
    - Linux:   CentOS / openEuler / Rocky Linux / Ubuntu / 龙蜥(Anolis OS)
    - Windows: Windows Server 2008 ~ 2019

用法:
    python3 agent.py                          # 使用脚本同目录下的 config.yaml
    python3 agent.py -c /etc/aiops/config.yaml
    python3 agent.py --once                   # 只采集上报一次(调试用)
"""
import argparse
import json
import logging
import os
import re
import signal
import threading
import time
import urllib.error
import urllib.request

from collectors import get_collector

try:
    import yaml  # 可选依赖: 有则用yaml解析, 没有则用内置简易解析器
except ImportError:
    yaml = None

# 停止标志: 收到 SIGINT/SIGTERM 后置位, 主循环优雅退出
stop_event = threading.Event()


def _handle_signal(signum, frame):
    """信号处理: 收到终止信号后通知主循环退出。"""
    logging.info("收到终止信号 %s, 准备退出...", signum)
    stop_event.set()


def _simple_yaml_load(text):
    """极简 YAML 解析器(无 PyYAML 的目标机使用)。

    支持本项目的 config.yaml 结构:
      - 顶层 key: value (标量)
      - 顶层 key: (空) 下跟字典段(server:/agent:/nas_creds:)
      - 顶层 key: (空) 下跟块列表段(nas_shares: -> "- 项", 缩进或顶格均可)
      - 嵌套字典(nas_creds 下再按主机分 user/password)
    容错(用户手写常见失误):
      - "- 项" 顶格写在段名下(标准 YAML 也允许)同样识别;
      - 注释仅按"井号前是行首或空白"剥离(密码里可含 # 不被截断);
      - 值两端的中文全角引号(“”)按引号剥掉(防中文输入法误输)。
    仅用于本项目, 非通用 YAML 解析器。
    """
    result = {}
    # 栈:(缩进, 容器); 容器为 dict 或 list, 栈底为 result(缩进 -1)
    stack = [(-1, result)]
    # 与栈平行: 每个容器在父容器中的"键名"(列表提升时用于回写父 dict)
    keys = [None]
    last_key = None  # 最近一个顶层段名: 用于容错"顶格写列表项"

    def _coerce(v):
        # 尝试转数字
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v

    def _strip_quotes(v):
        # 去两端空白与引号(含中文输入法的全角引号)
        return v.strip().strip("\"'“”‘’")

    for raw in text.splitlines():
        # 仅当 # 前是行首或空白时才视为注释(避免截断含 # 的密码/路径)
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()

        # 回退到缩进严格小于当前行的最近祖先
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
            keys.pop()
        _, parent = stack[-1]

        if content.startswith("- "):
            item = _strip_quotes(content[2:])
            if not isinstance(parent, list):
                # 常规路径: 把当前占位空 dict 提升为列表并回写父 dict 对应键
                gp = stack[-2][1] if len(stack) >= 2 else result
                k = keys[-1]
                if isinstance(gp, dict) and k is not None:
                    new_list = []
                    gp[k] = new_list
                    stack[-1] = (stack[-1][0], new_list)
                    parent = new_list
                elif last_key is not None and isinstance(result.get(last_key), (dict, list)):
                    # 容错: "- 项" 顶格写在段名下未缩进
                    cur = result[last_key]
                    if isinstance(cur, list):
                        parent = cur
                    elif isinstance(cur, dict) and not cur:
                        cur = []
                        result[last_key] = cur
                        parent = cur
                    else:
                        parent = []
                else:
                    parent = []
            if isinstance(parent, list):
                parent.append(_coerce(item))
        else:
            key, _, value = content.partition(":")
            key = key.strip()
            value = _strip_quotes(value)
            if indent == 0:
                last_key = key
                if value == "":
                    result[key] = {}
                    stack.append((0, result[key]))
                    keys.append(key)
                else:
                    result[key] = _coerce(value)
            else:
                if not isinstance(parent, dict):
                    continue
                if value == "":
                    parent[key] = {}
                    stack.append((indent, parent[key]))
                    keys.append(key)
                else:
                    parent[key] = _coerce(value)
    return result


def load_config(path):
    """加载 YAML 配置文件, 文件缺失或字段缺失时使用内置默认值。"""
    cfg = {
        "server": {
            "url": "http://SERVER:8080/api/servers/report",
            "token": "aiops-agent-shared-token",
        },
        "agent": {
            "interval": 5,    # 采集/上报间隔(秒)
            "timeout": 10,    # HTTP 请求超时(秒)
        },
        # 要监控的网络共享盘(SMB): 华为存储等映射盘/UNC。每项可写 "UNC路径"
        # 或 "UNC路径 | 备注名"。为空列表则不采集。服务身份下也能查 UNC,
        # 比依赖盘符可见性更稳。示例(取消注释并改成你的实际地址即可):
        # nas_shares:
        #   - \\192.168.1.5\share
        #   - \\192.168.1.6\share | 华为存储A
        "nas_shares": [],
        # 共享盘访问凭据(可选): Agent 常以 SYSTEM 身份运行, 直接查 UNC 共享
        # 常因无凭据失败。在此为共享所属主机配上 user/password, Agent 会用
        # WNetAddConnection2W 带凭据建连后再查空间。host 与 nas_shares 里
        # UNC 的 \\host 部分保持一致。示例(取消注释并改成你的实际信息即可):
        # nas_creds:
        #   192.168.1.5:
        #     user: "storage\\admin"
        #     password: "你的密码"
        "nas_creds": {},
    }
    try:
        with open(path, "rb") as f:
            raw = f.read()
        # 编码兜底: install.bat 用 echo 写出的 config 是控制台编码(中文系统为
        # GBK), 用户用记事本另存为 ANSI 也是 GBK; 依次尝试 utf-8-sig/utf-8/gbk。
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            logging.error("配置文件 %s 不是有效的 UTF-8/GBK 文本, 使用默认配置", path)
            return cfg
        user_cfg = yaml.safe_load(text) if yaml else _simple_yaml_load(text)
        user_cfg = user_cfg or {}
        for section, values in user_cfg.items():
            if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
    except FileNotFoundError:
        logging.warning("未找到配置文件 %s, 使用默认配置", path)
    except Exception as e:
        logging.error("配置文件 %s 解析失败: %s", path, e)
    return cfg


def report(url, token, timeout, payload):
    """将采集数据 POST 上报到服务端, 请求头携带 X-Agent-Token。
    使用标准库 urllib, 零第三方网络依赖。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "X-Agent-Token": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        return status


def main():
    parser = argparse.ArgumentParser(description="AIOps 跨平台监控 Agent")
    parser.add_argument(
        "-c", "--config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"),
        help="配置文件路径(默认: 脚本同目录下的 config.yaml)",
    )
    parser.add_argument("--once", action="store_true", help="只采集上报一次后退出(调试用)")
    parser.add_argument("--server", help="平台地址, 如 http://172.16.10.147:8080 (优先级高于配置文件)")
    parser.add_argument("--token", help="Agent令牌 (优先级高于配置文件)")
    parser.add_argument("--interval", type=int, help="采集间隔秒数 (优先级高于配置文件)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = load_config(args.config)
    url = cfg["server"]["url"]
    token = str(cfg["server"]["token"])
    interval = max(5, int(cfg["agent"]["interval"]))  # 间隔最小 5 秒, 防止配置错误打爆服务端
    timeout = int(cfg["agent"]["timeout"])

    # 命令行参数优先于配置文件
    if args.server:
        base = args.server.rstrip("/")
        url = base + "/api/servers/report" if "/api/" not in base else base
    if args.token:
        token = args.token
    if args.interval:
        interval = max(5, args.interval)

    # 容错: 配置/命令行漏写 http:// 前缀时自动补上
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 根据当前操作系统自动选择采集器
    collector = get_collector()
    # 共享盘监控列表(华为存储等 SMB 映射盘): 从配置下发到采集器
    # 空段可能被解析为 dict, 这里统一保证为 list
    _ns = cfg.get("nas_shares")
    collector.nas_shares = _ns if isinstance(_ns, list) else []
    # 共享盘访问凭据(可选, SYSTEM 身份访问 UNC 共享失败时使用)
    _nc = cfg.get("nas_creds")
    collector.nas_creds = _nc if isinstance(_nc, dict) else {}
    logging.info(
        "Agent 启动 | 平台=%s | 上报地址=%s | 间隔=%ss",
        collector.os_type, url, interval,
    )

    while not stop_event.is_set():
        start = time.time()
        try:
            payload = collector.collect()
            status = report(url, token, timeout, payload)
            logging.info(
                "上报成功: HTTP %s | cpu=%.1f%% mem=%.1f%% disk=%.1f%% rx=%.2fMbps tx=%.2fMbps",
                status,
                payload["cpu_percent"], payload["mem_percent"], payload["disk_percent"],
                payload["net_rx_mbps"], payload["net_tx_mbps"],
            )
        except (urllib.error.URLError, OSError) as e:
            logging.error("上报失败: %s", e)
        except Exception:
            logging.exception("采集/上报过程发生异常")

        if args.once:
            break

        # 按 1 秒步长休眠, 保证能及时响应终止信号
        deadline = start + interval
        while not stop_event.is_set() and time.time() < deadline:
            stop_event.wait(1)

    logging.info("Agent 已退出")


if __name__ == "__main__":
    main()
