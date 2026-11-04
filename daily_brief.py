#!/usr/bin/env python3
"""輸出「台債當日行情」快照，給不能連外網的 AI 或報告直接使用。

用法：
    python3 daily_brief.py                 # 最新交易日
    python3 daily_brief.py 2026-09-04      # 指定日期
    python3 daily_brief.py --days 5        # 最近 5 個交易日各出一份

產出（同時印到畫面，方便直接複製貼上）：
    out/台債當日行情_YYYYMMDD.md    給 AI 讀的摘要
    out/台債當日行情_YYYYMMDD.csv   當日成交明細
"""
from __future__ import annotations

import sys
from pathlib import Path

import bonddata as bd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
WIN = 10          # 參考曲線回溯期間（交易日），與網頁工具預設一致
BENCH_TENORS = [2, 5, 10, 20, 30]


def load():
    rows = bd.read_store()
    issues, _ = bd.read_issues()
    dates = sorted({r["d"] for r in rows})
    by_date = {d: [] for d in dates}
    for r in rows:
        by_date[r["d"]].append(r)
    return rows, dates, by_date, issues


def merge_markets(day_rows):
    """同一天同一檔券的兩個市場合併成一筆（面額加權）。"""
    out = {}
    for r in day_rows:
        o = out.setdefault(r["code"], dict(code=r["code"], name=r["name"], rem=r["rem"],
                                           face=0.0, wsum=0.0, otc=0, mk=set()))
        o["face"] += r["face"]
        o["wsum"] += r["yavg"] * r["face"]
        o["otc"] += r["otc"] or 0
        o["mk"].add(r["mk"])
        o["rem"] = r["rem"]
    for o in out.values():
        o["y"] = o["wsum"] / o["face"]
    return sorted(out.values(), key=lambda o: -o["face"])


def curve_nodes(dates, by_date, i, exclude=None):
    """回溯期間內每檔券最近一次成交，作為參考曲線節點（排除發行前 R 券與指定券）。"""
    seen = {}
    for j in range(i, max(-1, i - WIN), -1):
        for o in merge_markets(by_date[dates[j]]):
            if o["code"].endswith("R") or o["code"] == exclude:
                continue
            seen.setdefault(o["code"], o)
    pts = sorted(((o["rem"], o["y"], o["face"]) for o in seen.values()), key=lambda x: x[0])
    merged = []                       # 相近年限併點，位置與殖利率都用面額加權
    for x, y, w in pts:
        if merged and abs(x - merged[-1][0]) < 0.1:
            px, py, pw = merged[-1]
            merged[-1] = ((px * pw + x * w) / (pw + w), (py * pw + y * w) / (pw + w), pw + w)
        else:
            merged.append((x, y, w))
    return [(x, y) for x, y, _ in merged]


def interp(nodes, x):
    if len(nodes) < 2 or x < nodes[0][0] or x > nodes[-1][0]:
        return None
    for k in range(1, len(nodes)):
        if x <= nodes[k][0]:
            (x0, y0), (x1, y1) = nodes[k - 1], nodes[k]
            return y1 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


def bond_meta(rows, issues):
    """每檔券的年期與發行日：官方標售表優先，查不到才由券號＋剩餘年限推估。

    標售表是定期更新的快照，最新標售的券（例如 2026-08 才發行的 115甲8）還不在表上，
    只用官方檔會漏掉現行指標券——網頁工具也是這套規則，兩邊要一致。
    """
    last = {}
    for r in rows:
        if r["code"] not in last or r["d"] > last[r["code"]]["d"]:
            last[r["code"]] = r
    meta = {}
    for code, r in last.items():
        base = code[:-1] if code.endswith("R") else code
        off = issues.get(base)
        if off:
            meta[code] = dict(tenor=off["tenor"], issue=off["issue"], src="官方")
        else:
            t, iss, _wi, _cls = bd.bond_profile(code, r["d"], r["rem"])
            if t:
                meta[code] = dict(tenor=t, issue=iss, src="推估")
    return meta


