# python update_store.py
# 每個交易日跑：抓最新的價量、三大法人、融資融券，只追加「成交值前 300 名」的股票到 data/*.csv（同日同代號不重複）。
# 各來源日期不同步（融資融券比較晚公布），所以每個來源各自抓「最新可用的一天」。
import datetime as dt
import sys
from store_lib import *  # noqa: F401,F403

TOP_N, KEEP_N = 300, 450


def recent_weekdays(k=6):
    d = dt.datetime.now(TW).date()
    out = []
    while len(out) < k:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d -= dt.timedelta(days=1)
    return out


def main():
    warn = []
    days = recent_weekdays()

    # --- 價量 ---
    twp = []
    for d in days:
        twp = twse_prices(d)
        if len(twp) >= 800:
            break
    try:
        tpp = tpex_prices_latest()
    except RuntimeError as e:
        tpp, _ = [], warn.append(f"櫃買價量失敗：{e}")
    if len(twp) < 800:
        warn.append(f"證交所價量筆數異常：{len(twp)}")
    if not twp and not tpp:
        raise RuntimeError("價量完全抓不到")

    # --- 股票池：成交值前 300 ∪ 舊池中仍在前 450 者（變動小、不會一直換）---
    ranked = sorted([r for r in twp + tpp if is_stock(r["code"])], key=lambda r: -r["value"])
    top = [r["code"] for r in ranked[:TOP_N]]
    keep = set(r["code"] for r in ranked[:KEEP_N])
    old = load_universe().get("codes", [])
    universe = sorted(set(top) | (set(old) & keep))
    save_universe({"codes": universe, "asOf": max([r["date"] for r in twp + tpp] or [""]), "top": TOP_N})
    uni = set(universe)

    added = {"prices": append_rows("prices", [r for r in twp + tpp if r["code"] in uni])}

    # --- 三大法人 ---
    inst_rows = []
    for d in days:
        m = twse_inst(d)
        if m:
            inst_rows += [{"date": d, "code": c, **v} for c, v in m.items() if c in uni]
            break
    try:
        d2, m2 = tpex_inst_latest()
        if d2:
            inst_rows += [{"date": d2, "code": c, **v} for c, v in m2.items() if c in uni]
    except RuntimeError as e:
        warn.append(f"櫃買法人失敗：{e}")
    if not inst_rows:
        warn.append("三大法人今天抓不到")
    added["inst"] = append_rows("inst", inst_rows)

    # --- 融資融券 ---
    mg_rows = []
    for d in days:
        m = twse_margin(d)
        if m:
            mg_rows += [{"date": d, "code": c, **v} for c, v in m.items() if c in uni]
            break
    try:
        d3, m3 = tpex_margin_latest()
        if d3:
            mg_rows += [{"date": d3, "code": c, **v} for c, v in m3.items() if c in uni]
    except RuntimeError as e:
        warn.append(f"櫃買融資失敗：{e}")
    added["margin"] = append_rows("margin", mg_rows)

    print(f"OK universe={len(universe)} 新增列數={added}")
    if warn:
        print("WARN:", " | ".join(warn))
        with open("store_warn.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(warn))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e)
        sys.exit(1)
