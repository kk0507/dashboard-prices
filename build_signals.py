# python build_signals.py  → signals.json（成交值前 300 檔的技術＋籌碼硬數字；只含公開市場資料）
# 內容沒變就不會產生 commit（不寫時間戳，只寫各來源的資料日）。
import json, statistics
from collections import defaultdict
from store_lib import *  # noqa: F401,F403


def by_code(rows, key_sort="date"):
    d = defaultdict(list)
    for r in rows:
        d[r["code"]].append(r)
    for c in d:
        d[c].sort(key=lambda r: r[key_sort])
    return d


def clamp(v, lo=-2, hi=2):
    return max(lo, min(hi, v))


def auto_dims(it):
    """規則式的籌碼、技術面分數（-2～+2），讓排程不必自己心算。規則寫死、可重現；「排程版」，週日由 Claude 手動升級。"""
    out = {}
    if "foreign7_pct" in it:
        fp, dp = it["foreign7_pct"], it.get("domestic7_pct", 0)
        s = 2 if fp >= 4 else 1 if fp >= 1 else 0 if fp > -1 else -1 if fp > -4 else -2
        if s < 1 and dp >= 4:
            s += 1  # 內資（投信、自營）買盤撐住
        elif s > -1 and dp <= -4:
            s -= 1  # 內資（投信、自營）賣壓
        if it.get("margin_chg_pct", 0) >= 10:
            s -= 1  # 融資 7 日增 10% 以上＝散戶借錢追價
        note = (f"外資 7 日 {it['foreign7']:+,} 張（{it['foreign_buy_days']}/{it['inst_days']} 日買，約占成交量 {fp:+.1f}%）、"
                f"投信 {it['trust7']:+,}、自營 {it['dealer7']:+,}")
        if "margin_chg_pct" in it:
            note += f"；融資 7 日 {it['margin_chg_pct']:+.1f}%"
        out["chip"] = {"s": clamp(s), "note": note + f"（官方資料，資料日 {it['inst_date']}）"}
    if all(k in it for k in ("ma20", "ma60", "gap_ma20", "r20")):
        last, ma20, ma60, gap, r20 = it["last"], it["ma20"], it["ma60"], it["gap_ma20"], it["r20"]
        if last > ma20 and ma20 > ma60:
            s = 0 if gap >= 20 else 1
        elif last < ma20 and last < ma60:
            s = -2 if r20 <= -15 else -1
        elif last < ma20 and r20 <= -10:
            s = -1
        else:
            s = 0
        note = f"收盤 {last:g}，20 日線 {ma20:g}（{gap:+.1f}%）、60 日線 {ma60:g}；近 20 日 {r20:+.1f}%、離近期高點 {it['d_hi']:+.1f}%"
        if gap >= 20:
            note += "；離 20 日線 20% 以上，視為過熱"
        out["tech"] = {"s": s, "note": note + f"（官方資料，資料日 {it['date']}）"}
    return out


def main():
    pr, inst, mg = by_code(read_csv("prices")), by_code(read_csv("inst")), by_code(read_csv("margin"))
    universe = load_universe().get("codes", [])
    names = {}
    try:
        for r in http("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"):
            names[r["Code"].strip()] = r.get("Name", "").strip()
        for r in http("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"):
            names[r["SecuritiesCompanyCode"].strip()] = r.get("CompanyName", "").strip()
    except RuntimeError:
        pass
    items, dates = {}, {"prices": "", "inst": "", "margin": ""}
    for c in universe:
        rows = pr.get(c, [])
        if len(rows) < 2:
            continue
        cl = [float(r["close"]) for r in rows]
        hi = [float(r["high"]) for r in rows]
        lo = [float(r["low"]) for r in rows]
        last = cl[-1]
        it = {"name": names.get(c, ""), "date": rows[-1]["date"], "last": last, "days": len(rows)}
        vols = [float(r["volume"]) / 1000 for r in rows]  # 張
        if len(vols) >= 20:
            it["vol20"] = round(statistics.mean(vols[-20:]))
        dates["prices"] = max(dates["prices"], it["date"])
        if len(rows) >= 21:
            ma20 = statistics.mean(cl[-20:])
            tr = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, len(rows))]
            atr = statistics.mean(tr[-14:])
            it.update({"ma20": round(ma20, 1), "atr14": round(atr, 1), "atr_pct": round(atr / last * 100, 1),
                       "gap_ma20": round((last / ma20 - 1) * 100, 1), "low10": min(lo[-10:]), "low20": min(lo[-20:]),
                       "r5": round((last / cl[-6] - 1) * 100, 1), "r20": round((last / cl[-21] - 1) * 100, 1),
                       "d_hi": round((last / max(hi) - 1) * 100, 1)})
        if len(rows) >= 61:
            it["ma60"] = round(statistics.mean(cl[-60:]), 1)
            it["r60"] = round((last / cl[-61] - 1) * 100, 1)
        ir = inst.get(c, [])[-7:]
        if ir:
            f = [float(r["foreign"]) / 1000 for r in ir]
            it.update({"inst_days": len(ir), "foreign7": round(sum(f)), "foreign_buy_days": sum(1 for v in f if v > 0),
                       "trust7": round(sum(float(r["trust"]) for r in ir) / 1000),
                       "dealer7": round(sum(float(r["dealer"]) for r in ir) / 1000), "inst_date": ir[-1]["date"]})
            dates["inst"] = max(dates["inst"], ir[-1]["date"])
            base = it.get("vol20", 0) * len(ir)
            if base > 0:
                it["foreign7_pct"] = round(it["foreign7"] / base * 100, 1)
                it["domestic7_pct"] = round((it["trust7"] + it["dealer7"]) / base * 100, 1)
        mr = mg.get(c, [])[-8:]
        if len(mr) >= 2:
            b0, b1 = float(mr[0]["margin_bal"]), float(mr[-1]["margin_bal"])
            it.update({"margin_bal": b1, "margin_chg": round(b1 - b0), "margin_chg_pct": round((b1 - b0) / b0 * 100, 1) if b0 else 0,
                       "short_chg": round(float(mr[-1]["short_bal"]) - float(mr[0]["short_bal"])), "margin_date": mr[-1]["date"], "margin_days": len(mr)})
            dates["margin"] = max(dates["margin"], mr[-1]["date"])
        auto = auto_dims(it)
        if auto:
            it["auto"] = auto
        # 簡單旗標，方便排程篩選（不是買賣訊號）
        flags = []
        if it.get("foreign7", 0) > 0 and it.get("foreign_buy_days", 0) >= 5:
            flags.append("外資連買")
        if it.get("foreign7", 0) < 0 and it.get("trust7", 0) > 0:
            flags.append("外資賣投信買")
        if it.get("margin_chg_pct", 0) >= 10:
            flags.append("融資急增")
        if it.get("gap_ma20", 0) >= 15:
            flags.append("離20日線過遠")
        if flags:
            it["flags"] = flags
        items[c] = it
    out = {"note": "成交值前約300檔的公開市場資料計算結果；籌碼單位為張，融資為餘額張數；不是買賣建議。", "dates": dates,
           "count": len(items), "items": items}
    json.dump(out, open("signals.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("OK signals", len(items), dates)


if __name__ == "__main__":
    main()
