#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clash Meta 自动选择最低延迟美国节点（订阅自适应版）
====================================================

每 30 分钟(可配置)对当前订阅里的「美国」节点逐一测速,自动把主节点组切换到
延迟最低的那个。通过 Clash Meta 的外部控制接口(RESTful API)完成,不重启内核、
不修改订阅配置。

特性:
  - 主节点组自动识别,换订阅(TAGSS / TapFog / …)无需改代码。
  - 默认只使用 1x 倍率节点,避免 5x/10x 节点成倍消耗流量(可关闭)。
  - 控制接口自动探测常见端口,并可从 ClashX / ClashX Meta 偏好设置读取密钥。

用法:
  python3 clash-auto-us.py --once           # 执行一次:测速 + 切换
  python3 clash-auto-us.py --dry-run        # 只测速打印结果,不切换(推荐先跑这个)
  python3 clash-auto-us.py --loop           # 常驻进程,每 INTERVAL 秒跑一次
  python3 clash-auto-us.py --once --notify  # 切换后发 macOS 通知
  python3 clash-auto-us.py --once --group "TapFog"   # 强制指定主节点组

配置: 均可通过环境变量覆盖(CLASH_CONTROLLER / CLASH_SECRET / CLASH_GROUP /
CLASH_US_KEYWORDS / CLASH_X_FILTER / CLASH_DELAY_URL / CLASH_TIMEOUT_MS /
CLASH_INTERVAL_SEC / CLASH_MIN_GAIN_MS / CLASH_CONCURRENCY)。
"""

import argparse
import concurrent.futures
import json
import os
import plistlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 配置区(可通过环境变量覆盖)
# ---------------------------------------------------------------------------
CONTROLLER = os.environ.get("CLASH_CONTROLLER", "")          # 留空 = 自动探测
SECRET = os.environ.get("CLASH_SECRET", "")                  # 无密钥时留空
GROUP = os.environ.get("CLASH_GROUP", "")                    # 留空 = 自动识别主节点组
US_KEYWORDS = [k for k in os.environ.get(
    "CLASH_US_KEYWORDS", "🇺🇸,美国").split(",") if k]         # “美国节点”判定关键词
DELAY_URL = os.environ.get("CLASH_DELAY_URL",
                           "http://www.gstatic.com/generate_204")  # 测速 URL
TIMEOUT_MS = int(os.environ.get("CLASH_TIMEOUT_MS", "5000"))  # 单节点测速超时(毫秒)
INTERVAL_SEC = int(os.environ.get("CLASH_INTERVAL_SEC", "1800"))  # 循环间隔(秒)= 30 分钟
MIN_GAIN_MS = int(os.environ.get("CLASH_MIN_GAIN_MS", "20"))  # 快超过该值才切换,防抖动
MAX_CONCURRENCY = int(os.environ.get("CLASH_CONCURRENCY", "16"))  # 并发测速线程数

# 节点倍率过滤:仅保留该倍率的节点(如 "1")。空字符串 = 不过滤。
# 订阅节点名形如 "…丨1x US"/"…丨5x US",5x/10x 会消耗 5 倍/10 倍流量,故默认只用 1x。
X_FILTER = os.environ.get("CLASH_X_FILTER", "1")

# 常见 Clash GUI / mihomo 的外部控制端口(自动探测用)
CANDIDATE_PORTS = [9090, 9097, 19092, 50441]

# 这些是“组/策略”,不是真实节点
GROUP_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance",
               "Relay", "Direct", "Reject", "Pass", "Compatible", "Chain"}


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 控制接口解析(自动探测端口 + 读取密钥)
# ---------------------------------------------------------------------------
_ctl = {"base": None, "secret": None}

_PLIST_PATHS = [
    "~/Library/Preferences/com.metacubex.ClashX.meta.plist",
    "~/Library/Preferences/com.west2online.ClashX.plist",
]


def _plist_secrets(port: int):
    """从 ClashX / ClashX Meta 偏好设置里读取与端口匹配的 api-secret。"""
    out = []
    for p in _PLIST_PATHS:
        path = os.path.expanduser(p)
        try:
            with open(path, "rb") as f:
                d = plistlib.load(f)
            if int(d.get("apiPort", -1)) == port:
                s = d.get("api-secret")
                if s and s not in out:
                    out.append(s)
        except Exception:
            pass
    return out


def _probe(base: str, secret: str) -> bool:
    """探测某地址是否为可用的 Clash 控制接口。"""
    try:
        req = urllib.request.Request(base + "/version")
        if secret:
            req.add_header("Authorization", f"Bearer {secret}")
        with urllib.request.urlopen(req, timeout=2) as resp:
            d = json.loads(resp.read() or b"{}")
        return bool(d.get("version"))
    except Exception:
        return False


def resolve_controller():
    """返回 (base, secret)。环境变量优先,否则自动探测。"""
    if _ctl["base"]:
        return _ctl["base"], _ctl["secret"]
    if CONTROLLER:
        _ctl["base"], _ctl["secret"] = CONTROLLER.rstrip("/"), SECRET
        return _ctl["base"], _ctl["secret"]
    for port in CANDIDATE_PORTS:
        base = f"http://127.0.0.1:{port}"
        secrets = [SECRET] if SECRET else [""]
        for s in _plist_secrets(port):
            if s not in secrets:
                secrets.append(s)
        for s in secrets:
            if _probe(base, s):
                _ctl["base"], _ctl["secret"] = base, s
                log(f"自动发现控制接口: {base} ({'有密钥' if s else '无密钥'})")
                return _ctl["base"], _ctl["secret"]
    raise RuntimeError(
        "无法连接 Clash 控制接口。请用 CLASH_CONTROLLER / CLASH_SECRET 指定,"
        "并确认 Clash 的 External Controller 已开启。")


def _request(method: str, path: str, body: dict = None):
    """调用 Clash 外部控制接口。"""
    base, secret = resolve_controller()
    url = base + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if secret:
        req.add_header("Authorization", f"Bearer {secret}")
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------
def get_proxies() -> dict:
    return _request("GET", "/proxies").get("proxies", {})


def is_leaf(proxies: dict, name: str) -> bool:
    """是否是真实节点(而非组/策略/伪条目)。"""
    if name not in proxies:
        return False
    return proxies[name].get("type") not in GROUP_TYPES


def auto_detect_group(proxies: dict):
    """自动识别“主节点组”。

    启发式:在除 GLOBAL 外的 Selector 组里,取“真实节点数最多”者;
    若并列,再取“被其他组引用次数最多”者(即各分流组默认指向的那个)。
    实测:TAGSS 主组 ``🙂 TAGSS`` 与各分流组节点数并列,但被引用次数最多;
    TapFog 主组 ``TapFog`` 是唯一含真实节点的 select 组,均能正确命中。
    """
    selectors = [n for n, v in proxies.items()
                 if v.get("type") == "Selector" and n != "GLOBAL"]
    best, best_score = None, None
    for name in selectors:
        members = proxies[name].get("all", [])
        leaf = sum(1 for n in members if is_leaf(proxies, n))
        refs = sum(1 for k, v in proxies.items()
                   if v.get("type") in GROUP_TYPES and name in v.get("all", []))
        score = (leaf, refs)
        if best_score is None or score > best_score:
            best, best_score = name, score
    return best


def get_group(group: str) -> dict:
    key = urllib.parse.quote(group, safe="")
    return _request("GET", f"/proxies/{key}")


def is_us(name: str) -> bool:
    return any(k in name for k in US_KEYWORDS)


def is_allowed_multiplier(name: str) -> bool:
    """按倍率过滤节点名。X_FILTER 为空则不过滤;名称含 `Nx` 时仅保留 N == X_FILTER
    的节点;不含倍率标记(如 TapFog 的 Bronze/... 命名)则放行。"""
    if not X_FILTER:
        return True
    m = re.search(r"(\d+)x", name)
    if not m:
        return True
    return m.group(1) == X_FILTER


def measure_delay(name: str):
    """测单个节点延迟,失败/超时返回 None。"""
    key = urllib.parse.quote(name, safe="")
    url_q = urllib.parse.quote(DELAY_URL, safe="")
    path = f"/proxies/{key}/delay?url={url_q}&timeout={TIMEOUT_MS}"
    try:
        res = _request("GET", path)
        delay = res.get("delay", 0)
        return name, (delay if delay and delay > 0 else None)
    except Exception:
        return name, None


def measure_all(names: list) -> dict:
    """并发测速,返回 {节点名: 延迟ms},只保留成功项。"""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        for name, delay in pool.map(measure_delay, names):
            if delay:
                results[name] = delay
    return results


def select_node(group: str, name: str):
    key = urllib.parse.quote(group, safe="")
    _request("PUT", f"/proxies/{key}", {"name": name})


def notify(title: str, text: str):
    """macOS 系统通知(失败静默忽略)。"""
    try:
        script = f'display notification "{text}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass


def run_once(dry_run: bool, do_notify: bool, group_override: str = ""):
    proxies = get_proxies()

    group = group_override or GROUP or auto_detect_group(proxies)
    if not group:
        log("⚠️  无法确定主节点组(可用 --group 或 CLASH_GROUP 指定),跳过本次。")
        return

    try:
        g = get_group(group)
    except Exception as e:
        log(f"⚠️  读取节点组 {group!r} 失败({e}),跳过本次。")
        return

    now = g.get("now")
    members = g.get("all", [])

    all_us = [n for n in members if is_us(n)]
    us_nodes = [n for n in all_us if is_allowed_multiplier(n)]
    if X_FILTER and len(all_us) != len(us_nodes):
        log(f"按倍率过滤:{len(all_us)} 个美国节点 → 保留 {len(us_nodes)} 个 {X_FILTER}x 节点。")
    if not us_nodes:
        log(f"⚠️  组 {group!r} 中未找到符合条件({X_FILTER}x)的美国节点,跳过本次。")
        return

    log(f"主节点组: {group!r} | 当前节点: {now!r}")
    log(f"发现 {len(us_nodes)} 个美国节点,开始并发测速…")
    delays = measure_all(us_nodes)
    if not delays:
        log("⚠️  所有美国节点测速均失败/超时,保持当前节点不变。")
        return

    best = min(delays, key=delays.get)
    best_ms = delays[best]

    ranked = sorted(delays.items(), key=lambda kv: kv[1])
    for i, (n, d) in enumerate(ranked[:5], 1):
        mark = " ← 当前" if n == now else ""
        log(f"  {i}. {n}: {d} ms{mark}")

    if now == best:
        log(f"✅ 当前已是最低延迟节点,无需切换。({best_ms} ms)")
        return

    current_ms = delays.get(now)  # 当前节点若也是美国节点,才有可比延迟
    should_switch = True
    if current_ms is not None and best_ms >= current_ms - MIN_GAIN_MS:
        should_switch = False

    if dry_run:
        if should_switch:
            log(f"[dry-run] 将把 {group!r} 切换到: {best!r} ({best_ms} ms),实际未执行。")
        else:
            log(f"[dry-run] 最佳节点 {best!r} ({best_ms} ms) 相比当前 {now!r}"
                f" ({current_ms} ms) 提升不足 {MIN_GAIN_MS} ms,实际不会切换。")
        return

    if not should_switch:
        log(f"⏸️  最佳节点 {best!r} ({best_ms} ms) 相比当前 {now!r}"
            f" ({current_ms} ms) 提升不足 {MIN_GAIN_MS} ms,不切换以保持稳定。")
        return

    select_node(group, best)
    log(f"🔄 已把 {group!r} 切换到: {best!r} ({best_ms} ms)")
    if do_notify:
        notify("Clash 自动节点", f"已切换到 {best} ({best_ms} ms)")


def run_loop(interval: int, dry_run: bool, do_notify: bool, group_override: str):
    log(f"进入循环模式,每 {interval} 秒执行一次(Ctrl+C 退出)。")
    while True:
        try:
            run_once(dry_run, do_notify, group_override)
        except Exception as e:
            log(f"❌ 本轮出错: {e}")
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="Clash Meta 自动切换最低延迟美国节点")
    ap.add_argument("--once", action="store_true", help="只执行一次")
    ap.add_argument("--loop", action="store_true", help="常驻循环(默认)")
    ap.add_argument("--dry-run", action="store_true", help="只测速不切换")
    ap.add_argument("--notify", action="store_true", help="切换后发送 macOS 通知")
    ap.add_argument("--interval", type=int, default=INTERVAL_SEC, help="循环间隔秒数")
    ap.add_argument("--group", default=GROUP, help="指定主节点组名(默认自动识别)")
    args = ap.parse_args()

    try:
        if args.once:
            run_once(args.dry_run, args.notify, args.group)
        else:
            run_loop(args.interval, args.dry_run, args.notify, args.group)
    except KeyboardInterrupt:
        log("已退出。")
    except Exception as e:
        log(f"❌ 致命错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
