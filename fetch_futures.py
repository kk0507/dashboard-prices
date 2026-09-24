# python fetch_futures.py [--final | --night-final]
# 抓期交所官網的台指期(TX)/微台(TMF)近月行情（日盤＋最近一個夜盤）與三大法人台指期未平倉，輸出 futures.json。
# 只含公開行情，不含任何個人部位。不需要 FinMind token。
# 期交所 CSV 把「D 晚上 15:00 ~ D+1 清晨 05:00」的夜盤標成次一交易日的「盤後」列，而且要到那天才查得到；
# 所以剛收盤的夜盤改用行情網(MIS)的最後成交。--final＝日盤最後一輪、--night-final＝夜盤最後一輪（失敗才通知）。
import csv, io, json, re, sys, time, datetime as dt
import requests

H = {"User-Agent": "Mozilla/5.0"}
TW = dt.timezone(dt.timedelta(hours=8))
DAILY = "https://www.taifex.com.tw/cht/3/futDataDown"
CHIPS = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
MIS = "https://mis.taifex.com.tw/futures/api/getQuoteList"
WHO = {"外資及陸資": "foreign", "投信": "trust", "自營商": "dealer"}


class NoData(Exception):
    pass


def post_csv_once(url, data, tries=5):
    err = None
    for i in range(tries):
        if i:
            time.sleep(4 * i)
        try:
            r = requests.post(url, data=data, headers=H, timeout=30)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 網路錯誤才重試
            err = e
            continue
        text = r.content.decode("big5", "replace")
        if not text.lstrip().startswith(("日期", "交易日期")):
            raise NoData()
        return list(csv.reader(io.StringIO(text)))[1:]
    raise RuntimeError(f"{url} 抓取失敗：{err}")


def post_csv(url, data):
    """結束日若還沒有資料（休市、當天還沒公布），期交所回錯誤頁而不是 CSV → 結束日往前退，最多退 10 天。"""
    end = dt.datetime.strptime(data["queryEndDate"], "%Y/%m/%d")
    for _ in range(11):
        try:
            return post_csv_once(url, {**data, "queryEndDate": end.strftime("%Y/%m/%d")})
        except NoData:
            end -= dt.timedelta(days=1)
    raise RuntimeError(f"{url} 近 10 天都查無資料（可能網站改版）")


def num(x):
    s = str(x).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def iso(d):
    return d.strip().replace("/", "-")


def daily_rows(cid, start, end):
    """回傳 {(date, month, session): row}；session 為「一般」(日盤) 或「盤後」(夜盤)。只收月契約(6碼)。"""
    out = {}
    for r in post_csv(DAILY, {"down_type": "1", "commodity_id": cid,
                              "queryStartDate": start, "queryEndDate": end}):
        if len(r) < 18 or not re.fullmatch(r"\d{6}", r[2].strip()):
            continue
        close = num(r[6])
        if close is None:
            continue
        out[(iso(r[0]), r[2].strip(), r[17].strip())] = {
            "open": num(r[3]), "high": num(r[4]), "low": num(r[5]), "close": close,
            "change": num(r[7]), "changePct": num(r[8]), "volume": int(num(r[9]) or 0),
            "settle": num(r[10]), "oi": int(num(r[11]) or 0) if num(r[11]) is not None else None}
    return out


def next_day(ymd):
    return (dt.date.fromisoformat(ymd) + dt.timedelta(days=1)).isoformat()


def mis_night(cid):
    """期交所行情網夜盤(MarketType 1)近月。CDate＝夜盤開始那天；漲跌是對當天日盤結算價。"""
    r = requests.post(MIS, json={"MarketType": "1", "SymbolType": "F", "KindID": "1", "CID": cid, "ExpireMonth": "",
                                 "rowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"},
                      headers=H, timeout=20)
    r.raise_for_status()
    rows = [q for q in r.json()["RtData"]["QuoteList"]
            if q["SymbolID"].endswith(("-F", "-M")) and q["CTotalVolume"] and q["CLastPrice"]]
    if not rows:
        return None
    q = max(rows, key=lambda x: int(x["CTotalVolume"]))
    start = f"{q['CDate'][:4]}-{q['CDate'][4:6]}-{q['CDate'][6:]}"
    f = lambda k: float(q[k]) if q[k] else None  # noqa: E731
    return {"startDate": start, "endDate": next_day(start), "contract": q["SymbolID"],
            "q": {"open": f("COpenPrice"), "high": f("CHighPrice"), "low": f("CLowPrice"), "close": f("CLastPrice"),
                  "change": f("CDiff"), "changePct": f("CDiffRate"), "volume": int(q["CTotalVolume"]),
                  "settle": None, "oi": None}}


