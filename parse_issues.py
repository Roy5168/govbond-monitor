#!/usr/bin/env python3
"""取得各期公債的官方發行日與年期，寫進 data/issues.csv。

預設來源是中央銀行開放資料的「中央公債標售概況表」CSV，每有新券標售就會更新：
    https://www.cbc.gov.tw/public/data/OpenData/國庫局/債券科/Bond-Auction-Results.csv
連不到網路時，也可以改吃財政部「中央公債標售概況表」PDF（欄位相同）。

用法：
    python3 parse_issues.py                    # 抓官方 CSV（建議）
    python3 parse_issues.py 標售概況表.pdf      # 改用本機 PDF
    python3 parse_issues.py 來源 輸出.csv       # 指定輸出位置

期別對照券號：A ＋ 民國年後兩碼 ＋ 類別（甲=1、乙=2）＋ 序號兩碼，例如 115甲8 → A15108。
期別前的 ※ 是增額發行（不改首次發行日，只記最近一次增額）；Δ／△ 是可分割公債。
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "issues.csv"
CSV_URL = ("https://www.cbc.gov.tw/public/data/OpenData/"
           "%E5%9C%8B%E5%BA%AB%E5%B1%80/%E5%82%B5%E5%88%B8%E7%A7%91/Bond-Auction-Results.csv")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

PERIOD = re.compile(r"^([※Δ△\s]*)(\d{2,3})([甲乙])(\d{1,2})$")
ROC_DATE = re.compile(r"^(\d{2,3})[.\-/](\d{1,2})[.\-/](\d{1,2})$")
PDF_ROW = re.compile(r"^\s*([※Δ△]*)\s*(\d{2,3})([甲乙])(\d{1,2})\s+"
                     r"(\d{2,3})\.(\d{2})\.(\d{2})\s+(\d{1,2})\s", re.M)
PDF_UPDATED = re.compile(r"更新日期：(\d{2,3})年(\d{1,2})月(\d{1,2})日")


def code_of(roc_year: str, cls: str, seq: str) -> str:
    return f"A{int(roc_year) % 100:02d}{1 if cls == '甲' else 2}{int(seq):02d}"


def iso_of(roc_y: int, m: int, d: int) -> str:
    return f"{1911 + roc_y}-{m:02d}-{d:02d}"


# ────────────────────────── 來源：央行開放資料 CSV ──────────────────────────
def rows_from_csv(blob: bytes):
    text = blob.decode("utf-8-sig", errors="replace")
    for r in csv.DictReader(io.StringIO(text)):
        p = PERIOD.match((r.get("期別") or "").strip())
        d = ROC_DATE.match((r.get("發行日期") or "").strip())
        if not p or not d:
            continue
        mark, yy, cls, seq = p.groups()
        try:
            tenor = int(float(r.get("年期") or 0))
        except ValueError:
            continue
        if not tenor:
            continue
        yield dict(code=code_of(yy, cls, seq), period=f"{yy}{cls}{seq}",
                   tenor=tenor,
                   day=iso_of(int(d.group(1)), int(d.group(2)), int(d.group(3))),
                   reopen="※" in mark)


def fetch_csv() -> bytes:
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


# ────────────────────────── 來源：標售概況表 PDF ──────────────────────────
def pdf_text(pdf: Path) -> str:
    if shutil.which("pdftotext"):
        return subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                              capture_output=True, text=True, check=True).stdout
    try:
        import pdfplumber
    except ImportError:
        sys.exit("需要 pdftotext（brew install poppler）或 Python 套件 pdfplumber")
    with pdfplumber.open(pdf) as doc:
        return "\n".join(p.extract_text() or "" for p in doc.pages)


def rows_from_pdf(text: str):
    for mark, yy, cls, seq, dy, dm, dd, tenor in PDF_ROW.findall(text):
        yield dict(code=code_of(yy, cls, seq), period=f"{yy}{cls}{seq}", tenor=int(tenor),
                   day=iso_of(int(dy), int(dm), int(dd)), reopen="※" in mark)


# ────────────────────────── 彙整並輸出 ──────────────────────────
def collect(rows):
    """同一期會有首次發行與多次增額；首次發行日取最早的非增額列。"""
    issues: dict[str, dict] = {}
    for r in rows:
        e = issues.setdefault(r["code"], {"period": r["period"], "tenor": r["tenor"],
                                          "issue": r["day"], "reopens": []})
        if r["reopen"]:
            e["reopens"].append(r["day"])
        else:
            if r["day"] < e["issue"]:
                e["issue"] = r["day"]
            e["tenor"] = r["tenor"]
    return issues


def write(issues: dict, out: Path, source: str, updated: str):
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "period", "tenor", "issue", "reopens", "updated", "source"])
        for code in sorted(issues):
            e = issues[code]
            w.writerow([code, e["period"], e["tenor"], e["issue"],
                        ";".join(sorted(e["reopens"])), updated, source])


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    src = Path(args[0]).expanduser() if args and args[0].lower().endswith(".pdf") else None
    out = Path(args[1]).expanduser() if len(args) > 1 else OUT

    if src:
        issues = collect(rows_from_pdf(pdf_text(src)))
        m = PDF_UPDATED.search(pdf_text(src))
        updated = iso_of(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else ""
        source = f"標售概況表 PDF（{src.name}）"
    else:
        issues = collect(rows_from_csv(fetch_csv()))
        updated = date.today().isoformat()
        source = "中央銀行開放資料 Bond-Auction-Results.csv"

    if not issues:
        sys.exit("來源中找不到「期別＋發行日期＋年期」的資料列，格式可能改了")

    latest = max(e["issue"] for e in issues.values())
    write(issues, out, source, updated)
    print(f"來源：{source}")
    print(f"輸出：{out}（{len(issues)} 期，發行日 "
          f"{min(e['issue'] for e in issues.values())} ～ {latest}）")
    print(f"最新一期：" + "、".join(
        f"{e['period']}（{c}）{e['issue']}"
        for c, e in sorted(issues.items(), key=lambda kv: kv[1]["issue"])[-3:]))
    print(f"增額發行紀錄 {sum(len(e['reopens']) for e in issues.values())} 筆")
    return issues


def refresh(quiet: bool = True):
    """給 update.py 呼叫：抓最新官方資料，失敗就沿用現有檔案。"""
    try:
        issues = collect(rows_from_csv(fetch_csv()))
        if not issues:
            raise ValueError("來源沒有可用資料列")
        write(issues, OUT, "中央銀行開放資料 Bond-Auction-Results.csv", date.today().isoformat())
        if not quiet:
            print(f"官方發行資料已更新：{len(issues)} 期")
        return issues
    except Exception as e:                                   # noqa: BLE001 - 沒網路就沿用舊檔
        print(f"（官方發行資料更新失敗，沿用現有 {OUT.name}：{e}）")
        return None


if __name__ == "__main__":
    main(sys.argv[1:])
