"""One-shot latency bench — curl x N to a target host, parse timing JSON, print p50/p95.

Usage: python scripts/latency_bench.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time

HOST = "https://swer-etf.zeabur.app"
N = 10

ENDPOINTS = [
    ("/",                                                     "GET /"),
    ("/docs",                                                 "GET /docs"),
    ("/api/etf/search?q=&limit=20&code_only=1",               "GET /api/etf/search (empty)"),
    ("/api/etf/search?q=0050&limit=20&code_only=1",           "GET /api/etf/search?q=0050"),
    ("/compare?codes=0050&start=2025-04-29&end=2026-04-29",   "GET /compare"),
    ("/etf/0050",                                             "GET /etf/0050"),
    ("/news",                                                 "GET /news"),
    ("/static/img/logo.svg",                                  "GET /static/img/logo.svg"),
]

# curl -w 拿全 phase timing,輸出 JSON 一行
W_FORMAT = (
    '{"dns":%{time_namelookup},'
    '"connect":%{time_connect},'
    '"appconnect":%{time_appconnect},'
    '"starttransfer":%{time_starttransfer},'
    '"total":%{time_total},'
    '"http":%{http_code},'
    '"size":%{size_download},'
    '"speed":%{speed_download}}'
)


def run_curl(url: str) -> dict | None:
    try:
        # --compressed: 帶 Accept-Encoding gzip,br,自動解壓 — 看 server 是否真的壓縮
        r = subprocess.run(
            ["curl", "-s", "--compressed", "-o", "/dev/null", "-w", W_FORMAT, url],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None
    out = (r.stdout or "").strip()
    if not out:
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    # curl 給的數值單位:秒 → 換 ms,並把累積值轉成各 phase 獨立時間
    dns = d["dns"]
    connect = d["connect"]
    appconnect = d["appconnect"]
    ttfb = d["starttransfer"]
    total = d["total"]
    return {
        "dns_ms":      dns * 1000,
        "tcp_ms":      max(0.0, (connect - dns)) * 1000,
        "tls_ms":      max(0.0, (appconnect - connect)) * 1000,
        "wait_ms":     max(0.0, (ttfb - appconnect)) * 1000,   # server processing(TTFB - TLS done)
        "ttfb_ms":     ttfb * 1000,                            # 累積:DNS+TCP+TLS+server
        "transfer_ms": max(0.0, (total - ttfb)) * 1000,
        "total_ms":    total * 1000,
        "http":        d["http"],
        "size":        d["size"],
    }


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def fmt_row(name: str, p50: float, p95: float) -> str:
    return f"  {name:<14} p50={p50:7.1f} ms  p95={p95:7.1f} ms"


def bench(path: str, label: str) -> None:
    url = HOST + path
    print(f"\n=== {label}  ({url}) ===")
    samples = []
    for i in range(N):
        d = run_curl(url)
        if d is None:
            print(f"  [{i+1}] timeout / parse fail")
            continue
        samples.append(d)
        # 短 cooldown 避免 keep-alive 把後續 connect 全變 0
        time.sleep(0.3)
    if not samples:
        print("  ALL FAIL")
        return

    keys = ["dns_ms", "tcp_ms", "tls_ms", "wait_ms", "ttfb_ms", "transfer_ms", "total_ms"]
    label_map = {
        "dns_ms":      "DNS",
        "tcp_ms":      "TCP",
        "tls_ms":      "TLS",
        "wait_ms":     "Server(TTFB-TLS)",
        "ttfb_ms":     "TTFB(累積)",
        "transfer_ms": "Transfer",
        "total_ms":    "Total",
    }
    http_codes = sorted({s["http"] for s in samples})
    sizes = sorted({s["size"] for s in samples})
    print(f"  N={len(samples)}  http={http_codes}  size={sizes} bytes")
    for k in keys:
        vals = [s[k] for s in samples]
        print(fmt_row(label_map[k], pct(vals, 0.5), pct(vals, 0.95)))


def main():
    print(f"Target: {HOST}")
    print(f"Samples per endpoint: {N}")
    print(f"curl: {subprocess.run(['curl', '--version'], capture_output=True, text=True).stdout.splitlines()[0]}")
    for path, label in ENDPOINTS:
        bench(path, label)


if __name__ == "__main__":
    main()
