# 資料工廠共用函式：抓證交所／櫃買官方資料（價量、三大法人、融資融券），寫成 CSV 累積歷史。
# 全部是公開資料，不含任何持股資訊。
import csv, io, json, os, re, time, datetime as dt
import requests

H = {"User-Agent": "Mozilla/5.0"}
TW = dt.timezone(dt.timedelta(hours=8))
DATA = "data"


def http(url, tries=5, as_text=False):
    err = None
    for i in range(tries):
        if i:
            time.sleep(4 * i)
        try:
            r = requests.get(url, headers=H, timeout=40)
            r.raise_for_status()
            return r.text if as_text else r.json()
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"{url} 失敗：{err}")


def n(x, default=0.0):
    s = str(x).replace(",", "").replace("+", "").strip()
    if s in ("", "-", "--", "---", "X", "N/A"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def roc(d):
    d = str(d).strip()
    return f"{int(d[:-4]) + 1911:04d}-{d[-4:-2]}-{d[-2:]}"


def is_stock(code):
    return bool(re.fullmatch(r"[1-9]\d{3}", code))  # 4 碼一般股票（排除 00 開頭的 ETF 與權證）


# ---------- 價量 ----------
def twse_prices(day):
    """day: YYYY-MM-DD。官網 CSV，非交易日或尚未更新回傳 []。"""
    try:
        text = http("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=csv&date=" + day.replace("-", ""), tries=3, as_text=True).lstrip("﻿")
    except RuntimeError:
        return []
    if text.startswith("{") or text.startswith("<"):
        return []
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 9 and re.fullmatch(r"\d{7}", row[0].strip()):
            close = n(row[8])
            if close > 0:
                out.append({"date": roc(row[0]), "code": row[1].strip(), "open": n(row[5]), "high": n(row[6]), "low": n(row[7]),
                            "close": close, "volume": n(row[3]), "value": n(row[4])})
    return out


def twse_prices_hist(day):
    """證交所「指定日期」日收盤行情（MI_INDEX）。歷史回補用；非交易日回傳 []。"""
    try:
        j = http("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999&response=json&date=" + day.replace("-", ""), tries=3)
    except RuntimeError:
        return []
    if j.get("stat") != "OK":
        return []
    out = []
    for t in j.get("tables", []):
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f and len(t.get("data", [])) > 500:
            i = {k: f.index(k) for k in ("證券代號", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價")}
            for r in t["data"]:
                close = n(r[i["收盤價"]])
                if close > 0:
                    out.append({"date": day, "code": r[i["證券代號"]].strip(), "open": n(r[i["開盤價"]]), "high": n(r[i["最高價"]]),
                                "low": n(r[i["最低價"]]), "close": close, "volume": n(r[i["成交股數"]]), "value": n(r[i["成交金額"]])})
    return out


def tpex_prices_latest():
    rows = http("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
    out = []
    for r in rows:
        close = n(r.get("Close"))
        if close > 0:
            out.append({"date": roc(r["Date"]), "code": r["SecuritiesCompanyCode"].strip(), "open": n(r.get("Open")), "high": n(r.get("High")),
                        "low": n(r.get("Low")), "close": close, "volume": n(r.get("TradingShares")), "value": n(r.get("TransactionAmount"))})
    return out


# ---------- 三大法人（股數，淨買賣超）----------
def twse_inst(day):
    try:
        j = http("https://www.twse.com.tw/rwd/zh/fund/T86?selectType=ALL&response=json&date=" + day.replace("-", ""), tries=3)
    except RuntimeError:
        return {}
    if j.get("stat") != "OK":
        return {}
    f = j["fields"]
    ix = lambda name: f.index(name)  # noqa: E731
    i_f1, i_f2 = ix("外陸資買賣超股數(不含外資自營商)"), ix("外資自營商買賣超股數")
    i_t, i_d = ix("投信買賣超股數"), ix("自營商買賣超股數")
    out = {}
    for r in j["data"]:
        out[r[0].strip()] = {"foreign": n(r[i_f1]) + n(r[i_f2]), "trust": n(r[i_t]), "dealer": n(r[i_d])}
    return out


def tpex_inst_latest():
    rows = http("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading")
    out, day = {}, None
    for r in rows:
        code = (r.get("SecuritiesCompanyCode") or "").strip()
        if not code:
            continue
        day = roc(r["Date"])
        out[code] = {"foreign": n(r.get("ForeignInvestorsInclude MainlandAreaInvestors-Difference")),
                     "trust": n(r.get("SecuritiesInvestmentTrustCompanies-Difference")),
                     "dealer": n(r.get("Dealers-Difference"))}
    return day, out


# ---------- 融資融券（餘額，張）----------
def twse_margin(day):
    try:
        j = http("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?selectType=STOCK&response=json&date=" + day.replace("-", ""), tries=3)
    except RuntimeError:
        return {}
    if j.get("stat") != "OK":
        return {}
    out = {}
    for t in j.get("tables", []):
        if t.get("fields") and "代號" in t["fields"] and len(t.get("data", [])) > 300:
            for r in t["data"]:
                out[r[0].strip()] = {"margin_bal": n(r[6]), "short_bal": n(r[12])}
    return out


def tpex_margin_latest():
    rows = http("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance")
    out, day = {}, None
    for r in rows:
        code = (r.get("SecuritiesCompanyCode") or "").strip()
        if code:
            day = roc(r["Date"])
            out[code] = {"margin_bal": n(r.get("MarginPurchaseBalance")), "short_bal": n(r.get("ShortSaleBalance"))}
    return day, out


# ---------- CSV ----------
COLS = {
    "prices": ["date", "code", "open", "high", "low", "close", "volume", "value"],
    "inst": ["date", "code", "foreign", "trust", "dealer"],
    "margin": ["date", "code", "margin_bal", "short_bal"],
}


def path(kind):
    return os.path.join(DATA, kind + ".csv")


def load_keys(kind):
    p = path(kind)
    if not os.path.exists(p):
        return set()
    with open(p, encoding="utf-8", newline="") as f:
        return {(r["date"], r["code"]) for r in csv.DictReader(f)}


def append_rows(kind, rows):
    os.makedirs(DATA, exist_ok=True)
    p = path(kind)
    new = not os.path.exists(p)
    have = load_keys(kind) if not new else set()
    rows = [r for r in rows if (r["date"], r["code"]) not in have]
    if not rows:
        return 0
    with open(p, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS[kind])
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS[kind]})
    return len(rows)


def read_csv(kind):
    p = path(kind)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_universe():
    try:
        return json.load(open(os.path.join(DATA, "universe.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {"codes": []}


def save_universe(u):
    os.makedirs(DATA, exist_ok=True)
    json.dump(u, open(os.path.join(DATA, "universe.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
