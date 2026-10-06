# python fetch_prices.py [--final]
# 抓全市場收盤價(證交所+櫃買)與大盤，輸出 prices.json（只含公開行情，不含任何持股資訊）。
# 有問題時寫 alert.txt（workflow 會轉送 Discord）；--final 表示今天最後一輪，會額外檢查「今天該有資料卻沒有」。
import csv, io, json, re, sys, time, datetime as dt
import requests

H = {"User-Agent": "Mozilla/5.0"}
TW = dt.timezone(dt.timedelta(hours=8))
TWSE_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
FMTQIK = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
# 證交所官網（指定日期）比開放資料平台早更新好幾個小時，優先用；開放資料當備援
TWSE_RWD = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=csv&date={d}"
FIVE_MIN = "https://www.twse.com.tw/exchangeReport/MI_5MINS_INDEX?response=json&date={d}"
# 櫃買官網（指定日期）：檔案比開放資料小、更新也早；開放資料那支 4.7 MB，收盤後常傳到一半斷線，當備援
TPEX_SITE = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d}&id=&response=json"


def get_json(url, tries=5):
    err = None
    for i in range(tries):
        if i:
            time.sleep(4 * i)  # 對方伺服器偶爾中斷連線，隔幾秒再試
        try:
            r = requests.get(url, headers=H, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"{url} 抓取失敗：{err}")


def twse_rwd_rows(day):
    """官網 CSV：回傳與 openapi 相同欄位名稱的 list；當天沒資料或格式不對回傳 []。"""
    try:
        r = requests.get(TWSE_RWD.format(d=day.replace("-", "")), headers=H, timeout=30)
        text = r.text.lstrip("﻿")
        if r.status_code != 200 or text.startswith("{") or text.startswith("<"):
            return []
        out = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) >= 9 and re.fullmatch(r"\d{7}", row[0].strip()):
                out.append({"Date": row[0].strip(), "Code": row[1].strip(), "Name": row[2].strip(),
                            "ClosingPrice": row[8].strip(), "Change": row[9].strip() if len(row) > 9 else ""})
        return out
    except Exception:  # noqa: BLE001
        return []


def tpex_site_rows(day):
    """櫃買官網：回傳與 openapi 相同欄位名稱的 list；當天沒資料或格式不對回傳 []。"""
    for i in range(3):
        if i:
            time.sleep(4 * i)
        try:
            r = requests.get(TPEX_SITE.format(d=day.replace("-", "/")), headers=H, timeout=30)
            j = r.json()
            if r.status_code != 200 or j.get("stat") != "ok" or j.get("date") != day.replace("-", ""):
                return []
            t = j["tables"][0]
            if t["fields"][:4] != ["代號", "名稱", "收盤", "漲跌"]:
                return []
            rocd = f"{int(day[:4]) - 1911}{day[5:7]}{day[8:10]}"
            return [{"Date": rocd, "SecuritiesCompanyCode": x[0].strip(), "CompanyName": x[1].strip(),
                     "Close": x[2].strip(), "Change": x[3].strip()} for x in t["data"]]
        except Exception:  # noqa: BLE001
            continue
    return []


def load_old():
    try:
        with open("prices.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


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

    src = {}
    old = load_old()
    failed = []  # 這一輪抓不到的來源：沿用上一版該來源的資料，其餘照寫，不讓整輪失敗
    weekday = dt.datetime.now(TW).weekday() < 5
    today_str = dt.datetime.now(TW).strftime("%Y-%m-%d")

    def twse_quotes():
        rows = twse_rwd_rows(today_str) if weekday else []
        if len(rows) < 800:
            rows = get_json(TWSE_ALL)
        q = {}
        for r in rows:
            c = num(r.get("ClosingPrice"))
            if c and STOCK_CODE.match(r["Code"].strip()):
                q[r["Code"].strip()] = {"c": c, "d": roc(r["Date"]), "chg": num_signed(r.get("Change")), "n": r.get("Name", "").strip()}
        # 資料量太少代表來源壞了，寧可當作沒抓到也不要寫進去
        if len(q) < 800:
            raise RuntimeError(f"證交所資料量異常：{len(q)}")
        return q, {"date": roc(rows[0]["Date"]), "count": len(q)}

    def tpex_quotes():
        rows = tpex_site_rows(today_str) if weekday else []
        if len(rows) < 400:
            rows = get_json(TPEX_ALL)
        q = {}
        for r in rows:
            c = num(r.get("Close"))
            if c and STOCK_CODE.match(r["SecuritiesCompanyCode"].strip()):
                q[r["SecuritiesCompanyCode"].strip()] = {
                    "c": c, "d": roc(r["Date"]), "chg": num_signed(r.get("Change")), "n": r.get("CompanyName", "").strip()}
        if len(q) < 400:
            raise RuntimeError(f"櫃買資料量異常：{len(q)}")
        return q, {"date": roc(rows[0]["Date"]), "count": len(q)}

    fresh = {}
    for key, label, fn in (("twse", "證交所", twse_quotes), ("tpex", "櫃買", tpex_quotes)):
        try:
            q, src[key] = fn()
            fresh.update(q)
        except Exception as e:  # noqa: BLE001
            if not (old.get("sources") or {}).get(key):
                raise
            src[key] = old["sources"][key]
            failed.append(label)
            print(f"WARN {label}這一輪抓不到，沿用上一版（{src[key]}）：{e}")
    # 有來源沒抓到時，以上一版為底、蓋上這一輪抓到的（上市與上櫃代號不重疊）
    quotes = {**old.get("quotes", {}), **fresh} if failed else fresh

    rec = []
    try:
        fm = get_json(FMTQIK)
        rec = [(roc(x["Date"]), float(str(x["TAIEX"]).replace(",", "")), float(str(x["Change"]).replace(",", ""))) for x in fm if num(x.get("TAIEX"))]
        if not rec:
            raise RuntimeError("大盤資料為空")
    except Exception as e:  # noqa: BLE001
        ot = old.get("taiex") or {}
        if not ot.get("recent") or not ot.get("date"):
            raise
        # 用上一版的大盤當底（下面仍會試著用今天的 5 秒指數補上今天）
        yr = int(ot["date"][:4])
        rec = [(f"{yr}-{md}", c, 0.0) for md, c in ot["recent"]]
        rec[-1] = (ot["date"], ot["close"], ot["change"])
        failed.append("大盤")
        print(f"WARN 大盤這一輪抓不到，沿用上一版（{ot['date']}）：{e}")
    if len(failed) == 3:
        raise RuntimeError("證交所、櫃買、大盤全部抓不到")
    d, close, chg = rec[-1]
    if d < today_str and dt.datetime.now(TW).weekday() < 5:
        # FMTQIK 還沒更新今天時，用今天 5 秒指數的最後一筆當收盤（13:30 那筆＝收盤指數）
        try:
            j0 = get_json(FIVE_MIN.format(d=today_str.replace("-", "")))
            d0 = j0.get("data") or []
            if j0.get("stat") == "OK" and d0 and d0[-1][0][:5] >= "13:30":
                c0 = float(str(d0[-1][1]).replace(",", ""))
                rec.append((today_str, c0, round(c0 - close, 2)))
                d, close, chg = rec[-1]
        except Exception:  # noqa: BLE001
            pass
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
                    f"戰情室會在下一輪雲端排程寫入（平日 16:45／17:45／19:45，隔天 07:45 兜底）。")

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
                alerts.append(f"🔴 今天（{today}）是交易日，但各輪都沒抓到最新收盤價：{'、'.join(late)}。請手動跑 /refresh")
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
