# 趨勢確認系統（GitHub 版）每日總控：python trend/run.py   （prices.yml 21:47 那一輪跑）
# 1) 補資料（data/trend/*.csv）→ 2) trend_daily.py 算今天 → 3) 成績單（data/trend/history.csv）→ 4) 寫 trend.json → 5) Discord
# Discord（環境變數 DISCORD_TREND_WEBHOOK）：每個交易日一則摘要；分數變了另外先發一則提醒；同一天重跑不重發（data/trend/notify.json）
# 只有市場數據，不放個人部位／損益
import csv
import json
import os
import runpy
import statistics as st
import sys
from datetime import date

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import data  # noqa: E402

OUT = os.path.join(ROOT, "trend.json")
HIST = os.path.join(data.DDIR, "history.csv")
NOTIFY = os.path.join(data.DDIR, "notify.json")
LAB = {1: "轉好", 0: "中性", -1: "轉壞", None: "無資料"}


def sgn(v):
    return "—" if v is None else (f"+{v}" if v > 0 else str(v))


def score_label(s):
    return "—" if s is None else ("偏弱" if s <= -2 else "略弱" if s == -1 else "中性" if s == 0 else "略強" if s == 1 else "偏強")


def md(d):
    return f"{int(d[5:7])}/{int(d[8:10])}"


def pct(v, nd=1):
    return "—" if v is None else f"{v:+.{nd}f}%"


# ---------- 1) 補資料 ----------
latest = data.update()
print("資料最新日期", latest)

# ---------- 2) 算今天 ----------
tmp = os.path.join(data.DDIR, "_trend_doc.json")
sys.argv = [os.path.join(HERE, "trend_daily.py"), tmp]
runpy.run_path(sys.argv[0], run_name="__main__")
doc = json.load(open(tmp, encoding="utf-8"))
os.remove(tmp)

