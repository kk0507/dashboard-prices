# python backfill_local.py      （在本機跑，一次性或新股票進入股票池後補歷史；不在 GitHub 上跑）
# 需要先跑過 update_store.py（產生 data/universe.json）。上市股票用證交所官方「指定日期」資料補；
# 上櫃股票櫃買沒有歷史查詢，改用 FinMind（需要環境變數 FINMIND_TOKEN，本機已有，不會上傳）。
import datetime as dt, os, time
import requests
from store_lib import *  # noqa: F401,F403

PRICE_DAYS = 115   # 往前找幾個日曆天（約 80 個交易日，夠算 60 日均線）
FLOW_DAYS = 22     # 法人／融資往前補的日曆天（約 15 個交易日）


def weekdays_back(k):
    d = dt.datetime.now(TW).date()
    out = []
    for i in range(k):
        x = d - dt.timedelta(days=i)
        if x.weekday() < 5:
            out.append(x.strftime("%Y-%m-%d"))
    return out


def fm(ds, code, start):
    tok = os.environ.get("FINMIND_TOKEN")
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": ds, "data_id": code, "start_date": start},
                     headers={"Authorization": f"Bearer {tok}"} if tok else {}, timeout=60).json()
    return r.get("data", [])


def main():
    uni = set(load_universe().get("codes", []))
    if not uni:
        raise SystemExit("data/universe.json 是空的，先跑 update_store.py")
    # 判斷哪些是上市
    today_twse = set()
    for d in weekdays_back(6):
        rows = twse_prices(d)
        if len(rows) >= 800:
            today_twse = {r["code"] for r in rows}
            break
    twse_codes = uni & today_twse
    tpex_codes = uni - today_twse
    print("上市", len(twse_codes), "上櫃", len(tpex_codes))

    # --- 上市：價量（逐日）---
    trading_days = []
    tot = 0
    for d in weekdays_back(PRICE_DAYS):
        rows = twse_prices_hist(d)
        time.sleep(1.5)
        if len(rows) < 800:
            continue
        trading_days.append(d)
        tot += append_rows("prices", [r for r in rows if r["code"] in twse_codes])
    print("上市價量新增", tot, "交易日", len(trading_days))

    # --- 上市：法人、融資（最近 FLOW_DAYS 天內的交易日）---
    recent = [d for d in trading_days if d >= (dt.datetime.now(TW).date() - dt.timedelta(days=FLOW_DAYS)).strftime("%Y-%m-%d")]
    ti = tm = 0
    for d in recent:
        m = twse_inst(d); time.sleep(1.5)
        ti += append_rows("inst", [{"date": d, "code": c, **v} for c, v in m.items() if c in twse_codes])
        g = twse_margin(d); time.sleep(1.5)
        tm += append_rows("margin", [{"date": d, "code": c, **v} for c, v in g.items() if c in twse_codes])
    print("上市法人新增", ti, "融資新增", tm)

    if "twse-only" in __import__("sys").argv:
        return

    # --- 上櫃：FinMind ---
    start_p = (dt.datetime.now(TW).date() - dt.timedelta(days=PRICE_DAYS)).strftime("%Y-%m-%d")
    start_f = (dt.datetime.now(TW).date() - dt.timedelta(days=FLOW_DAYS)).strftime("%Y-%m-%d")
    tp = tf = tg = 0
    for c in sorted(tpex_codes):
        p = fm("TaiwanStockPrice", c, start_p)
        tp += append_rows("prices", [{"date": x["date"], "code": c, "open": x["open"], "high": x["max"], "low": x["min"], "close": x["close"],
                                      "volume": x["Trading_Volume"], "value": x["Trading_money"]} for x in p if x["close"] and x["close"] > 0])
        inst = fm("TaiwanStockInstitutionalInvestorsBuySell", c, start_f)
        by = {}
        for x in inst:
            k = "foreign" if x["name"] in ("Foreign_Investor", "Foreign_Dealer_Self") else "trust" if x["name"] == "Investment_Trust" else "dealer"
            by.setdefault(x["date"], {"foreign": 0, "trust": 0, "dealer": 0})
            by[x["date"]][k] += x["buy"] - x["sell"]
        tf += append_rows("inst", [{"date": d, "code": c, **v} for d, v in by.items()])
        mgn = fm("TaiwanStockMarginPurchaseShortSale", c, start_f)
        tg += append_rows("margin", [{"date": x["date"], "code": c, "margin_bal": x["MarginPurchaseTodayBalance"], "short_bal": x["ShortSaleTodayBalance"]} for x in mgn])
        time.sleep(0.3)
    print("上櫃價量", tp, "法人", tf, "融資", tg)


if __name__ == "__main__":
    main()
