# python fetch_prices.py [--final]
# 抓全市場收盤價(證交所+櫃買)與大盤，輸出 prices.json（只含公開行情，不含任何持股資訊）。
# 有問題時寫 alert.txt（workflow 會轉送 Discord）；--final 表示今天最後一輪，會額外檢查「今天該有資料卻沒有」。
import json, re, sys, datetime as dt
import requests

H = {"User-Agent": "Mozilla/5.0"}
TW = dt.timezone(dt.timedelta(hours=8))
TWSE_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
FMTQIK = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
FIVE_MIN = "https://www.twse.com.tw/exchangeReport/MI_5MINS_INDEX?response=json&date={d}"


def get_json(url, tries=3):
    err = None
    for _ in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"{url} 抓取失敗：{err}")


def roc(d):
    d = str(d).strip()
    return f"{int(d[:-4]) + 1911:04d}-{d[-4:-2]}-{d[-2:]}"


STOCK_CODE = re.compile(r"^\d{4,5}[A-Z]?$")  # 股票與ETF，排除權證等


def num(x):
    s = str(x).replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def main():
    final = "--final" in sys.argv
    alerts = []

    quotes, src = {}, {}
    rows = get_json(TWSE_ALL)
    for r in rows:
        c = num(r.get("ClosingPrice"))
        if c and STOCK_CODE.match(r["Code"].strip()):
            chg = num_signed(r.get("Change"))
            quotes[r["Code"].strip()] = {"c": c, "d": roc(r["Date"]), "chg": chg, "n": r.get("Name", "").strip()}
    src["twse"] = {"date": roc(rows[0]["Date"]) if rows else None, "count": len(quotes)}
    n0 = len(quotes)

    rows = get_json(TPEX_ALL)
    for r in rows:
        c = num(r.get("Close"))
        if c and STOCK_CODE.match(r["SecuritiesCompanyCode"].strip()):
            quotes[r["SecuritiesCompanyCode"].strip()] = {
                "c": c, "d": roc(r["Date"]), "chg": num_signed(r.get("Change")), "n": r.get("CompanyName", "").strip()}
    src["tpex"] = {"date": roc(rows[0]["Date"]) if rows else None, "count": len(quotes) - n0}

    # 資料量太少代表來源壞了，寧可失敗也不要寫進去
    if src["twse"]["count"] < 800 or src["tpex"]["count"] < 400:
        raise RuntimeError(f"資料量異常：{src}")

    fm = get_json(FMTQIK)
    rec = [(roc(x["Date"]), float(str(x["TAIEX"]).replace(",", "")), float(str(x["Change"]).replace(",", ""))) for x in fm if num(x.get("TAIEX"))]
    if not rec:
        raise RuntimeError("大盤資料為空")
    d, close, chg = rec[-1]
    taiex = {"date": d, "close": close, "change": chg,
             "changePct": round(chg / (close - chg) * 100, 2),
             "recent": [[x[0][5:], x[1]] for x in rec[-15:]], "intraday": []}
    try:
        j = get_json(FIVE_MIN.format(d=d.replace("-", "")))
        data = j.get("data") or []
        if j.get("stat") == "OK" and data:
            pts = [[row[0][:5], float(str(row[1]).replace(",", ""))] for i, row in enumerate(data) if i % 60 == 0]
            last = [data[-1][0][:5], float(str(data[-1][1]).replace(",", ""))]
            if pts[-1] != last:
                pts.append(last)
            taiex["intraday"] = pts
    except Exception as e:  # noqa: BLE001 走勢圖抓不到不算致命
        alerts.append(f"大盤走勢圖抓取失敗（不影響收盤價）：{e}")

    now = dt.datetime.now(TW)
    out = {"generatedAt": now.isoformat(timespec="seconds"), "sources": src, "taiex": taiex, "quotes": quotes}

    # 三個來源日期到齊＝當天資料完整；同一天只通知一次（跟上一版 prices.json 比較）
    def complete_date(o):
        try:
            a, b, c = o["sources"]["twse"]["date"], o["sources"]["tpex"]["date"], o["taiex"]["date"]
            return a if a == b == c else None
        except (KeyError, TypeError):
            return None

    try:
        with open("prices.json", encoding="utf-8") as f:
            old_done = complete_date(json.load(f))
    except (OSError, ValueError):
        old_done = None
    new_done = complete_date(out)
    if new_done and new_done != old_done:
        sign = "+" if taiex["change"] >= 0 else ""
        with open("notify.txt", "w", encoding="utf-8") as f:
            f.write(f"✅ {new_done} 收盤價已備妥（大盤 {taiex['close']:,.2f}，{sign}{taiex['change']:,.2f} / {sign}{taiex['changePct']}%）。"
                    f"戰情室會在下一輪雲端排程（16:45／17:45／19:45／隔天07:45）寫入。")

    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK twse={src['twse']} tpex={src['tpex']} taiex={d} {close}")

    if final and now.weekday() < 5:
        today = now.strftime("%Y-%m-%d")
        try:
            j = get_json(FIVE_MIN.format(d=today.replace("-", "")))
            is_trading_day = j.get("stat") == "OK" and bool(j.get("data"))
        except Exception:  # noqa: BLE001
            is_trading_day = False
        if is_trading_day:
            late = [k for k, v in (("證交所", src["twse"]["date"]), ("櫃買", src["tpex"]["date"]), ("大盤", d)) if v != today]
            if late:
                alerts.append(f"🔴 今天（{today}）是交易日，但三輪都沒抓到最新收盤價：{'、'.join(late)}。請手動跑 /refresh")
    if alerts:
        with open("alert.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(alerts))


def num_signed(x):
    s = str(x).replace(",", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