def benchmarks(meta, day):
    """當日各天期指標券＝發行日已到、發行未滿 3 年、最近發行的那一檔（不含發行前 R 券）。"""
    cut = f"{int(day[:4]) - 3}{day[4:]}"
    best = {}
    for code, e in meta.items():
        if code.endswith("R") or e["tenor"] not in BENCH_TENORS:
            continue
        if not (cut <= e["issue"] <= day):
            continue
        cur = best.get(e["tenor"])
        if cur is None or e["issue"] > cur[1]:
            best[e["tenor"]] = (code, e["issue"])
    return {t: c for t, (c, _) in best.items()}


def brief(day: str, dates, by_date, meta):
    i = dates.index(day)
    prev = dates[i - 1] if i else None
    today = merge_markets(by_date[day])
    face = sum(o["face"] for o in today)
    wy = sum(o["y"] * o["face"] for o in today) / face
    otc = sum(o["otc"] for o in today)
    pf = pwy = None
    if prev:
        p = merge_markets(by_date[prev])
        pf = sum(o["face"] for o in p)
        pwy = sum(o["y"] * o["face"] for o in p) / pf
    bench = benchmarks(meta, day)
    bench_by_code = {c: t for t, c in bench.items()}

    # 每檔券：對曲線利差（排除本券）、較上次成交變動
    last_seen = {}
    for j in range(i):
        for o in merge_markets(by_date[dates[j]]):
            last_seen[o["code"]] = (dates[j], o["y"])
    rows = []
    for o in today:
        sp = None
        c = interp(curve_nodes(dates, by_date, i, exclude=o["code"]), o["rem"])
        if c is not None:
            sp = (o["y"] - c) * 100
        pr = last_seen.get(o["code"])
        rows.append(dict(o, spread=sp, prev_y=pr[1] if pr else None, prev_d=pr[0] if pr else None,
                         kind=("指標券" if o["code"] in bench_by_code
                               else "發行前" if o["code"].endswith("R") else "非指標券"),
                         tenor=bench_by_code.get(o["code"])))

    # 天期參考行情：以指標券當日成交為準，沒成交就註明最近一次
    nodes = curve_nodes(dates, by_date, i)
    tenor_lines = []
    for t in BENCH_TENORS:
        code = bench.get(t)
        if not code:
            continue
        hit = next((r for r in rows if r["code"] == code), None)
        if hit:
            chg = (hit["y"] - hit["prev_y"]) * 100 if hit["prev_y"] is not None else None
            tenor_lines.append((t, code, hit["y"], chg, hit["face"], "當日成交"))
        else:
            pr = last_seen.get(code)
            tenor_lines.append((t, code, pr[1] if pr else None, None, None,
                                f"最近成交 {pr[0]}" if pr else "期間無成交"))

    md = [f"# 台債當日行情 {day}", ""]
    md.append(f"資料來源：櫃買中心買賣斷日檔（營業處所 BDdcs001＋等殖成交系統 BDdys01a），"
              f"面額單位億元、殖利率為成交面額加權（%）。")
    md.append("")
    md.append("## 市場總覽")
    md.append("")
    md.append(f"- 成交面額 **{face:,.1f} 億元**" + (f"（前一交易日 {prev} {pf:,.1f} 億）" if pf else ""))
    md.append(f"- 成交券數 {len(today)} 檔（指標券 {sum(1 for r in rows if r['kind']=='指標券')} 檔、"
              f"非指標券 {sum(1 for r in rows if r['kind']=='非指標券')} 檔）")
    md.append(f"- 成交面額加權平均殖利率 **{wy:.3f}%**"
              + (f"，較前一交易日 {(wy-pwy)*100:+.1f} bp（券別組合不同，僅供參考）" if pwy else ""))
    md.append(f"- 營業處所成交筆數 {otc}（等殖成交系統日檔未揭露筆數）")
    md.append("")
    md.append("## 指標券行情")
    md.append("")
    md.append("| 天期 | 券號 | 發行日 | 殖利率 | 較上次成交 | 當日成交面額 | 備註 |")
    md.append("|---|---|---|---|---|---|---|")
    for t, code, y, chg, f_, note in tenor_lines:
        e = meta.get(code, {})
        iss = e.get("issue", "—")
        if e.get("issue") and e["issue"] > day:            # 還沒到發行日才需要標，已發行就直接寫日期
            iss += "（預計發行）" if e.get("src") == "推估" else "（尚未發行）"
        md.append(f"| {t}Y | {code} | {iss} | {'—' if y is None else f'{y:.3f}%'} | "
                  f"{'—' if chg is None else f'{chg:+.1f} bp'} | "
                  f"{'—' if f_ is None else f'{f_:,.1f} 億'} | {note} |")
    md.append("")
    md.append("## 當日成交明細")
    md.append("")
    md.append("| 券號 | 名稱 | 券別 | 剩餘年限 | 成交面額(億) | 殖利率 | 較上次成交 | 對曲線利差 | 市場 |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        chg = f"{(r['y']-r['prev_y'])*100:+.1f} bp" if r["prev_y"] is not None else "期間首次"
        sp = f"{r['spread']:+.1f} bp" if r["spread"] is not None else "—"
        mk = "處所＋等殖" if len(r["mk"]) == 2 else next(iter(r["mk"]))
        md.append(f"| {r['code']} | {r['name']} | {r['kind']}"
                  + (f"（{r['tenor']}Y）" if r["tenor"] else "") + f" | {r['rem']:.2f} | "
                  f"{r['face']:,.1f} | {r['y']:.3f}% | {chg} | {sp} | {mk} |")
    md.append("")
    md.append(f"註：對曲線利差＝該筆成交殖利率減去參考曲線（回溯 {WIN} 個交易日、"
              f"各券最近一次成交依剩餘年限線性內插，計算時排除該券自身）；正值代表成交殖利率高於曲線。")

    csv = ["交易日,券號,名稱,券別,天期,剩餘年限,成交面額(億),殖利率(%),較上次成交(bp),對曲線利差(bp),市場,處所筆數"]
    for r in rows:
        chg = f"{(r['y']-r['prev_y'])*100:.2f}" if r["prev_y"] is not None else ""
        sp = f"{r['spread']:.2f}" if r["spread"] is not None else ""
        mk = "處所＋等殖" if len(r["mk"]) == 2 else next(iter(r["mk"]))
        csv.append(f"{day},{r['code']},{r['name']},{r['kind']},{r['tenor'] or ''},{r['rem']},"
                   f"{r['face']},{r['y']},{chg},{sp},{mk},{r['otc']}")
    return "\n".join(md), "\n".join(csv)


def main(argv):
    rows, dates, by_date, issues = load()
    meta = bond_meta(rows, issues)
    if not dates:
        sys.exit("資料檔是空的，請先跑 update.py")
    days = [dates[-1]]
    if argv and argv[0] == "--days":
        n = int(argv[1]) if len(argv) > 1 else 5
        days = dates[-n:]
    elif argv:
        if argv[0] not in dates:
            sys.exit(f"{argv[0]} 不在資料範圍或當天沒有公債成交；最近的交易日是 {dates[-1]}")
        days = [argv[0]]

    OUT.mkdir(exist_ok=True)
    for day in days:
        md, csv = brief(day, dates, by_date, meta)
        tag = day.replace("-", "")
        (OUT / f"台債當日行情_{tag}.md").write_text(md + "\n", encoding="utf-8")
        (OUT / f"台債當日行情_{tag}.csv").write_text("﻿" + csv + "\n", encoding="utf-8")
        if len(days) == 1:
            print(md)
        print(f"\n→ 已輸出：{OUT / f'台債當日行情_{tag}.md'}", file=sys.stderr)
        print(f"→ 已輸出：{OUT / f'台債當日行情_{tag}.csv'}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
