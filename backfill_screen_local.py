# python backfill_screen_local.py   （在本機跑，不在 GitHub 上跑；新股票進入股票池或想重算時再跑一次）
# 選股頁需要、但官方開放資料沒有歷史的三樣東西，從本機 FinMind 快取算出來：
#   data/revenue.csv   最近 8 個月的月營收（之後由 build_screen.py 每天用官方資料接著累積）
#   data/per_hist.json 每檔每週一筆的歷史本益比（由小到大），用來算「本益比在自己歷史的位置」
#   data/downday.json  大盤單日跌 2% 以上那些天，各股的平均漲跌
# 快取位置：環境變數 SCREEN_CACHE，預設 ../backtest_score/cache（先用 fetch_history.py 抓好；FinMind 金鑰只在本機用，不會上傳）。
import datetime as dt, json, os, statistics
from store_lib import *  # noqa: F401,F403

C = os.environ.get("SCREEN_CACHE", os.path.join("..", "backtest_score", "cache"))
REV_MONTHS, DOWN_PCT, MIN_DOWN_DAYS = 8, -2.0, 10


def cache(ds, code):
    p = os.path.join(C, ds, f"{code}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def main():
    uni = load_universe().get("codes", [])
    # --- 月營收（FinMind 單位是元，官方是千元）---
    rows = []
    for c in uni:
        m = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in cache("TaiwanStockMonthRevenue", c)}
        for (y, mo) in sorted(m)[-REV_MONTHS:]:
            if m[(y, mo)] > 0 and m.get((y - 1, mo), 0) > 0:
                rows.append({"date": f"{y:04d}-{mo:02d}", "code": c, "revenue": round(m[(y, mo)] / 1000), "prev_year": round(m[(y - 1, mo)] / 1000)})
    added = append_rows("revenue", rows)
    # --- 本益比歷史（每週最後一個交易日）---
    hist = {}
    for c in uni:
        wk = {}
        for r in cache("TaiwanStockPER", c):
            if r["PER"] > 0:
                wk[tuple(dt.date.fromisoformat(r["date"]).isocalendar()[:2])] = r["PER"]
        if wk:
            hist[c] = sorted(wk.values())
    json.dump(hist, open(os.path.join(DATA, "per_hist.json"), "w", encoding="utf-8"), separators=(",", ":"))
    # --- 大盤大跌日 ---
    tx = [r for r in cache("TaiwanStockPrice", "TAIEX") if r["close"] > 0]
    bad = {tx[i]["date"] for i in range(1, len(tx)) if (tx[i]["close"] / tx[i - 1]["close"] - 1) * 100 <= DOWN_PCT}
    items = {}
    for c in uni:
        p = [r for r in cache("TaiwanStockPrice", c) if r["close"] > 0]
        rs = [(p[i]["close"] / p[i - 1]["close"] - 1) * 100 for i in range(1, len(p)) if p[i]["date"] in bad]
        rs = [x for x in rs if abs(x) <= 10.5]  # 分割、減資造成的價格斷層不算
        if len(rs) >= MIN_DOWN_DAYS:
            items[c] = {"avg": round(statistics.mean(rs), 2), "n": len(rs)}
    mkt = [(tx[i]["close"] / tx[i - 1]["close"] - 1) * 100 for i in range(1, len(tx)) if tx[i]["date"] in bad]
    json.dump({"asOf": tx[-1]["date"] if tx else "", "since": tx[0]["date"] if tx else "", "threshold": DOWN_PCT, "days": len(bad),
               "market": round(statistics.mean(mkt), 2) if mkt else None, "items": items},
              open(os.path.join(DATA, "downday.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"OK 月營收新增 {added} 列；本益比歷史 {len(hist)} 檔；大跌日 {len(bad)} 天、{len(items)} 檔（股票池 {len(uni)} 檔）")


if __name__ == "__main__":
    main()
