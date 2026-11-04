#!/usr/bin/env python3
"""產生週報 PDF（A4 直式 2 頁），不受瀏覽器字級、文字縮放等個人設定影響。

用法：
    python3 weekly_pdf.py               # 最近一個完整週
    python3 weekly_pdf.py 2026-09-07    # 指定週（該週任一天皆可）

原理：用 Chrome 無介面模式（全新暫存設定檔）開啟 公債市場觀測.html#print-week:週一，
直接列印成 PDF，版面與網頁「列印／存成 PDF」相同。輸出到 out/。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import bonddata as bd

HERE = Path(__file__).resolve().parent
HTML = HERE / "公債市場觀測.html"
OUT = HERE / "out"
CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def find_chrome() -> str:
    for c in CHROMES:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    sys.exit("找不到 Google Chrome（或 Edge／Chromium），無法產生 PDF。")


def pick_week(arg: str | None) -> tuple[date, date]:
    """與網頁相同的規則：沒指定就取最近一個完整週（週五已過，或資料已含週五）。"""
    days = sorted({date.fromisoformat(r["d"]) for r in bd.read_store()})
    if not days:
        sys.exit("本機還沒有資料檔，請先執行 update.py。")
    mondays = sorted({d - timedelta(days=d.weekday()) for d in days})
    if arg:
        d = date.fromisoformat(arg)
        mon = d - timedelta(days=d.weekday())
        if mon not in mondays:
            sys.exit(f"{mon} 那一週沒有資料。")
    else:
        today = date.today()
        mon = mondays[-1]
        for m in reversed(mondays):
            fri = m + timedelta(days=4)
            last = max(x for x in days if m <= x <= fri)
            if fri < today or last >= fri:
                mon = m
                break
    return mon, mon + timedelta(days=4)


def main(argv):
    if not HTML.exists():
        sys.exit("找不到 公債市場觀測.html，請先執行 update.py。")
    mon, fri = pick_week(argv[0] if argv else None)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"公債週報_{mon:%Y%m%d}-{fri:%Y%m%d}.pdf"
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory() as prof:            # 全新設定檔：不帶入個人字級設定
        cmd = [find_chrome(), "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", f"--user-data-dir={prof}", "--no-pdf-header-footer",
               "--virtual-time-budget=5000", f"--print-to-pdf={out}",
               f"{HTML.as_uri()}#print-week:{mon.isoformat()}"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 有些環境 Chrome 印完不會自己結束：等 PDF 寫完（大小不再變動）就收掉
        last, stable, t0 = -1, 0, time.time()
        while time.time() - t0 < 90:
            if proc.poll() is not None and out.exists():
                break
            size = out.stat().st_size if out.exists() else -1
            stable = stable + 1 if size > 0 and size == last else 0
            if stable >= 3:
                break
            last = size
            time.sleep(0.5)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    if not out.exists():
        sys.exit("PDF 產生失敗。")
    print(f"已產生：{out}")


if __name__ == "__main__":
    main(sys.argv[1:])
