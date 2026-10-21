# 趨勢確認系統（GitHub 版）：資料層。介面跟私人 repo 的研究版 data.py 一樣，indicators.py／backtest.py／trend_daily.py 直接共用
# 歷史資料存在 data/trend/*.csv（只有市場數據）；update() 每天只補最近 45 天（順便吃掉事後修正），Put/Call 只補最近兩個月
# Yahoo 每次整段重抓（一個代號一次請求）
# 需要環境變數：FINMIND_TOKEN
import csv
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DDIR = os.path.join(ROOT, "data", "trend")
os.makedirs(DDIR, exist_ok=True)
TODAY = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
H = {"User-Agent": "Mozilla/5.0"}
FM = "https://api.finmindtrade.com/api/v4/data"
BACK_DAYS = 45

# 每個 CSV 的欄位（第一欄都是 date）
FILES = {
    "taiex": ["c", "h", "l"],
    "foreign_spot": ["net", "gross"],          # 外資現貨淨買超、買賣總額（億）
    "margin": ["lots", "money"],               # 融資餘額（張、元）
    "futures": ["foreign", "inst", "oi"],      # 外資期貨淨部位（折大台）、三大法人小台＋微台/5 淨部位、小台＋微台/5 總未平倉
    "pcr": ["vol", "oi"],                      # 期交所 Put/Call 成交量比、未平倉比（%）
}


# ---------- CSV ----------
def load(name):
    p = os.path.join(DDIR, name + ".csv")
    out = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[r["date"]] = {k: (float(r[k]) if r[k] not in ("", None) else None) for k in FILES[name]}
    return out


def _num(v):
    """CSV 裡的數字：空值留白，小數最多 6 位、去掉多餘的 0（檔案小、每天的差異也小）"""
    if v is None:
        return ""
    s = repr(round(float(v), 6))
    return s[:-2] if s.endswith(".0") else s


def save(name, rows):
    p = os.path.join(DDIR, name + ".csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date"] + FILES[name])
        for d in sorted(rows):
            w.writerow([d] + [_num(rows[d].get(k)) for k in FILES[name]])


# ---------- 抓資料 ----------
def fm(dataset, data_id, start, end=None):
    tok = os.environ.get("FINMIND_TOKEN", "")
    if not tok:
        raise SystemExit("讀不到 FINMIND_TOKEN")
    end = end or TODAY
    rows = []
    for y in range(int(start[:4]), int(end[:4]) + 1):
        s, e = max(start, f"{y}-01-01"), min(end, f"{y}-12-31")
        r = requests.get(FM, params={"dataset": dataset, "data_id": data_id, "start_date": s, "end_date": e},
                         headers={"Authorization": f"Bearer {tok}"}, timeout=180).json()
        if r.get("status") not in (200, None):
            raise SystemExit(f"FinMind {dataset} {y}: {r.get('msg')}")
        rows += r.get("data", [])
    return rows


def fetch_finmind(start):
    """回傳 {name: {date: {...}}}，涵蓋 start～今天"""
    out = {k: {} for k in ("taiex", "foreign_spot", "margin", "futures")}
    for r in fm("TaiwanStockPrice", "TAIEX", start):
        if r["close"]:
            out["taiex"][r["date"]] = {"c": r["close"], "h": r["max"] or r["close"], "l": r["min"] or r["close"]}
    for r in fm("TaiwanStockTotalInstitutionalInvestors", "", start):
        if r["name"] == "Foreign_Investor":  # 外資自營商 2017 才分出來，不加，前後才一致
            out["foreign_spot"][r["date"]] = {"net": (r["buy"] - r["sell"]) / 1e8, "gross": (r["buy"] + r["sell"]) / 1e8}
    for r in fm("TaiwanStockTotalMarginPurchaseShortSale", "", start):
        m = out["margin"].setdefault(r["date"], {"lots": None, "money": None})
        if r["name"] == "MarginPurchase":
            m["lots"] = r["TodayBalance"]
        elif r["name"] == "MarginPurchaseMoney":
            m["money"] = r["TodayBalance"]
    fst = max(start, "2018-01-01")
    foreign, inst, oi = defaultdict(float), defaultdict(float), defaultdict(float)
    seen = set()
    for cid, wf, wr in (("TX", 1.0, 0.0), ("MTX", 0.25, 1.0), ("TMF", 0.05, 0.2)):
        for r in fm("TaiwanFuturesInstitutionalInvestors", cid, fst):
            n = r["long_open_interest_balance_volume"] - r["short_open_interest_balance_volume"]
            seen.add(r["date"])
            if r["institutional_investors"] == "外資":
                foreign[r["date"]] += wf * n
            if wr:
                inst[r["date"]] += wr * n
    for cid, w in (("MTX", 1.0), ("TMF", 0.2)):
        for r in fm("TaiwanFuturesDaily", cid, fst):
            if r["trading_session"] == "position" and "/" not in r["contract_date"]:
                oi[r["date"]] += w * (r["open_interest"] or 0)
    for d in seen:
        out["futures"][d] = {"foreign": foreign[d], "inst": inst[d], "oi": oi.get(d) or None}
    return out


PCR_URL = "https://www.taifex.com.tw/cht/3/pcRatioDown"


