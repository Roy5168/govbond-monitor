#!/usr/bin/env python3
"""公債買賣斷資料的共用處理：讀檔、合併、存檔、產生 HTML。

資料一律轉成同一組單位再存：
  殖利率、百元價 → 百分比（1.205 表示 1.205%）
  成交面額、成交金額 → 億元
  處所筆數 → 整數（等殖成交系統日檔未揭露，一律填 0）
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
STORE = HERE / "data" / "records.csv"
ISSUES = HERE / "data" / "issues.csv"   # 官方發行日（parse_issues.py 由標售概況表產生）
DEFAULT_OUT = HERE / "公債市場觀測.html"

MARKETS = ["營業處所", "等殖成交系統"]
GOVBOND = re.compile(r"^A\d{5}R?$")

FIELDS = ["d", "code", "name", "mk", "dur", "rem",
          "plo", "phi", "pavg", "yhi", "ylo", "yavg", "amt", "face", "otc"]


# ────────────────────────── 小工具 ──────────────────────────
def num(v, default=None):
    """把日檔裡的 '-'、''、全形空白等雜訊轉成 None。"""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return default if v != v else float(v)      # NaN → default
    s = str(v).strip().replace(",", "").replace("　", "")
    if s in ("", "-", "－", "N/A", "nan"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def rnd(v, nd):
    return None if v is None else round(float(v), nd)


def roc_to_iso(s: str):
    """民國日期字串（115/08/21）轉西元 ISO。也接受 2026/08/21。"""
    m = re.search(r"(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})", str(s))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1911:
        y += 1911
    return f"{y:04d}-{mo:02d}-{d:02d}"


def key(row):
    return (row["d"], row["code"], row["mk"])


# ── 券別屬性：年期與推估發行日（用來判斷指標券） ──────────────────
STD_TENORS = [2, 3, 5, 7, 10, 15, 20, 30]     # 台債曾發行過的標準年期
CODE = re.compile(r"^A(\d{2})(\d)(\d{2})(R?)$")


def bond_profile(code: str, trade_day: str, rem: float):
    """由券號與某日的剩餘年限，推估原始年期與發行日。

    券號規則：A + 民國年(2) + 類別(1 為甲類、2 為乙類) + 序號(2)，尾碼 R 為發行前交易。
    到期日 ≈ 交易日 + 剩餘年限；年期取到期日與發行年度距離最接近的標準年期；
    發行日 ≈ 到期日 − 年期。台債標準年期相隔夠遠，這個推估足以判斷指標券。
    """
    m = CODE.match(code)
    if not m or rem is None:
        return None, None, False, None
    yy = int(m.group(1))
    roc = yy + 100 if yy < 50 else yy
    issue_year = 1911 + roc
    wi = bool(m.group(4))
    d = date.fromisoformat(trade_day)
    mat = d.year + d.timetuple().tm_yday / 365.25 + rem
    # span 是「到期日 − 發行『年度起點』」，必然等於年期加上當年已過的比例（0～1），
    # 所以要取不超過 span 的最大標準年期，取最接近的會把 2.5 年判成 3 年期。
    span = mat - issue_year
    tenor = max([t for t in STD_TENORS if t <= span + 0.15], default=STD_TENORS[0])
    iss = mat - tenor                       # 發行日（小數年）
    y = int(iss)
    doy = max(1, min(365, round((iss - y) * 365.25)))
    return tenor, (date(y, 1, 1) + timedelta(days=doy - 1)).isoformat(), wi, int(m.group(2))


def canon(row):
    """統一小數位數，讓彙整檔與日檔兩條路徑產生完全一致的數值。"""
    for f in ("dur", "rem"):
        row[f] = rnd(row[f], 3)
    for f in ("plo", "phi", "pavg"):
        row[f] = rnd(row[f], 4)
    for f in ("yhi", "ylo", "yavg"):
        row[f] = rnd(row[f], 6)
    for f in ("amt", "face"):
        row[f] = rnd(row[f], 6)
    row["otc"] = int(row["otc"] or 0)
    return row


def read_issues(path: Path = ISSUES):
    """官方標售概況表整理出的發行日與年期；沒有這個檔就全部回退到推估。"""
    if not path.exists():
        return {}, ""
    out, updated = {}, ""
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out[r["code"]] = {"period": r["period"], "tenor": int(r["tenor"]),
                              "issue": r["issue"],
                              "reopens": [x for x in (r.get("reopens") or "").split(";") if x]}
            updated = r.get("updated") or updated
    return out, updated


# ────────────────────── 讀取：櫃買日檔（.xls） ──────────────────────
def parse_daily_xls(blob: bytes, market: str, expect_date: str | None = None):
    """解析櫃買買賣斷日檔。市場別為「營業處所」（BDdcs001）或「等殖成交系統」（BDdys01a）。"""
    import xlrd

    wb = xlrd.open_workbook(file_contents=blob)
    sheet_name = "BDdcs01a" if market == MARKETS[0] else None   # 處所檔第二張表是附條件，不能拿
    sh = wb.sheet_by_name(sheet_name) if sheet_name in wb.sheet_names() else wb.sheet_by_index(0)

    day = None
    for r in range(min(6, sh.nrows)):
        for c in sh.row_values(r):
            if "日期" in str(c):
                day = roc_to_iso(c)
                break
        if day:
            break
    if not day:
        raise ValueError("日檔中找不到日期")
    if expect_date and day != expect_date:
        raise ValueError(f"日檔日期為 {day}，與指定的 {expect_date} 不符")

    rows = []
    for r in range(sh.nrows):
        v = sh.row_values(r)
        code = str(v[0]).strip()
        if not GOVBOND.match(code):
            continue
        if market == MARKETS[0]:      # 營業處所：面額與金額為「元」，有成交筆數
            if str(v[2]).strip() != "TWD":
                continue
            face, amt = num(v[13]), num(v[12])
            row = dict(d=day, code=code, name=str(v[1]).strip(), mk=market,
                       dur=num(v[3]), rem=num(v[4]),
                       plo=num(v[5]), phi=num(v[6]), pavg=num(v[7]),
                       yhi=num(v[8]), ylo=num(v[9]), yavg=num(v[10]),
                       amt=None if amt is None else amt / 1e8,
                       face=None if face is None else face / 1e8,
                       otc=int(num(v[14], 0)))
        else:                          # 等殖成交系統：面額為「億」，金額為「元」，未揭露筆數
            amt = num(v[14])
            row = dict(d=day, code=code, name=str(v[1]).strip(), mk=market,
                       dur=num(v[2]), rem=num(v[3]),
                       plo=num(v[4]), phi=num(v[5]), pavg=num(v[6]),
                       yhi=num(v[9]), ylo=num(v[10]), yavg=num(v[11]),
                       amt=None if amt is None else amt / 1e8,
                       face=num(v[15]), otc=0)
        if row["face"] is None or row["yavg"] is None:
            continue
        rows.append(canon(row))
    return day, rows


# ────────────────── 讀取：GPT 彙整的 xlsx（底稿） ──────────────────
def parse_source_xlsx(path: Path):
    """讀「交易明細」工作表；殖利率在該檔為小數，這裡一律轉成百分比。"""
    import pandas as pd

    xl = pd.ExcelFile(path)
    det = xl.parse("交易明細", header=2).dropna(subset=["交易日"])
    det["交易日"] = pd.to_datetime(det["交易日"])
    rows = []
    for _, r in det.iterrows():
        rows.append(dict(
            d=r["交易日"].strftime("%Y-%m-%d"),
            code=str(r["債券代號"]).strip(), name=str(r["債券名稱"]).strip(),
            mk=str(r["市場別"]).strip(),
            dur=num(r["存續期間（年）"]), rem=num(r["剩餘年限（年）"]),
            plo=num(r["百元價－最低"]), phi=num(r["百元價－最高"]), pavg=num(r["百元價－加權平均"]),
            yhi=num(r["殖利率－最高"]) * 100 if num(r["殖利率－最高"]) is not None else None,
            ylo=num(r["殖利率－最低"]) * 100 if num(r["殖利率－最低"]) is not None else None,
            yavg=num(r["殖利率－加權平均"]) * 100,
            amt=num(r["成交金額（億元）"]), face=num(r["成交面額（億元）"]),
            otc=int(num(r["處所成交筆數"], 0))))
    rows = [canon(r) for r in rows]
    checks = []
    if "檢核" in xl.sheet_names:
        chk = xl.parse("檢核", header=2).dropna(subset=["檢核項目"])
        checks = [{"item": str(x["檢核項目"]), "actual": str(x["實際值"]),
                   "expect": str(x["預期值"]), "status": str(x["狀態"])}
                  for _, x in chk.iterrows()]
    return rows, checks


# ────────────────────────── 資料檔存取 ──────────────────────────
def read_store(path: Path = STORE):
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out.append(dict(d=r["d"], code=r["code"], name=r["name"], mk=r["mk"],
                            dur=num(r["dur"]), rem=num(r["rem"]),
                            plo=num(r["plo"]), phi=num(r["phi"]), pavg=num(r["pavg"]),
                            yhi=num(r["yhi"]), ylo=num(r["ylo"]), yavg=num(r["yavg"]),
                            amt=num(r["amt"]), face=num(r["face"]), otc=int(num(r["otc"], 0))))
    return out


def write_store(rows, path: Path = STORE):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["d"], r["code"], r["mk"]))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})
    return len(rows)


def merge(old, new):
    """以（交易日, 券號, 市場別）為鍵覆蓋合併；回傳合併結果與新增/更新筆數。"""
    m = {key(r): r for r in old}
    added = updated = 0
    for r in new:
        k = key(r)
        if k in m:
            if m[k] != r:
                updated += 1
        else:
            added += 1
        m[k] = r
    return list(m.values()), added, updated


# ────────────────────────── 產生 HTML ──────────────────────────
def build_payload(rows, src_label, extra_checks=None):
    rows = sorted(rows, key=lambda r: (r["d"], r["code"], r["mk"]))
    dates = sorted({r["d"] for r in rows})
    didx = {d: i for i, d in enumerate(dates)}
    codes = sorted({r["code"] for r in rows})
    bidx = {c: i for i, c in enumerate(codes)}

    issues, issues_updated = read_issues()
    bonds = [dict(c=c, n="", rem=0, dur=0, days=0, face=0.0, first=0, last=0,
                  t=None, iss=None, wi=0, cls=0, src="est") for c in codes]
    lastseen = {}
    seen = {c: set() for c in codes}
    recs = []
    markets = list(MARKETS)
    for r in rows:
        bi = bidx[r["code"]]
        b = bonds[bi]
        b["n"] = r["name"] or b["n"]
        b["rem"], b["dur"] = r["rem"], r["dur"]
        lastseen[r["code"]] = r
        b["face"] += r["face"]
        seen[r["code"]].add(r["d"])
        if r["mk"] not in markets:
            markets.append(r["mk"])
        recs.append([didx[r["d"]], bi, markets.index(r["mk"]),
                     rnd(r["face"], 4), rnd(r["amt"], 4),
                     rnd(r["yavg"], 6), rnd(r["ylo"], 6), rnd(r["yhi"], 6),
                     rnd(r["pavg"], 4), rnd(r["rem"], 3), rnd(r["dur"], 3), int(r["otc"] or 0)])
    for c, b in zip(codes, bonds):
        ds = sorted(seen[c])
        b["days"] = len(ds)
        b["first"], b["last"] = didx[ds[0]], didx[ds[-1]]
        b["face"] = rnd(b["face"], 3)
        b["rem"], b["dur"] = rnd(b["rem"], 3), rnd(b["dur"], 3)
        lr = lastseen[c]
        tenor, iss, wi, cls = bond_profile(c, lr["d"], lr["rem"])
        base = c[:-1] if c.endswith("R") else c          # 發行前交易沿用本券的發行資料
        off = issues.get(base)
        if off:                                           # 官方標售概況表優先
            b["t"], b["iss"], b["src"] = off["tenor"], off["issue"], "off"
            if off["reopens"]:
                b["reopen"] = off["reopens"][-1]
        else:
            b["t"], b["iss"], b["src"] = tenor, iss, "est"
        b["wi"], b["cls"] = int(wi), cls or 0

    tot_face = sum(r["face"] for r in rows)
    tot_amt = sum(r["amt"] or 0 for r in rows)
    checks = [
        {"item": "資料列數", "actual": str(len(recs)), "expect": str(len(recs)), "status": "OK"},
        {"item": "涵蓋交易日數", "actual": str(len(dates)), "expect": str(len(dates)), "status": "OK"},
        {"item": "最新資料日", "actual": dates[-1], "expect": dates[-1], "status": "OK"},
        {"item": "營業處所列數", "actual": str(sum(1 for r in rows if r["mk"] == MARKETS[0])), "expect": "—", "status": "OK"},
        {"item": "等殖成交系統列數", "actual": str(sum(1 for r in rows if r["mk"] == MARKETS[1])), "expect": "—", "status": "OK"},
        {"item": "成交面額合計（億元）", "actual": f"{tot_face:,.2f}", "expect": "—", "status": "OK"},
        {"item": "官方發行日涵蓋", "actual": f"{sum(1 for b in bonds if b['src'] == 'off')} / {len(bonds)} 檔",
         "expect": "—", "status": "OK"},
        {"item": "標售概況表更新日期", "actual": issues_updated or "（未載入）", "expect": "—", "status": "OK"},
    ] + list(extra_checks or [])

    meta = dict(start=dates[0], end=dates[-1],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                src=src_label, rows=len(recs),
                totFace=rnd(tot_face, 3), totAmt=rnd(tot_amt, 3),
                totOtc=int(sum(r["otc"] or 0 for r in rows)),
                wy=rnd(sum(r["yavg"] * r["face"] for r in rows) / tot_face, 4),
                nDays=len(dates), nBonds=len(bonds),
                checks=checks, checkFail=sum(1 for c in checks if c["status"] != "OK"))
    meta["issuesUpdated"] = issues_updated
    # 官方發行資料一併帶進頁面，匯入他人檔案時也用得到（只信任這份，不信匯入檔）
    issue_map = {c: {"t": e["tenor"], "iss": e["issue"],
                     "re": e["reopens"][-1] if e["reopens"] else ""} for c, e in issues.items()}
    return dict(meta=meta, dates=dates, bonds=bonds, recs=recs, markets=markets, issues=issue_map)


# frame-ancestors 只有透過 HTTP 回應標頭才生效，放在 <meta> 會被瀏覽器忽略並警告，
# 所以這裡不放；本工具是本機 file:// 開啟，沒有被嵌入的情境。
# 若日後要放到網站上，請在伺服器加上：
#   Content-Security-Policy: ...; frame-ancestors 'none'
#   X-Frame-Options: DENY
CSP_TMPL = ('<meta http-equiv="Content-Security-Policy" content="'
            "default-src 'none'; script-src '{hash}'; style-src 'unsafe-inline'; "
            "img-src data:; font-src 'none'; connect-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'"
            '">')


EDITIONS = ("full", "public")
ADMIN_BLOCK = re.compile(r"<!--ADMIN\{-->.*?<!--\}ADMIN-->", re.S)


def render_html(payload, edition: str = "full") -> str:
    """把資料內嵌進 template，並依內嵌後的 script 內容計算 CSP hash，回傳整頁 HTML。

    頁面沒有任何外部資源，CSP 只允許這一段自己的 script 執行；就算未來某處
    escape 漏掉，插入的 inline handler 也不會被執行。

    edition：full＝完整版（本機與管理者版）；public＝一般版（公開網址），
    template 中 <!--ADMIN{--> … <!--}ADMIN--> 包起來的區塊（匯出／匯入、資料說明與檢核）整段拿掉。
    """
    if edition not in EDITIONS:
        raise ValueError(f"edition 只能是 {EDITIONS}")
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("/*__EDITION__*/'full'", json.dumps(edition), 1)   # 舊版 template 沒有這個標記，照樣可產
    if edition == "public":
        html = ADMIN_BLOCK.sub("", html)
    html = html.replace("<!--ADMIN{-->", "").replace("<!--}ADMIN-->", "")

    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    if len(scripts) != 1:
        raise SystemExit(f"template 應該只有一段 inline script，實際有 {len(scripts)} 段，CSP hash 會失效")
    html = html.replace("<!--__CSP__-->", CSP_TMPL.format(hash=script_hash(scripts[0])), 1)
    return html


def script_hash(js: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(js.encode("utf-8")).digest()).decode()


def render(payload, out: Path = DEFAULT_OUT, edition: str = "full"):
    out.write_text(render_html(payload, edition), encoding="utf-8")
    return out


def summary(payload):
    m = payload["meta"]
    return (f"期間 {m['start']} ～ {m['end']}｜{m['nDays']} 個交易日｜{m['nBonds']} 檔券"
            f"｜{m['rows']} 筆明細｜成交面額 {m['totFace']:,.0f} 億元")
