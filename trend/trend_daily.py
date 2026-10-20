# 趨勢確認系統：產生戰情室「趨勢」分頁的資料（meta/trend）（2026-10-19 交易助手）
# python -I trend_daily.py [輸出檔]   → 預設寫 trend_doc.json（同資料夾），再用 ArtifactData set meta/trend
# 只放市場數據，不放個人部位／損益
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as I

with contextlib.redirect_stdout(io.StringIO()):
    import backtest as B   # 80/20 版的回測：fwd、pull、stats、VERDICT

S, RAW, days, c = B.S, B.RAW, B.days, B.c
d0, d1 = days[-1], days[-2]
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "trend_doc.json")
NAME = {x.key: x for x in I.INDICATORS}


def p1(v):
    return None if v is None else round(v, 1)


def pctv(v, nd=1):
    return f"{v * 100:+.{nd}f}%"


def value_text(k, d):
    r = RAW[k].get(d)
    if r is None or r[0] is None:
        return "—"
    v = r[0]
    if k in ("sox20", "tsm20", "oil20", "credit", "margin20", "adrprem"):
        return pctv(v)
    if k == "tnx20":
        return f"{v:+.2f} 個百分點"
    if k == "twd20":
        return f"台幣{'貶' if v > 0 else '升'} {abs(v) * 100:.1f}%"
    if k == "vix":
        return f"{v:.1f}"
    if k == "ma60":
        return f"{'在季線上' if v >= 0 else '在季線下'} {abs(v) * 100:.1f}%"
    if k == "ma20":
        return f"{'在月線上' if v >= 0 else '在月線下'} {abs(v) * 100:.1f}%"
    if k == "struct":
        return f"高點 {pctv(v[0])}／低點 {pctv(v[1])}"
    if k == "fstreak":
        return "—" if v == 0 else (f"連買 {v} 天" if v > 0 else f"連賣 {-v} 天")
    if k == "fspot20":
        return f"淨買超占 {pctv(v)}"
    if k == "ffut20":
        return f"{'淨多增加' if v > 0 else '淨空增加'} {abs(v):,.0f} 口"
    if k == "retail":
        return f"散戶淨{'多' if v >= 0 else '空'} {abs(v):.1f}%"
    if k == "pcr":
        return f"{v:.1f}"
    return str(v)


def pos(k, d):
    if k == "vix":   # raw 是 (VIX, 狀態)，沒有百分位
        return None
    r = RAW[k].get(d)
    return None if (r is None or len(r) < 2 or r[1] is None) else round(r[1], 1)


def edge_text(k, sub):
    v = B.VERDICT[(k, sub)]
    return "／".join("—" if e is None else f"{e:+.0f}" for _, e in v[2])


pull0 = bool(B.pull.get(d0))
pull1 = bool(B.pull.get(d1))

# ---------- 預測欄 ----------
PRED = [
    dict(key="pcr", stars=3, when="all", sign=1,
         note="最可信：2006 起兩段都有效，回檔時更明顯", edge=f"所有日子 {edge_text('pcr', '所有日子')}；回檔中 {edge_text('pcr', '回檔中')}"),
    dict(key="retail", stars=2, when="all", sign=1,
         note="散戶越偏多，後面途中回檔越深；只有 2019 起資料", edge=f"所有日子 {edge_text('retail', '所有日子')}"),
    dict(key="ma60", stars=1, when="pull", sign=1,
         note="只在回檔中看：還站在往上的季線上＝多頭裡的修正；樣本少", edge=f"回檔中 {edge_text('ma60', '回檔中')}"),
    dict(key="oil20", stars=1, when="pull", sign=-1,
         note="只在回檔中看，方向反著：回檔時油價大跌反而比較差（怕衰退）", edge=f"回檔中 {edge_text('oil20', '回檔中')}（反向）"),
]
pred_rows = []
for p in PRED:
    k = p["key"]
    st0, st1 = S[k].get(d0), S[k].get(d1)
    applies0 = p["when"] == "all" or pull0
    applies1 = p["when"] == "all" or pull1
    eff0 = None if (st0 is None or not applies0) else st0 * p["sign"]
    eff1 = None if (st1 is None or not applies1) else st1 * p["sign"]
    pred_rows.append(dict(key=k, name=NAME[k].name, rule=NAME[k].desc, value=value_text(k, d0), pos=pos(k, d0),
                          raw=st0, state=eff0, prev=eff1, applies=applies0, reversed=p["sign"] < 0,
                          stars=p["stars"], note=p["note"], edge=p["edge"]))


def score_on(d):
    v = [S[k].get(d) for k in ("pcr", "retail")]
    v = [x for x in v if x is not None]
    return sum(v) if v else None


score0, score1 = score_on(d0), score_on(d1)


def sdict(s):
    if not s:
        return None
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}


# 分數對照表（2018～）
ds18 = [d for d in days if d >= "2018-01-01" and score_on(d) is not None]
buckets = []
for lab, f in (("≤−2", lambda s: s <= -2), ("−1", lambda s: s == -1), ("0", lambda s: s == 0),
               ("+1", lambda s: s == 1), ("≥+2", lambda s: s >= 2)):
    st_ = B.stats([d for d in ds18 if f(score_on(d))])
    b = sdict(st_) or {}
    b["label"] = lab
    b["current"] = score0 is not None and f(score0)
    buckets.append(b)
base18 = sdict(B.stats([d for d in days if d >= "2018-01-01"]))