def main():
    final = "--final" in sys.argv
    alerts = []
    now = dt.datetime.now(TW)
    start = (now - dt.timedelta(days=21)).strftime("%Y/%m/%d")
    end = now.strftime("%Y/%m/%d")

    tx = daily_rows("TX", start, end)
    tmf = daily_rows("TMF", start, end)
    day_dates = sorted({d for d, _, s in tx if s == "一般"})
    if not day_dates:
        raise RuntimeError("期交所日盤資料為空")
    date = day_dates[-1]
    # 近月＝當天日盤成交量最大的月份（結算後會自動換到下個月）
    month = max((m for d, m, s in tx if d == date and s == "一般"),
                key=lambda m: tx[(date, m, "一般")]["volume"])

    def pick(rows, d, sess):
        v = rows.get((d, month, sess))
        return v and {k: v[k] for k in ("open", "high", "low", "close", "change", "changePct", "volume", "settle", "oi")}

    night_dates = sorted({d for d, m, s in tx if s == "盤後" and m == month})
    night = None
    if night_dates:
        nd = night_dates[-1]
        # 夜盤開始的那一天＝期交所標記日期的前一個日盤交易日
        prev = [d for d in day_dates if d < nd]
        if prev:
            night = {"startDate": prev[-1], "endDate": next_day(prev[-1]), "source": "taifex_csv",
                     "tx": pick(tx, nd, "盤後"), "tmf": pick(tmf, nd, "盤後")}
    # 剛收盤的夜盤，期交所 CSV 要到下一個交易日才有 → 用行情網(MIS)的夜盤最後成交補上
    try:
        mis = {cid: mis_night(cid) for cid in ("TXF", "TMF")}
        if all(mis.values()) and mis["TXF"]["startDate"] == mis["TMF"]["startDate"] and \
                now >= dt.datetime.strptime(mis["TMF"]["endDate"] + " 05:00", "%Y-%m-%d %H:%M").replace(tzinfo=TW) and \
                (night is None or mis["TMF"]["startDate"] > night["startDate"]):
            night = {"startDate": mis["TMF"]["startDate"], "endDate": mis["TMF"]["endDate"], "source": "taifex_mis",
                     "contract": mis["TMF"]["contract"], "tx": mis["TXF"]["q"], "tmf": mis["TMF"]["q"]}
    except Exception as e:  # noqa: BLE001 MIS 抓不到就用 CSV 的
        print(f"MIS 夜盤抓取失敗：{e}")
        if "--night-final" in sys.argv:
            alerts.append(f"⚠️ 夜盤收盤價抓取失敗（期交所行情網）：{e}。戰情室夜盤那列會停在上一個夜盤。")

    hist = {}
    for r in post_csv(CHIPS, {"queryStartDate": start, "queryEndDate": end, "commodityId": "TXF"}):
        if len(r) < 14 or r[2].strip() not in WHO:
            continue
        hist.setdefault(iso(r[0]), {})[WHO[r[2].strip()]] = {"long": int(num(r[9])), "short": int(num(r[11]))}
    chip_dates = sorted(d for d, v in hist.items() if len(v) == 3)
    if not chip_dates:
        raise RuntimeError("三大法人期貨籌碼資料為空")
    cd = chip_dates[-1]
    history = [{"date": d, **{k: hist[d][k]["long"] - hist[d][k]["short"] for k in WHO.values()}}
               for d in chip_dates[-10:]]

    # 期現貨價差：TX 日盤收盤 − 加權指數收盤（同一天才算）
    basis = None
    try:
        with open("prices.json", encoding="utf-8") as f:
            t = json.load(f)["taiex"]
        if t["date"] == date and tx.get((date, month, "一般")):
            basis = {"date": date, "taiex": t["close"],
                     "value": round(tx[(date, month, "一般")]["close"] - t["close"], 2)}
    except (OSError, ValueError, KeyError):
        pass

    out = {"generatedAt": now.isoformat(timespec="seconds"), "date": date, "contractMonth": month,
           "tx": pick(tx, date, "一般"), "tmf": pick(tmf, date, "一般"), "night": night,
           "chipsDate": cd, "chips": hist[cd], "chipsHistory": history, "basis": basis,
           "recentDays": [{"date": d, "close": tx[(d, month, "一般")]["close"]}
                          for d in day_dates[-10:] if (d, month, "一般") in tx]}
    if not out["tmf"]:
        raise RuntimeError(f"微台 {month} {date} 日盤資料缺漏")

    with open("futures.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK futures date={date} month={month} tmf={out['tmf']['close']} chips={cd}")

    if final and now.weekday() < 5:
        try:
            with open("prices.json", encoding="utf-8") as f:
                taiex_date = json.load(f)["taiex"]["date"]
        except (OSError, ValueError, KeyError):
            taiex_date = None
        today = now.strftime("%Y-%m-%d")
        if taiex_date == today and (date != today or cd != today):
            alerts.append(f"🔴 今天（{today}）是交易日，但期貨資料沒到齊：行情 {date}、法人籌碼 {cd}。")
    if alerts:
        with open("alert.txt", "a", encoding="utf-8") as f:
            f.write("\n".join(alerts) + "\n")


if __name__ == "__main__":
    main()
