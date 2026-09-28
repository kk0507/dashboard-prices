# python fetch_global.py
# 盤前全球快照：美股收盤、美股期貨、原油、美債殖利率、台積電 ADR、前一日亞股 → global.json。
# 只含公開行情，不含任何個人部位。資料來源 Yahoo Finance（跟 TMF-night-report 的美股收盤通知同一個來源）；富台期來自新加坡交易所 API。
# 期貨（原油、美股期貨）不用 Yahoo 的連續月報價算漲跌：連續月換約時歷史還是舊合約，漲跌會失真
# （2026-09-28 布蘭特連續月已換成 12 月約，算出 −3.8%，實際 +2.9%）。改找「目前報價對應的那一個合約」，用它自己的前一日收盤算。
# 微台粗估：只有在「微台上次收盤之後還有美股交易日」時才算（台股休市、美股照開）；平常夜盤收盤已經反映美股，不估。
import json, sys, datetime as dt
from zoneinfo import ZoneInfo
import requests

H = {"User-Agent": "Mozilla/5.0"}
TW = dt.timezone(dt.timedelta(hours=8))
NY = ZoneInfo("America/New_York")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
MONTHS = "FGHJKMNQUVXZ"

INDEXES = [  # (群組, 代號, 名稱)
    ("us", "^SOX", "費城半導體"), ("us", "^IXIC", "那斯達克"), ("us", "^GSPC", "標普500"), ("us", "^DJI", "道瓊"),
    ("rates", "^TNX", "美債10年"), ("rates", "^TYX", "美債30年"),
    ("asia", "^N225", "日經225"), ("asia", "^KS11", "韓國KOSPI"),
]
FUTURES = [  # (群組, 連續月代號, 合約根, 交易所, 只有季月, 名稱)
    ("usFut", "ES=F", "ES", "CME", True, "標普期貨"), ("usFut", "NQ=F", "NQ", "CME", True, "那指期貨"),
    ("oil", "CL=F", "CL", "NYM", False, "WTI原油"), ("oil", "BZ=F", "BZ", "NYM", False, "布蘭特原油"),
]
BETAS = [("^SOX", "費半", 0.36), ("^IXIC", "那指", 0.67)]  # us_correlation.py：微台夜盤 ≈ 費半×0.36 ≈ 那指×0.67


def chart(sym, rng="10d"):
    r = requests.get(CHART + sym, params={"range": rng, "interval": "1d"}, headers=H, timeout=20)
    r.raise_for_status()
    j = r.json()["chart"]["result"][0]
    m = j["meta"]
    tz = ZoneInfo(m.get("exchangeTimezoneName") or "UTC")
    bars = [(dt.datetime.fromtimestamp(t, tz).date(), c)
            for t, c in zip(j.get("timestamp") or [], j["indicators"]["quote"][0]["close"]) if c is not None]
    t = dt.datetime.fromtimestamp(m["regularMarketTime"], tz)
    return {"price": m["regularMarketPrice"], "time": t, "bars": bars}


def last_and_prev(c):
    """最新價＝regularMarketPrice；前一日＝最新價所在交易日之前的最後一根收盤。"""
    day = c["time"].date()
    prev = [v for d, v in c["bars"] if d < day]
    if not prev:
        raise ValueError("沒有前一日收盤")
    return day, c["price"], prev[-1]


def item(group, sym, name, day, price, prev, t):
    x = {"group": group, "sym": sym, "name": name, "date": day.isoformat(),
         "time": t.astimezone(TW).isoformat(timespec="minutes"), "price": round(price, 4), "prev": round(prev, 4)}
    if group == "rates":
        x["changeBp"] = round((price - prev) * 100, 1)
    else:
        x["change"] = round(price - prev, 4)
        x["changePct"] = round((price / prev - 1) * 100, 2)
    return x


def same_contract(root, exch, quarterly, cont_price, today):
    """找出報價跟連續月一樣的那個合約（往後 13 個月內）。"""
    best = None
    for i in range(13):
        m0 = today.month - 1 + i
        y, m = today.year + m0 // 12, m0 % 12 + 1
        if quarterly and m not in (3, 6, 9, 12):
            continue
        sym = f"{root}{MONTHS[m - 1]}{y % 100}.{exch}"
        try:
            c = chart(sym)
        except Exception:  # noqa: BLE001 該月沒有合約或抓不到
            continue
        diff = abs(c["price"] - cont_price)
        if best is None or diff < best[0]:
            best = (diff, sym, c)
        if diff == 0:
            break
    if best and best[0] <= cont_price * 0.0005:
        return best[1], best[2]
    return None, None


