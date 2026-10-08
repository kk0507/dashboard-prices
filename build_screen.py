# python build_screen.py [--weekly]  → screen.json（選股頁的資料：股票池每一檔的所有篩選欄位；只含公開市場資料）
# 先抓官方最新月營收追加到 data/revenue.csv、更新 data/industry.json，再把 signals.json（技術、籌碼）＋月營收＋本益比位置
# ＋最新一季累計 EPS（官方，累積在 data/eps.csv）＋處置／注意股＋大盤大跌日表現合成一份。頁面拿這份自己套條件篩，這裡不做篩選、不排名。
# 每週摘要（新進／掉出／轉弱／轉強）放在 changes 欄位，由戰情室顯示。--weekly：把本週本益比記進歷史。
# themes.json（人工歸類的題材分組）＋月營收 → 每個題材的營收循環階段，放在 themes 欄位。
# 內容沒變就不會產生 commit（不寫時間戳，只寫各來源的資料日）。
import bisect, datetime as dt, json, os, sys
from collections import defaultdict
from store_lib import *  # noqa: F401,F403

POOL_YOY, POOL_MONTHS, MIN_VALUE = 20, 3, 5e8  # 預設「營收池」：連 3 個月年增 ≥20%、20 日均成交值 ≥5 億（只用來算每週變化，頁面可自己調）
REV_KEEP = 6      # 每檔留最近幾個月的年增率
ATTN_DAYS = 5     # 最近幾個公告日內被列注意股就標記


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def update_revenue(uni):
    rows = []
    for f in (twse_revenue_latest, tpex_revenue_latest):
        try:
            rows += f()
        except RuntimeError as e:
            print("WARN 月營收抓取失敗：", e)
    ind = jload(os.path.join(DATA, "industry.json"), {})
    for r in rows:
        if r["code"] in uni and r["industry"]:
            ind[r["code"]] = r["industry"]
    jsave(os.path.join(DATA, "industry.json"), dict(sorted(ind.items())))
    return append_rows("revenue", [r for r in rows if r["code"] in uni]), ind


def update_eps(uni):
    rows = []
    for f in (twse_eps_latest, tpex_eps_latest):
        try:
            rows += f()
        except RuntimeError as e:
            print("WARN 每股盈餘抓取失敗：", e)
    return append_rows("eps", [r for r in rows if r["code"] in uni])


def next_month(ym):
    y, m = int(ym[:4]), int(ym[5:])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def ym_shift(ym, k):
    t = int(ym[:4]) * 12 + int(ym[5:]) - 1 + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


STAGES = {1: "衰退擴大", 2: "衰退收斂", 3: "剛轉正", 4: "成長加速", 5: "成長減速"}