# ---------- 3) 成績單 ----------
hist = {}
if os.path.exists(HIST):
    with open(HIST, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            hist[r["date"]] = r
pmap = {p["key"]: p for p in doc["predict"]}
hist[doc["asOf"]] = {"date": doc["asOf"], "score": "" if doc["score"] is None else str(doc["score"]), "taiex": f"{doc['taiex']:.2f}",
                     "pcr": LAB[pmap["pcr"]["state"]], "retail": LAB[pmap["retail"]["state"]], "pullback": "1" if doc["pullback"] else "0"}
with open(HIST, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["date", "score", "taiex", "pcr", "retail", "pullback"], lineterminator="\n")
    w.writeheader()
    for d in sorted(hist):
        w.writerow(hist[d])

px = data.taiex()
days = sorted(px)
idx = {d: i for i, d in enumerate(days)}
rows = []
for d in sorted(hist, reverse=True):
    if d not in idx or hist[d]["score"] == "":
        continue
    i, c0 = idx[d], px[d]["c"]
    n = min(20, len(days) - 1 - i)
    ret = (px[days[i + n]]["c"] / c0 - 1) * 100 if n > 0 else None
    mn = (min(px[x]["l"] for x in days[i + 1:i + n + 1]) / c0 - 1) * 100 if n > 0 else None
    rows.append(dict(date=d, score=int(hist[d]["score"]), taiex=c0, days=n, done=n >= 20,
                     ret=None if ret is None else round(ret, 2), min=None if mn is None else round(mn, 2)))
summary = []
for s in sorted({r["score"] for r in rows}):
    xs = [r for r in rows if r["score"] == s and r["done"]]
    summary.append(dict(score=s, n=len(xs), pending=sum(1 for r in rows if r["score"] == s and not r["done"]),
                        up=round(sum(r["ret"] > 0 for r in xs) / len(xs) * 100) if xs else None,
                        mdRet=round(st.median(r["ret"] for r in xs), 1) if xs else None,
                        mdMin=round(st.median(r["min"] for r in xs), 1) if xs else None))
doc["scorecard"] = dict(since=min(hist), rows=rows[:60], summary=summary,
                        note="每天收盤記一筆，滿 20 個交易日補上實際結果；途中最低用加權日內最低價。樣本少的時候看看就好。")

# ---------- 4) 寫 trend.json ----------
json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("寫好 trend.json", os.path.getsize(OUT), "bytes；分數", doc["score"], "前一天", doc["scorePrev"])

# ---------- 5) Discord ----------
hook = os.environ.get("DISCORD_TREND_WEBHOOK", "")
state = json.load(open(NOTIFY, encoding="utf-8")) if os.path.exists(NOTIFY) else {}
today = data.TODAY
msgs = []


def changed(rows_):
    return [f"{r['name']} {LAB[r['prev']]}→{LAB[r['state']]}" for r in rows_
            if r.get("applies", True) and r["prev"] is not None and r["state"] is not None and r["prev"] != r["state"]]


def plain(doc_):
    """放在 Discord 最上面的白話結論（兩句）：分數與變化、歷史上像今天這樣後面通常怎麼走"""
    s, sp = doc_["score"], doc_["scorePrev"]
    head = f"分數 {sgn(s)}（{score_label(s)}）"
    if sp is not None and s is not None and s != sp:
        why = [f"{r['name'].split('（')[0]}變{LAB[r['state']]}" for r in doc_["predict"]
               if r.get("applies", True) and r["prev"] is not None and r["state"] is not None and r["prev"] != r["state"]]
        head += f"，比前一天{'好' if s > sp else '差'}一點" + (f"（{'、'.join(why)}）" if why else "")
    a, b = doc_.get("analog"), doc_.get("baseline")
    if not (a and b):
        return f"👉 白話：{head}。"
    du = a["up20"] - b["up20"]
    up = "跟平常差不多" if abs(du) < 5 else ("比平常低" if du < 0 else "比平常高")
    dm = a["mn20"] - b["mn20"]
    risk = (f"但中途比較容易先跌一段，大約跌到 {round(a['mn20Pts'], -2):,} 附近" if dm < -0.5
            else "中途的回檔通常比平常淺" if dm > 0.5 else "中途的回檔跟平常差不多")
    return f"👉 白話：{head}。歷史上像今天這樣，20 天後上漲的機率{up}，{risk}。"


if doc["asOf"] == today:
    if state.get("daily") != today:
        pc = changed(doc["predict"])
        if doc["score"] != doc["scorePrev"] and state.get("change") != today:
            msgs.append(f"⚠️ **趨勢分數變了：{sgn(doc['scorePrev'])} → {sgn(doc['score'])}（{score_label(doc['score'])}）**　{md(today)} 收盤\n"
                        f"{plain(doc)}\n原因：{'、'.join(pc) if pc else '預測指標狀態改變'}")
            state["change"] = today
        a, b = doc.get("analog"), doc.get("baseline")
        lines = [plain(doc), "",
                 f"📊 **趨勢 {md(today)} 收盤**｜加權 {doc['taiex']:,.0f}（離 60 天高點 {pct(doc['dd60'])}，{'回檔中' if doc['pullback'] else '非回檔'}）",
                 f"預測分數 **{sgn(doc['score'])}**（{score_label(doc['score'])}）" + ("" if doc["score"] == doc["scorePrev"] else f"，前一天 {sgn(doc['scorePrev'])}")]
        for p in doc["predict"]:
            if not p["applies"]:
                continue
            pos = "" if p["pos"] is None else f"，一年位置 {p['pos']:.1f}" if (p["pos"] < 1 or p["pos"] > 99) else f"，一年位置 {p['pos']:.0f}"
            chg = "" if p["prev"] == p["state"] or p["prev"] is None else f" ▲變（前一天 {LAB[p['prev']]}）"
            lines.append(f"• {p['name']}：{p['value']}{pos} → {LAB[p['state']]}{chg}")
        if a and b:
            lines.append(f"類似日子（{a['ep']} 段）：20 天上漲 {a['up20']:.0f}%（平常 {b['up20']:.0f}%），途中最低中位 {pct(a['mn20'])}（約 {a['mn20Pts']:,}）")
        sc = changed(doc["status"])
        nb = sum(r["state"] == -1 for r in doc["status"]); ng = sum(r["state"] == 1 for r in doc["status"])
        lines.append(f"現況 13 項：{nb} 轉壞、{ng} 轉好、{13 - nb - ng} 中性／無資料" + (f"；今天變動：{'、'.join(sc)}" if sc else ""))
        lines.append("只有市場數據，不是買賣建議。")
        msgs.append("\n".join(lines))
        state["daily"] = today
elif date.fromisoformat(today).weekday() < 5 and state.get("nodata") != today:
    msgs.append(f"ℹ️ 趨勢：今天（{md(today)}）沒有新的加權收盤資料（休市或資料延遲），戰情室停在 {md(doc['asOf'])}。")
    state["nodata"] = today

for m in msgs:
    if not hook:
        print("（沒有 DISCORD_TREND_WEBHOOK，只印出）\n" + m)
        continue
    r = requests.post(hook, json={"content": m[:1900]}, timeout=30)
    print("Discord", r.status_code)
    r.raise_for_status()
if hook:  # 本機沒設網址的測試不記「已發過」，免得 GitHub 那一輪以為發過了
    json.dump(state, open(NOTIFY, "w", encoding="utf-8"), ensure_ascii=False)
