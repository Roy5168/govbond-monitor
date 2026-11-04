#!/usr/bin/env python3
"""從櫃買中心抓每日買賣斷日檔，累積到本機資料檔，並重新產生 HTML。

資料頁面（人工查閱用）：
  等殖成交系統 https://www.tpex.org.tw/zh-tw/bond/info/statistics-gb/day/ebts.html
  營業處所     https://www.tpex.org.tw/zh-tw/bond/info/statistics-gb/day/otc.html

用法：
    python3 update.py                     # 從資料檔最後一天的隔天補到今天
    python3 update.py 2026-08-01 2026-08-21   # 指定期間（含頭尾）
    python3 update.py --days 10           # 只補最近 10 個日曆日
    python3 update.py --rebuild           # 不連網，只用現有資料重產 HTML
    python3 update.py --no-issues         # 跳過官方發行資料更新

每次更新都會順便抓一次中央銀行開放資料的「中央公債標售概況表」，讓新標售的券
一發行就有官方發行日與年期（否則會退回推估，指標券換手日可能不準）。

三種「沒有新資料」要分清楚：
  週末／休市     → 櫃買沒有日檔，會標「沒有日檔」
  尚未發布       → 同上，過一會兒再跑即可
  有日檔但 0 筆  → 當天確實有交易日檔，只是沒有中央政府公債買賣斷成交
                   （日檔涵蓋公司債與外幣債，公債零成交在冷門天期是常態）
"""
from __future__ import annotations

import sys
import ssl
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import bonddata as bd
import parse_issues


# 櫃買 www.tpex.org.tw 自 2026-09-07 換發憑證後，伺服器只送出網站憑證、漏送中間憑證
# （TWCA SSL Certification Authority），瀏覽器會自行補抓所以網頁正常，但 Python／curl 會出現
# CERTIFICATE_VERIFY_FAILED（2026-09-14 使用者實際遇到，整批下載失敗）。
# 這裡把該中間憑證（certs/TWCA_SSL_CA_2023.pem）加入驗證用的憑證串，仍完整驗證到
# 系統信任的 TWCA CYBER Root CA，不是關閉驗證。
def _ssl_context():
    ctx = ssl.create_default_context()
    pem = Path(__file__).resolve().parent / "certs" / "TWCA_SSL_CA_2023.pem"
    if pem.exists():
        ctx.load_verify_locations(cafile=str(pem))
    return ctx


SSL_CTX = _ssl_context()

BASE = "https://www.tpex.org.tw/storage/bond_zone/tradeinfo/govbond"
FILE_CODE = {bd.MARKETS[0]: "BDdcs001", bd.MARKETS[1]: "BDdys01a"}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def url_for(d: date, market: str) -> str:
    return (f"{BASE}/{d:%Y}/{d:%Y%m}/{FILE_CODE[market]}.{d:%Y%m%d}-C.xls")


BIFF = b"\xd0\xcf\x11\xe0"        # 舊版 .xls 的檔頭；休市日會回傳 HTML 錯誤頁


def fetch(url: str, timeout=30, tries=3):
    """回傳日檔內容；沒有檔案回 None；連線／伺服器暫時性錯誤重試後仍失敗則丟出例外。

    暫時性錯誤（5xx、逾時、連線中斷）不能當成「休市」，否則會在資料裡留下
    看不出來的缺口——2025-12-25 就曾經回 520。
    """
    last = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": "https://www.tpex.org.tw/"})
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                if resp.status != 200:
                    return None
                blob = resp.read()
            return blob if blob[:4] == BIFF else None      # 不是 Excel＝當天沒有日檔
        except urllib.error.HTTPError as e:
            if e.code == 404:                              # 確定沒有這個檔，不必重試
                return None
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        if attempt < tries:
            time.sleep(1.5 * attempt)
    raise last


