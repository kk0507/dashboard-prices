# python build_signals.py  → signals.json（成交值前 300 檔的技術＋籌碼硬數字；只含公開市場資料）
# 內容沒變就不會產生 commit（不寫時間戳，只寫各來源的資料日）。
import json, math, statistics
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


def low_third(vals):
    """同一批股票裡「最低那 1/3」的切點（跟回測一樣：平均排名百分位 ≤ 1/3）。回傳切點值，≤ 它就算最低那 1/3。"""
    v = sorted(vals)
    if len(v) < 30:
        return None
    cut, i = None, 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[j + 1] == v[i]:
            j += 1
        if ((i + j) / 2 + 1) / len(v) <= 1 / 3:  # 同值取平均名次
            cut = v[i]
        i = j + 1
    return cut


def revenue_yoy():
    """官方月營收（build_screen 存的 data/revenue.csv）→ {代號: (最新月, [年增%… 新到舊])}；中間缺月就斷，資料斷掉（落後兩個月）不算。"""
    m = defaultdict(dict)
    for r in read_csv("revenue"):
        try:
            rv, pv = float(r["revenue"]), float(r["prev_year"])
        except (TypeError, ValueError):
            continue
        if pv > 0:
            m[r["code"]][r["date"]] = round((rv / pv - 1) * 100, 1)
    latest = max((ym for x in m.values() for ym in x), default="")

    def prev(k):
        y, mo = int(k[:4]), int(k[5:])
        return f"{y - (mo == 1):04d}-{(mo - 2) % 12 + 1:02d}"
    out = {}
    for c, x in m.items():
        ym = max(x)
        if prev(prev(latest)) >= ym:   # 別人都公布兩個月了這檔還沒有
            continue
        ys, k = [], ym
        while k in x and len(ys) < 6:
            ys.append(x[k])
            k = prev(k)
        out[c] = (ym, ys)
    return out, latest


def month_label(ym, back):
    mo = (int(ym[5:]) - 1 - back) % 12 + 1
    return f"{mo} 月"


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


POOL_YOY, MIN_VALUE = 20, 5e8  # 營收池＝連 3 個月營收年增 ≥20%；切點只用 20 日均成交值 ≥5 億的個股算（跟回測同一批）