# 類似日子：回檔與否跟今天一樣、Put/Call 與散戶狀態都一樣（2019 起）
sp, sr = S["pcr"].get(d0), S["retail"].get(d0)
cond = lambda d: d != d0 and d >= "2019-01-01" and bool(B.pull.get(d)) == pull0 and S["pcr"].get(d) == sp and S["retail"].get(d) == sr
ads = [d for d in days if cond(d)]
an = sdict(B.stats(ads))
eps, last = [], -999
for d in ads:
    if B.idx[d] - last > 20:
        eps.append(d)
    last = B.idx[d]
ep_rows = [(d, B.fwd[d]) for d in eps if d in B.fwd]
LAB = {1: "轉好", 0: "中性", -1: "轉壞", None: "無資料"}
analog = None
if an:
    best = max(ep_rows, key=lambda x: x[1]["r20"]) if ep_rows else None
    worst = min(ep_rows, key=lambda x: x[1]["m20"]) if ep_rows else None
    base = c[-1]
    analog = dict(an, title=f"{'回檔中' if pull0 else '非回檔'}＋Put/Call {LAB[sp]}＋散戶多空比 {LAB[sr]}", since="2019",
                  mn20Pts=round(base * (1 + an["mn20"] / 100)), mn60Pts=round(base * (1 + an["mn60"] / 100)),
                  best=None if not best else dict(date=best[0], r20=p1(best[1]["r20"] * 100)),
                  worst=None if not worst else dict(date=worst[0], m20=p1(worst[1]["m20"] * 100), r20=p1(worst[1]["r20"] * 100),
                                                    pts=round(base * (1 + worst[1]["m20"]))),
                  episodes=[dict(date=d, r20=p1(f["r20"] * 100), m20=p1(f["m20"] * 100), r60=p1(f["r60"] * 100), m60=p1(f["m60"] * 100))
                            for d, f in ep_rows])

# ---------- 現況欄 ----------
STATUS_KEYS = ["sox20", "tsm20", "adrprem", "credit", "tnx20", "vix", "twd20", "ma20", "struct",
               "fstreak", "fspot20", "ffut20", "margin20"]
LAYER = {"sox20": "美股", "tsm20": "美股", "adrprem": "美股", "credit": "美股", "tnx20": "美股", "vix": "美股",
         "twd20": "匯率", "ma20": "台股價格", "struct": "台股價格", "fstreak": "籌碼", "fspot20": "籌碼", "ffut20": "籌碼", "margin20": "籌碼"}
status_rows = [dict(key=k, layer=LAYER[k], name=NAME[k].name, rule=NAME[k].desc, value=value_text(k, d0), pos=pos(k, d0),
                    state=S[k].get(d0), prev=S[k].get(d1) if S[k].get(d0) is not None else None) for k in STATUS_KEYS]
# 還沒公布的（例如融資晚上才出來）：沒資料就不標「變」
nb = sum(r["state"] == -1 for r in status_rows)
ng = sum(r["state"] == 1 for r in status_rows)

# ---------- 一句話 ----------
if analog and base18:
    tone = ("上漲機率" + ("跟平常差不多" if abs(an["up20"] - base18["up20"]) < 5 else ("比平常低" if an["up20"] < base18["up20"] else "比平常高")) +
            ("，但途中回檔比較深" if an["mn20"] < base18["mn20"] - 0.5 else ("，途中回檔也比較淺" if an["mn20"] > base18["mn20"] + 0.5 else "")))
    pred_line = (f"預測分數 {score0:+d}。歷史上類似的日子（{an['ep']} 段）20 天後上漲 {an['up20']:.0f}%（平常 {base18['up20']:.0f}%），"
                 f"途中最低中位 {an['mn20']:+.1f}%（平常 {base18['mn20']:+.1f}%）→ {tone}。")
else:
    pred_line = f"預測分數 {score0:+d}。"
status_line = f"現況 13 項：{nb} 項轉壞、{ng} 項轉好、{13 - nb - ng} 項中性。這欄只描述現在，回測不能拿來推後面。"

def last_us_date():
    import data
    y = data.yahoo("^SOX")
    return max(d for d in y if d < d0)


doc = dict(
    asOf=d0, prevDay=d1, usSession=last_us_date(),
    updatedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    taiex=round(c[-1], 2), dd60=round((c[-1] / max(c[-60:]) - 1) * 100, 1), pullback=pull0, pullbackPrev=pull1,
    score=score0, scorePrev=score1, predLine=pred_line, statusLine=status_line,
    predict=pred_rows, buckets=buckets, baseline=base18, analog=analog, status=status_rows,
    method="每個指標拿今天的數值跟過去 250 個交易日比，排前 20%／後 20% 才算轉好／轉壞。2005～2017 定規則、2018～今驗證（散戶多空比 2019～2022 定、2023～驗證），兩段都有效才放進預測欄。",
    limits="2018 年後幾乎都是多頭，上漲比例整體偏高；相鄰日子會重疊，真正獨立的只有幾十段；只回測加權指數，沒算微台成本與跳空。",
)
json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("寫好", OUT, "bytes", os.path.getsize(OUT))
print(pred_line); print(status_line)
for r in pred_rows:
    print(" 預測", r["name"], r["value"], r["pos"], LAB[r["state"]], "前一天", LAB[r["prev"]], "適用" if r["applies"] else "不適用")
for r in status_rows:
    print(" 現況", r["layer"], r["name"], r["value"], r["pos"], LAB[r["state"]], "前一天", LAB[r["prev"]])