def tmf_base():
    """微台上次收盤：日盤（date 13:45）與夜盤（endDate 05:00）取較晚者。"""
    try:
        f = json.load(open("futures.json", encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cands = []
    if f.get("tmf") and f.get("date"):
        cands.append((dt.datetime.fromisoformat(f["date"] + "T13:45:00+08:00"), f["tmf"]["close"], "日盤"))
    n = f.get("night") or {}
    if n.get("tmf") and n.get("endDate"):
        cands.append((dt.datetime.fromisoformat(n["endDate"] + "T05:00:00+08:00"), n["tmf"]["close"], "夜盤"))
    return max(cands) if cands else None


def estimate(charts):
    base = tmf_base()
    if not base:
        return None
    t0, close, sess = base
    est, parts, days = [], [], set()
    for sym, name, beta in BETAS:
        bars = charts[sym]["bars"]
        close_t = lambda d: dt.datetime.combine(d, dt.time(16, 0), NY)  # noqa: E731
        before = [v for d, v in bars if close_t(d) <= t0]
        after = [(d, v) for d, v in bars if close_t(d) > t0]
        if not before or not after:
            continue
        cum = after[-1][1] / before[-1] - 1
        days.update(d.isoformat() for d, _ in after)
        parts.append({"name": name, "cumPct": round(cum * 100, 2)})
        est.append(beta * cum * close)
    if not est:
        return None
    lo, hi = min(est), max(est)
    return {"base": close, "baseSession": sess, "baseTime": t0.isoformat(timespec="minutes"),
            "usDays": sorted(days), "parts": parts,
            "lowPts": round(lo), "highPts": round(hi), "low": round(close + lo), "high": round(close + hi),
            "note": "粗估：微台大約是費半漲跌的0.36倍、那指的0.67倍，約四成的波動解釋不到"}


SGX = "https://api.sgx.com/derivatives/v1.0"


def sgx_twn():
    """富台期（新加坡 FTSE Taiwan 期貨）：台股休市時照常交易，是台股最直接的參考。
    取成交量最大的合約（避開換月時連續月新舊合約混算），漲跌對「微台最後一次日盤收盤那天」的富台結算價
    （富台結算在台股日盤收盤時定），並換算成微台點數。拿不到就丟例外，由 main 記進 errors。"""
    try:
        f = json.load(open("futures.json", encoding="utf-8"))
        tmf_date, tmf_close = f["date"], f["tmf"]["close"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"futures.json 沒有微台日盤收盤：{e}")
    rows = requests.get(f"{SGX}/contract-code/TWN", params={
        "order": "asc", "orderby": "delivery-month", "category": "futures",
        "t": int(dt.datetime.now().timestamp() * 1000)}, headers=H, timeout=20).json()["data"]
    rows = [r for r in rows if not r["symbol"].endswith("_TAIC")]
    vol = {}
    for r in rows:
        vol[r["symbol"]] = vol.get(r["symbol"], 0) + (r.get("total-volume") or 0)
    sym = max(vol, key=vol.get)
    mine = {r["current-trading-session"]: r for r in rows if r["symbol"] == sym}
    night, day = mine.get("1", {}), mine.get("0", {})
    live = night if night.get("last-traded-price-abs") else day
    price = live.get("last-traded-price-abs") or day.get("daily-settlement-price-abs")
    hist = requests.get(f"{SGX}/history/symbol/{sym}", params={"days": "1m", "category": "futures"},
                        headers=H, timeout=20).json()["data"]
    settle = {h["base-date"]: h["daily-settlement-price-abs"] for h in hist if h.get("daily-settlement-price-abs")}
    if day.get("daily-settlement-price-abs"):
        settle[day["base-date"]] = day["daily-settlement-price-abs"]
    base = settle.get(tmf_date.replace("-", ""))
    if not price or not base:
        raise ValueError(f"富台期 {sym} 缺價格或 {tmf_date} 結算價")
    t = dt.datetime.strptime(live["last-update-time"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TW)
    x = item("twFut", sym, "富台期", t.date(), price, base, t)
    x.update({"contract": f"{sym}.SGX", "session": "夜間盤" if live is night else "日間盤",
              "baseDate": tmf_date, "tmfBase": tmf_close, "tmfEquiv": round(tmf_close * price / base)})
    return x


def main():
    now = dt.datetime.now(TW)
    items, errors, charts = [], [], {}
    for group, sym, name in INDEXES:
        try:
            c = chart(sym)
            charts[sym] = c
            day, price, prev = last_and_prev(c)
            items.append(item(group, sym, name, day, price, prev, c["time"]))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}：{e}")
    for group, cont, root, exch, quarterly, name in FUTURES:
        try:
            c = chart(cont)
            sym, cc = same_contract(root, exch, quarterly, c["price"], now.date())
            if not cc:
                raise ValueError("找不到對應合約，換約中漲跌不可靠，略過")
            day, price, prev = last_and_prev(cc)
            x = item(group, sym, name, day, price, prev, cc["time"])
            x["contract"] = sym
            items.append(x)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}：{e}")
    # 台積電 ADR：1 股 ADR＝5 股台股；溢價＝ADR 換算台幣 ÷ 台股收盤 − 1
    try:
        tsm, fx = chart("TSM"), chart("TWD=X")
        day, price, prev = last_and_prev(tsm)
        x = item("adr", "TSM", "台積電ADR", day, price, prev, tsm["time"])
        q = json.load(open("prices.json", encoding="utf-8"))["quotes"]["2330"]
        twd = price * fx["price"] / 5
        x.update({"twdRate": round(fx["price"], 3), "twdEquiv": round(twd, 1), "twClose": q["c"], "twDate": q["d"],
                  "premiumPct": round((twd / q["c"] - 1) * 100, 1)})
        items.append(x)
    except Exception as e:  # noqa: BLE001
        errors.append(f"台積電ADR：{e}")

    try:
        items.append(sgx_twn())
    except Exception as e:  # noqa: BLE001
        errors.append(f"富台期：{e}")

    us = [x for x in items if x["group"] == "us"]
    if len(us) < 2:
        print("美股收盤抓不到，不輸出", errors, file=sys.stderr)
        sys.exit(1)
    out = {"generatedAt": now.isoformat(timespec="seconds"),
           "usSession": max(x["date"] for x in us),
           "items": items,
           "estimate": estimate(charts) if "^SOX" in charts and "^IXIC" in charts else None,
           "errors": errors}
    with open("global.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for x in items:
        chg = f"{x['changeBp']:+.1f}bp" if "changeBp" in x else f"{x['changePct']:+.2f}%"
        print(f"{x['name']:<8} {x['price']:>12,.2f} {chg:>9}  {x['date']}  {x.get('contract', '')}")
    print("estimate:", out["estimate"])
    if errors:
        print("errors:", errors)


if __name__ == "__main__":
    main()