def add_factor_scores(items, dates):
    """回測有用的因素（2026-10-19 全因素回測，2022-12～2026-09）→ 營收分、外資賣壓兩個分數＋標籤。
    切點＝這次股票池裡「最低那 1/3」，每次重算（寫在 cuts，方便核對）。回傳 cuts。"""
    rev, latest = revenue_yoy()
    dates["revenue"] = latest
    for c, it in items.items():
        if c in rev and len(rev[c][1]) >= 3:
            ym, ys = rev[c]
            it["rev_ym"], it["yoy3"] = ym, ys[:3]
    peers = [it for c, it in items.items() if not c.startswith("00") and it.get("vol20")
             and it["last"] * it["vol20"] * 1000 >= MIN_VALUE]
    cuts = {"n": len(peers),
            "yoyMin": low_third([min(it["yoy3"]) for it in peers if "yoy3" in it]),
            "foreign7Pct": low_third([it["foreign7_pct"] for it in peers if "foreign7_pct" in it]),
            "hv20": low_third([it["hv20"] for it in peers if "hv20" in it]),
            "foreignBuyDays": low_third([it["foreign_buy_days"] for it in peers if "foreign_buy_days" in it])}
    for c, it in items.items():
        auto, flags = it.setdefault("auto", {}), []
        ymin = min(it["yoy3"]) if "yoy3" in it else None
        pool = ymin is not None and ymin >= POOL_YOY
        if ymin is not None:
            # 營收分：營收池 +2；近 3 月最低年增高於「最低 1/3」切點 +1；切點以下但沒負成長 −1；有負成長 −2
            lo = cuts["yoyMin"]
            s = 2 if pool else -2 if ymin < 0 else 1 if lo is not None and ymin > lo else -1
            ym = it["rev_ym"]
            mon = "、".join(f"{month_label(ym, k)} {v:+.1f}%" for k, v in reversed(list(enumerate(it["yoy3"]))))
            why = ("連 3 個月年增 20% 以上（營收池）" if pool else "有月份負成長" if ymin < 0 else
                   f"最低 {ymin:.1f}% 高於股票池最差 1/3 切點 {lo:.1f}%" if s == 1 else f"最低 {ymin:.1f}% 落在股票池最差 1/3（切點 {lo:.1f}%）")
            auto["rev"] = {"s": s, "note": f"月營收年增 {mon}；{why}（官方月營收，最新 {ym}）"}
            if lo is not None and ymin <= lo:
                flags.append("營收衰退")
        if "foreign7_pct" in it and it.get("inst_days", 0) >= 5:
            fp, bd, cut = it["foreign7_pct"], it["foreign_buy_days"], cuts["foreign7Pct"]
            big = cut is not None and fp <= cut
            s = -int(big) - int(bd <= 1)   # 只扣不加：回測外資大買沒用、外資賣才有用
            parts = [f"外資 7 日 {it['foreign7']:+,} 張，占成交量 {fp:+.1f}%" + (f"（股票池賣最多 1/3，切點 {cut:+.1f}%）" if big else ""),
                     f"{it['inst_days']} 天買 {bd} 天" + ("（連賣）" if bd <= 1 else "")]
            auto["fsell"] = {"s": s, "note": "；".join(parts) + f"（官方資料，資料日 {it['inst_date']}）"}
            if bd <= 1:
                flags.append("外資連賣")
            if big and ymin is not None and not pool:
                flags.append("外資大賣＋營收沒成長")
            if (cuts["hv20"] is not None and it.get("hv20", 1e9) <= cuts["hv20"]
                    and cuts["foreignBuyDays"] is not None and bd <= cuts["foreignBuyDays"]):
                flags.append("低波動＋外資少買")
        if it.get("margin_chg_pct", 0) >= 10:
            flags.append("融資急增")   # 熱門股，回測不是利空（散戶追強勢股）
        if it.get("gap_ma20", 0) >= 15:
            flags.append("離20日線過遠")
        if flags:
            it["flags"] = flags
        if not auto:
            del it["auto"]
    return cuts


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
        if len(rows) >= 4:
            # 最近 3 個交易日：累計漲跌%，以及每天各自的漲跌%（由舊到新）
            it["r3"] = round((last / cl[-4] - 1) * 100, 1)
            it["d3"] = [round((cl[i] / cl[i - 1] - 1) * 100, 1) for i in (-3, -2, -1)]
        if len(rows) >= 21:
            ma20 = statistics.mean(cl[-20:])
            tr = [max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1])) for i in range(1, len(rows))]
            atr = statistics.mean(tr[-14:])
            it.update({"ma20": round(ma20, 1), "atr14": round(atr, 1), "atr_pct": round(atr / last * 100, 1),
                       "gap_ma20": round((last / ma20 - 1) * 100, 1), "low10": min(lo[-10:]), "low20": min(lo[-20:]),
                       "r5": round((last / cl[-6] - 1) * 100, 1), "r20": round((last / cl[-21] - 1) * 100, 1),
                       "d_hi": round((last / max(hi) - 1) * 100, 1)})
            rets = [math.log(cl[i] / cl[i - 1]) for i in range(-20, 0)]
            it["hv20"] = round(statistics.stdev(rets) * math.sqrt(252) * 100, 1)  # 20 日波動度（年化 %）
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
        items[c] = it
    rev_note = add_factor_scores(items, dates)
    out = {"note": "成交值前約300檔的公開市場資料計算結果；籌碼單位為張，融資為餘額張數；不是買賣建議。", "dates": dates,
           "cuts": rev_note, "count": len(items), "items": items}
    json.dump(out, open("signals.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("OK signals", len(items), dates)


if __name__ == "__main__":
    main()