def theme_stages(themes, raw):
    """題材的營收循環階段。raw＝{代號: {"YYYY-MM": (當月營收, 去年同月營收)}}。
    個股「近 3 個月合計營收年增」取題材中位數（至少 3 檔有資料才算），拿現在、3 個月前、6 個月前三個值分五個階段：
    1 衰退擴大（年減且比 3 個月前差）、2 衰退收斂（年減但比 3 個月前好）、3 剛轉正（6 個月前還是年減）、4 成長加速、5 成長減速。
    月份取「八成以上的題材股都公布了」的最新一個月，公布期中間不會只拿少數幾檔來算。"""
    cnt = defaultdict(int)
    for cs in themes.values():
        for c in cs:
            for ym in raw.get(c, {}):
                cnt[ym] += 1
    if not cnt:
        return "", {}
    full = max(ym for ym, k in cnt.items() if k >= 0.8 * max(cnt.values()))

    def yoy3(c, ym):
        v = [raw.get(c, {}).get(ym_shift(ym, -j)) for j in range(3)]
        if None in v or sum(x[1] for x in v) <= 0:
            return None
        return sum(x[0] for x in v) / sum(x[1] for x in v) - 1

    def med(cs, ym):
        v = sorted(x for x in (yoy3(c, ym) for c in cs) if x is not None)
        if len(v) < 3:
            return None
        return (v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2

    out = {}
    for name, cs in themes.items():
        o = {"codes": cs}
        now, p3, p6 = med(cs, full), med(cs, ym_shift(full, -3)), med(cs, ym_shift(full, -6))
        if None not in (now, p3, p6):
            if now < 0:
                st = 1 if now <= p3 else 2
            elif p6 < 0:
                st = 3
            else:
                st = 4 if now > p3 else 5
            o.update(stage=st, yoy=round(now * 100, 1), p3=round(p3 * 100, 1), p6=round(p6 * 100, 1))
        out[name] = o
    return full, out


def main():
    weekly = "--weekly" in sys.argv
    universe = load_universe().get("codes", [])
    themes = jload("themes.json", {})   # 題材分組（人工歸類，不是官方分類；一檔只放一組）
    themes.pop("_note", None)
    # 題材裡有些股票不在股票池（成交值不夠大），月營收照樣累積，題材的中位數才不會少算
    added, ind = update_revenue(set(universe) | {c for cs in themes.values() for c in cs})
    eps_added = update_eps(set(universe))
    sig = jload("signals.json", {"items": {}, "dates": {}})
    ann = jload("announcements.json", {})
    hist = jload(os.path.join(DATA, "per_hist.json"), {})      # {代號: 由小到大的歷史本益比（每週一筆）}，本機回補＋每週追加
    down = jload(os.path.join(DATA, "downday.json"), {}).get("items", {})  # 大盤單日大跌時各股的平均漲跌，本機回補
    week = jload(os.path.join(DATA, "screen_week.json"), {})   # {"cur": {"week","items"}, "prev": {...}}：算「跟上週比」用

    per = {}
    for f in (twse_per_latest, tpex_per_latest):
        try:
            per.update(f())
        except RuntimeError as e:
            print("WARN 本益比抓取失敗：", e)

    rev, raw = defaultdict(dict), defaultdict(dict)
    for r in read_csv("revenue"):
        if n(r["prev_year"]) > 0:
            rev[r["code"]][r["date"]] = round((n(r["revenue"]) / n(r["prev_year"]) - 1) * 100, 1)
            raw[r["code"]][r["date"]] = (n(r["revenue"]), n(r["prev_year"]))
    theme_ym, theme_out = theme_stages(themes, raw)
    latest_ym = max((ym for m in rev.values() for ym in m), default="")

    eps = defaultdict(dict)   # {代號: {"YYYY-Qn": 年初到該季的累計 EPS}}
    for r in read_csv("eps"):
        eps[r["code"]][r["date"]] = n(r["eps"])

    inst = defaultdict(list)
    for r in read_csv("inst"):
        inst[r["code"]].append((r["date"], n(r["foreign"])))

    disp = {x["code"] for x in ann.get("activeDispositions", [])}
    adays = sorted({x["date"] for x in ann.get("items", [])})[-ATTN_DAYS:]
    attn = {x["code"] for x in ann.get("items", []) if x.get("type") == "注意股" and x["date"] in adays}

    items = {}
    for c in universe:
        it = sig["items"].get(c)
        if not it or "vol20" not in it or "ma20" not in it:
            continue
        o = {"name": it["name"], "ind": ind.get(c, ""), "last": it["last"], "value": round(it["last"] * it["vol20"] * 1000 / 1e8, 1),
             "gap20": it["gap_ma20"], "r20": it["r20"], "atr": it["atr_pct"]}
        if "ma60" in it:
            o["gap60"] = round((it["last"] / it["ma60"] - 1) * 100, 1)
        # 月營收：最近幾個月的年增率（新到舊），中間缺月就斷
        m = rev.get(c, {})
        if m:
            ym = max(m)
            ys, k = [], ym
            while k in m and len(ys) < REV_KEEP:
                ys.append(m[k])
                y, mo = int(k[:4]), int(k[5:])
                k = f"{y - (mo == 1):04d}-{(mo - 2) % 12 + 1:02d}"
            # 別人都公布兩個月了這檔還沒有＝資料斷了，不算
            if next_month(next_month(ym)) > latest_ym:
                o["revYm"], o["yoy"] = ym, ys
        # 每股盈餘：最新一季的累計值，和去年同期的累計值（有才放）
        e = eps.get(c, {})
        if e:
            q = max(e)
            o["epsQ"], o["eps"] = q, e[q]
            pq = f"{int(q[:4]) - 1:04d}{q[4:]}"
            # 名稱帶 * 的是面額不是 10 元（多半分割過），每股盈餘跟去年不能直接比，不放去年同期
            if pq in e and not it["name"].endswith("*"):
                o["epsPrev"] = e[pq]
        # 籌碼
        if "foreign7_pct" in it:
            o["f7"] = it["foreign7_pct"]
            o["t7"] = round(it["trust7"] / (it["vol20"] * it["inst_days"]) * 100, 1)
            streak = 0
            for _, v in sorted(inst.get(c, []), reverse=True):
                if v <= 0:
                    break
                streak += 1
            o["fbuy"] = streak
        if "margin_chg_pct" in it:
            o["mg"] = it["margin_chg_pct"]
        a = it.get("auto", {})
        if "chip" in a and "tech" in a:
            o["chip"], o["tech"] = a["chip"]["s"], a["tech"]["s"]
            o["ct"] = round((4 * o["chip"] + 2 * o["tech"]) / 6, 1)
        # 本益比與它在自己歷史裡的位置（0＝最便宜、100＝最貴）
        p = per.get(c, 0)
        if p > 0:
            o["per"] = p
            h = hist.get(c, [])
            if len(h) >= 60:
                o["perPct"] = round(bisect.bisect_right(h, p) / len(h) * 100)
        if c in disp:
            o["flag"] = "處置中"
        elif c in attn:
            o["flag"] = "注意股"
        if c in down:
            o["dd"] = down[c]["avg"]
        items[c] = o

    def in_pool(o):
        return o["value"] * 1e8 >= MIN_VALUE and len(o.get("yoy", [])) >= POOL_MONTHS and min(o["yoy"][:POOL_MONTHS]) >= POOL_YOY

    # 跟上週比：以每週最後一次執行的狀態為準
    price_day = sig.get("dates", {}).get("prices", "")
    wk = "-".join(f"{x:02d}" for x in dt.date.fromisoformat(price_day).isocalendar()[:2]) if price_day else ""
    snap = {c: [int(in_pool(o)), int(o.get("tech", 0) < 0)] for c, o in items.items()}
    if week.get("cur", {}).get("week") not in (None, wk):
        week["prev"] = week["cur"]
    week["cur"] = {"week": wk, "date": price_day, "items": snap}
    prev = week.get("prev", {})
    chg = {"in": [], "out": [], "weak": [], "strong": []}
    for c, (p, w) in snap.items():
        old = prev.get("items", {}).get(c)
        if old is None:
            if p and prev:
                chg["in"].append(c)
        elif p and not old[0]:
            chg["in"].append(c)
        elif old[0] and not p:
            chg["out"].append(c)
        elif p and w and not old[1]:
            chg["weak"].append(c)
        elif p and old[1] and not w:
            chg["strong"].append(c)
    chg["out"] += [c for c, old in prev.get("items", {}).items() if old[0] and c not in snap]
    jsave(os.path.join(DATA, "screen_week.json"), week)

    pool = [c for c, o in items.items() if in_pool(o)]
    out = {"note": "股票池每一檔的篩選欄位（公開市場資料計算）；value＝20 日均成交值（億）、yoy＝月營收年增%（新到舊）、eps＝epsQ 那一季為止的年初累計每股盈餘（元）、epsPrev＝去年同期累計、perPct＝本益比在自己歷史的位置、"
                   "dd＝大盤單日跌 2% 以上那些天的平均漲跌%；themes＝人工歸類的題材與營收循環階段（stage 1 衰退擴大／2 衰退收斂／3 剛轉正／4 成長加速／5 成長減速，yoy／p3／p6＝題材內個股近 3 個月合計營收年增%的中位數：現在／3 個月前／6 個月前，月份見 dates.themes）；不是買賣建議。",
           "dates": {**sig.get("dates", {}), "revenue": latest_ym, "themes": theme_ym, "downday": jload(os.path.join(DATA, "downday.json"), {}).get("asOf", "")},
           "pool": {"yoy": POOL_YOY, "months": POOL_MONTHS, "minValue": MIN_VALUE / 1e8, "count": len(pool)},
           "prevDate": prev.get("date", ""), "changes": {k: sorted(v) for k, v in chg.items()}, "count": len(items), "themes": theme_out, "items": items}
    jsave("screen.json", out)
    print(f"OK screen {len(items)} 檔，營收池 {len(pool)} 檔，營收到 {latest_ym}（新增 {added} 列），EPS 新增 {eps_added} 列，變化 { {k: len(v) for k, v in chg.items()} }")

    if weekly:
        for c, o in items.items():
            if "per" in o:
                bisect.insort(hist.setdefault(c, []), o["per"])
        jsave(os.path.join(DATA, "per_hist.json"), hist)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e)
        sys.exit(1)
