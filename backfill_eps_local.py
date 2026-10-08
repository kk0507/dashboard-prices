# python backfill_eps_local.py   （在本機跑，不在 GitHub 上跑；需要環境變數 FINMIND_TOKEN）
# 官方開放資料只給最新一季的累計 EPS，沒有歷史。這支從 FinMind 把股票池每一檔「去年」每一季的 EPS 抓下來，
# 加總成「年初到該季的累計」，補進 data/eps.csv（已經有的列不會重複寫），給「跟去年同期比」用。
# 今年的季別不回補：一律等 build_screen.py 抓官方累計值（FinMind 是單季相加，股本有變動時會跟官方累計差一點）。
# 只含公開財報數字，不含任何持股資訊。
import os, sys, time, datetime as dt
import requests
from store_lib import append_rows, load_universe

TOKEN = os.environ.get("FINMIND_TOKEN", "")
START = f"{dt.date.today().year - 1}-01-01"


def main():
    if not TOKEN:
        sys.exit("沒有 FINMIND_TOKEN")
    rows, miss = [], []
    codes = load_universe().get("codes", [])
    for i, c in enumerate(codes):
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data", timeout=40, headers={"Authorization": "Bearer " + TOKEN},
                             params={"dataset": "TaiwanStockFinancialStatements", "data_id": c, "start_date": START})
            data = [x for x in r.json().get("data", []) if x.get("type") == "EPS"]
        except Exception as e:  # noqa: BLE001
            miss.append(c); print("WARN", c, e); continue
        cum = {}
        for x in sorted(data, key=lambda x: x["date"]):
            y, q = x["date"][:4], (int(x["date"][5:7]) + 2) // 3
            cum[y] = round(cum.get(y, 0) + x["value"], 2)
            if int(y) < dt.date.today().year:
                rows.append({"date": f"{y}-Q{q}", "code": c, "eps": cum[y]})
        if not data:
            miss.append(c)
        if i % 50 == 49:
            print(i + 1, "/", len(codes))
        time.sleep(0.25)
    print("OK 回補", append_rows("eps", rows), "列；沒有資料的", len(miss), "檔：", " ".join(miss[:40]))


if __name__ == "__main__":
    main()
