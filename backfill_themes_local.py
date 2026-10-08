# python backfill_themes_local.py   （在本機跑，不在 GitHub 上跑；themes.json 加了新股票、或想把歷史補長時再跑一次）
# 題材階段要用到「6 個月前的近 3 個月營收」，也要用到不在股票池裡的題材股；官方開放資料只有最新一個月，
# 所以從本機 FinMind 快取把題材股最近 12 個月缺的月營收補進 data/revenue.csv（之後由 build_screen.py 每天用官方資料接著累積）。
# 快取位置：環境變數 SCREEN_CACHE，預設 ../backtest_score/cache（FinMind 金鑰只在本機用，不會上傳）。
import json, os
from store_lib import *  # noqa: F401,F403

C = os.environ.get("SCREEN_CACHE", os.path.join("..", "backtest_score", "cache"))
MONTHS = 12


def main():
    themes = json.load(open("themes.json", encoding="utf-8"))
    themes.pop("_note", None)
    rows, miss = [], []
    for c in sorted({c for cs in themes.values() for c in cs}):
        p = os.path.join(C, "TaiwanStockMonthRevenue", f"{c}.json")
        if not os.path.exists(p):
            miss.append(c)
            continue
        m = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in json.load(open(p, encoding="utf-8"))}
        for (y, mo) in sorted(m)[-MONTHS:]:
            if m[(y, mo)] > 0 and m.get((y - 1, mo), 0) > 0:   # FinMind 單位是元，官方是千元
                rows.append({"date": f"{y:04d}-{mo:02d}", "code": c, "revenue": round(m[(y, mo)] / 1000), "prev_year": round(m[(y - 1, mo)] / 1000)})
    print(f"OK 題材股月營收新增 {append_rows('revenue', rows)} 列；沒有快取的 {miss}")


if __name__ == "__main__":
    main()