def _pcr_rows(text):
    out = {}
    for line in text.splitlines():
        c = line.split(",")
        if len(c) >= 7 and re.match(r"\d{4}/\d{2}/\d{2}", c[0]):
            out[c[0].replace("/", "-")] = {"vol": float(c[3]), "oi": float(c[6])}
    return out


def fetch_pcr(start_month):
    """start_month＝(年, 月)，抓到這個月；期交所一次最多查一個月。
    兩種抓法輪流試：先開查詢頁拿 cookie 再下載（本機可以），以及 fetch_futures.py 那種直接 POST（GitHub 上一直正常）；
    每個月印出抓到幾筆，查問題用"""
    s = requests.Session()
    hd = dict(H, Referer="https://www.taifex.com.tw/cht/3/pcRatio")
    try:
        s.get("https://www.taifex.com.tw/cht/3/pcRatio", headers=hd, timeout=60)
    except requests.RequestException as e:
        print("PCR 查詢頁打不開", e)
    out = {}
    y, m = start_month
    end = datetime.strptime(TODAY, "%Y-%m-%d")
    while (y, m) <= (end.year, end.month):
        a = f"{y}/{m:02d}/01"
        b = (datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).strftime("%Y/%m/%d")
        form = {"queryStartDate": a, "queryEndDate": b, "down_type": ""}
        rows, note = {}, ""
        for i, post in enumerate((lambda: s.post(PCR_URL, data=form, headers=hd, timeout=60),
                                  lambda: requests.post(PCR_URL, data=form, headers=H, timeout=60)) * 2):
            try:
                r = post()
                rows = _pcr_rows(r.content.decode("big5", "ignore"))
                note = f"第 {i + 1} 次 HTTP {r.status_code} {len(r.content)} bytes"
                if rows:
                    break
                note += "，開頭：" + r.content[:120].decode("big5", "ignore").replace("\n", " ")
            except requests.RequestException as e:
                note = f"第 {i + 1} 次 {type(e).__name__}"
            time.sleep(3)
        print(f"PCR {a[:7]}：{len(rows)} 筆，最後 {max(rows) if rows else '—'}（{note}）")
        out.update(rows)
        time.sleep(0.4)
        y, m = y + (m == 12), m % 12 + 1
    return out


def update():
    """補最近的資料寫回 CSV；沒有 CSV 就從頭抓。回傳每個檔案最新的日期"""
    cur = {k: load(k) for k in FILES}
    last = [max(v) for k, v in cur.items() if k != "pcr" and v]
    start = (datetime.strptime(min(last), "%Y-%m-%d") - timedelta(days=BACK_DAYS)).strftime("%Y-%m-%d") if len(last) == 4 else "2004-01-01"
    new = fetch_finmind(start)
    for k, rows in new.items():
        cur[k].update(rows)
        save(k, cur[k])
    if cur["pcr"]:
        lp = datetime.strptime(max(cur["pcr"]), "%Y-%m-%d") - timedelta(days=31)
        sm = (lp.year, lp.month)
    else:
        sm = (2005, 1)
    cur["pcr"].update(fetch_pcr(sm))
    save("pcr", cur["pcr"])
    return {k: max(v) if v else None for k, v in cur.items()}


# ---------- 給 indicators.py 用的介面（跟研究版一樣）----------
_Y = {}


def yahoo(sym):
    """回傳 {date: {"c","h","l"}}，date 是該市場當地日期"""
    if sym not in _Y:
        d = None
        for i in range(3):
            try:
                d = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={int(time.time())}&interval=1d",
                                 headers=H, timeout=90).json()["chart"]["result"][0]
                break
            except Exception:
                time.sleep(5 * (i + 1))
        if d is None:
            raise SystemExit(f"Yahoo {sym} 抓不到")
        off = d["meta"]["gmtoffset"]
        q = d["indicators"]["quote"][0]
        out = {}
        for t, c, h, l in zip(d["timestamp"], q["close"], q["high"], q["low"]):
            if c:
                out[datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d")] = {"c": c, "h": h or c, "l": l or c}
        _Y[sym] = out
    return _Y[sym]


def taiex():
    return {d: v for d, v in load("taiex").items() if v["c"]}


def foreign_spot():
    rows = load("foreign_spot")
    return ({d: v["net"] for d, v in rows.items() if v["net"] is not None},
            {d: v["gross"] for d, v in rows.items() if v["gross"] is not None})


def margin_balance():
    rows = load("margin")
    return ({d: v["lots"] for d, v in rows.items() if v["lots"] is not None},
            {d: v["money"] for d, v in rows.items() if v["money"] is not None})


def futures_inst():
    rows = load("futures")
    foreign = {d: v["foreign"] for d, v in rows.items() if v["foreign"] is not None}
    retail = {d: -v["inst"] / v["oi"] * 100 for d, v in rows.items() if v["inst"] is not None and v["oi"]}
    return foreign, retail


def put_call():
    return load("pcr")


US = {"sox": "^SOX", "tsm": "TSM", "hyg": "HYG", "lqd": "LQD", "tnx": "^TNX", "vix": "^VIX", "oil": "CL=F"}
TW = {"tw2330": "2330.TW", "usdtwd": "TWD=X", "twii": "^TWII"}

if __name__ == "__main__":
    print(update())
