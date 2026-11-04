#!/usr/bin/env python3
"""用彙整好的 xlsx 建立本機資料檔底稿，並產生單檔 HTML。

用法:
    python3 build.py                       # 自動抓 ~/Downloads 最新的「台灣公債買賣斷交易彙整_*.xlsx」
    python3 build.py 某個檔案.xlsx          # 指定來源
    python3 build.py 來源.xlsx 輸出.html    # 指定來源與輸出

之後每天的更新請用 update.py（直接向櫃買中心抓日檔）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import bonddata as bd

DOWNLOADS = Path.home() / "Downloads"


def find_source() -> Path:
    cands = sorted(DOWNLOADS.glob("台灣公債買賣斷交易彙整_*.xlsx"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        sys.exit(f"找不到來源檔：{DOWNLOADS}/台灣公債買賣斷交易彙整_*.xlsx")
    return cands[0]


def main(argv):
    src = Path(argv[0]).expanduser() if argv else find_source()
    out = Path(argv[1]).expanduser() if len(argv) > 1 else bd.DEFAULT_OUT

    rows, checks = bd.parse_source_xlsx(src)
    # 與來源檔「檢核」表對帳：來源被改壞就直接停下，不要靜靜產出錯的圖
    exp = next((c for c in checks if c["item"] == "交易明細列數"), None)
    if exp and str(int(float(exp["expect"]))) != str(len(rows)):
        sys.exit(f"列數與來源檢核表不符：實際 {len(rows)}，檢核表預期 {exp['expect']}")

    merged, added, updated = bd.merge(bd.read_store(), rows)
    n = bd.write_store(merged)
    payload = bd.build_payload(merged, src.name, extra_checks=checks)
    bd.render(payload, out)

    print(f"來源：{src}")
    print(f"資料檔：{bd.STORE}（新增 {added} 筆、更新 {updated} 筆，共 {n} 筆）")
    print(f"輸出：{out}  ({out.stat().st_size/1024:.0f} KB)")
    print(bd.summary(payload))
    bad = [c for c in checks if c["status"] != "OK"]
    if bad:
        print("⚠️ 來源檢核有非 OK 項目：" + "、".join(c["item"] for c in bad))


if __name__ == "__main__":
    main(sys.argv[1:])