def fetch_day(d: date):
    """回傳（該日公債資料列, 有抓到日檔的市場說明）。

    注意：抓得到日檔不代表有公債成交——日檔涵蓋公司債、外幣債等，
    當天若只有公司債成交，公債列數就是 0，這是市場實況不是錯誤。
    """
    rows, got, failed = [], [], []
    for market in bd.MARKETS:
        try:
            blob = fetch(url_for(d, market))
        except Exception as e:                             # noqa: BLE001 - 重試後仍失敗
            print(f"  ! {d} {market} 連線失敗（已重試）：{e}")
            failed.append(market)
            continue
        if not blob:
            continue
        try:
            _, rs = bd.parse_daily_xls(blob, market, expect_date=d.isoformat())
        except Exception as e:                       # noqa: BLE001 - 單日壞檔不該中斷整批
            print(f"  ! {d} {market} 日檔解析失敗：{e}")
            continue
        rows += rs
        got.append(f"{market} {len(rs)} 筆")
    return rows, got, failed


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    store = bd.read_store()

    if "--no-issues" not in flags and "--rebuild" not in flags:
        parse_issues.refresh()          # 順手更新官方發行日，新券才不會退回推估

    if "--rebuild" in flags:
        if not store:
            sys.exit("本機還沒有資料檔，請先執行 build.py 以彙整檔建立底稿，或直接執行 update.py 連網抓取。")
        payload = bd.build_payload(store, f"本機資料檔（{bd.STORE.name}）")
        out = bd.render(payload)
        print(f"已重新產生：{out}\n{bd.summary(payload)}")
        return

    today = date.today()
    if len(args) >= 2:
        start = date.fromisoformat(args[0])
        end = date.fromisoformat(args[1])
    elif "--days" in flags or any(a.startswith("--days=") for a in flags):
        n = 10
        for a in flags:
            if a.startswith("--days="):
                n = int(a.split("=")[1])
        if "--days" in flags:
            i = argv.index("--days")
            if i + 1 < len(argv) and argv[i + 1].isdigit():
                n = int(argv[i + 1])
        start, end = today - timedelta(days=n - 1), today
    elif store:
        last = max(r["d"] for r in store)
        start = date.fromisoformat(last) + timedelta(days=1)
        end = today
    else:
        sys.exit("本機還沒有資料檔。請先跑 build.py 以彙整檔建立底稿，或指定期間：update.py 2026-01-01 2026-08-21")

    if start > end:
        print(f"資料已是最新（最後一天 {max(r['d'] for r in store)}），不需更新。")
        payload = bd.build_payload(store, f"本機資料檔（{bd.STORE.name}）")
        print(bd.summary(payload))
        return

    print(f"抓取 {start} ～ {end} 的櫃買買賣斷日檔…")
    new_rows, days_with_data, days_no_gov, days_no_file, days_weekend = [], 0, 0, 0, 0
    err_days = []
    d = start
    while d <= end:
        if d.weekday() >= 5:                       # 週末直接跳過，少打兩次網路
            days_weekend += 1
        else:
            rows, got, failed = fetch_day(d)
            if failed:
                err_days.append(d.isoformat())
            if rows:
                days_with_data += 1
                new_rows += rows
                print(f"  {d} ✓ {'、'.join(got)}")
            elif got:                              # 有日檔，但當天沒有公債買賣斷成交
                days_no_gov += 1
                print(f"  {d} － 有日檔，但當天沒有中央政府公債買賣斷成交")
            elif failed:                           # 抓取失敗，不是休市
                print(f"  {d} ! 抓取失敗，這天沒有納入")
            else:                                  # 連日檔都沒有：休市或尚未發布
                days_no_file += 1
                print(f"  {d} ✗ 沒有日檔（休市，或櫃買尚未發布）")
            time.sleep(0.4)                        # 對來源客氣一點
        d += timedelta(days=1)

    if err_days:
        print(f"\n⚠️ 以下日期抓取失敗（重試後仍失敗），資料可能有缺口，請稍後重跑：\n   "
              + "、".join(err_days))
        print(f"   重跑指令：python3 update.py {err_days[0]} {err_days[-1]}")
    if not new_rows:
        bits = []
        if days_weekend:
            bits.append(f"週末 {days_weekend} 天")
        if days_no_gov:
            bits.append(f"有日檔但無公債成交 {days_no_gov} 天")
        if days_no_file:
            bits.append(f"沒有日檔（休市或尚未發布）{days_no_file} 天")
        print("沒有新增資料：" + "、".join(bits) + "。")
        if store:
            print(f"資料檔維持在 {max(r['d'] for r in store)}，HTML 不需重新產生。")
        return

    merged, added, updated = bd.merge(store, new_rows)
    n = bd.write_store(merged)
    payload = bd.build_payload(merged, f"櫃買日檔更新至 {max(r['d'] for r in merged)}")
    out = bd.render(payload)
    print(f"\n新增 {added} 筆、更新 {updated} 筆，資料檔共 {n} 筆（{bd.STORE}）")
    print(f"有公債成交 {days_with_data} 天、有日檔但無公債成交 {days_no_gov} 天、"
          f"沒有日檔 {days_no_file} 天、週末 {days_weekend} 天")
    print(f"已重新產生：{out}\n{bd.summary(payload)}")


if __name__ == "__main__":
    main(sys.argv[1:])
