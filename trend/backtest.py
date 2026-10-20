# 趨勢確認系統第一階段回測（2026-10-19 交易助手）
# python -I backtest.py > 結果.txt
# 規則（看結果前寫死）：
#   每個指標分「轉好／中性／轉壞」，看之後 20、60 天加權：上漲比例、中位數、途中最低（最低價÷起點−1）
#   有效＝「轉好的上漲比例 − 轉壞的上漲比例」≥ 5 個百分點，且轉好的 20 天中位數 > 轉壞的；
#        兩邊各要 ≥40 天、≥3 段（相隔 >20 個交易日算不同段）
#   反向＝差距 ≤ −5 且中位數也反過來
#   定規則段 2005～2017、驗證段 2018～；籌碼裡 2018 才有的（外資期貨、散戶多空比）用 2018～2022 定、2023～ 驗證
#   兩段都「有效」才留；兩段都「反向」標出來（方向跟原本想的相反，但穩定）
#   情況分兩種：所有日子、回檔中（收盤離 60 天收盤高點 ≥5%）
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indicators as I

EDGE = 5.0
MIN_N, MIN_EP = 40, 3

days, px, S, RAW = I.build()
c = [px[d]["c"] for d in days]
l = [px[d]["l"] if px[d]["l"] and px[d]["l"] <= px[d]["c"] * 1.0001 else px[d]["c"] for d in days]
N = len(days)
idx = {d: i for i, d in enumerate(days)}

fwd = {}
for i, d in enumerate(days):
    if i + 60 < N:
        fwd[d] = dict(r20=c[i + 20] / c[i] - 1, r60=c[i + 60] / c[i] - 1,
                      m20=min(l[i + 1:i + 21]) / c[i] - 1, m60=min(l[i + 1:i + 61]) / c[i] - 1)
pull = {}
for i, d in enumerate(days):
    if i >= 60:
        pull[d] = c[i] / max(c[i - 59:i + 1]) - 1 <= -0.05

PERIODS = {
    "2005": (("定規則", "2005-01-01", "2017-12-31"), ("驗證", "2018-01-01", "2099")),
    "2018": (("定規則", "2018-01-01", "2022-12-31"), ("驗證", "2023-01-01", "2099")),
}
SUBSETS = (("所有日子", lambda d: True), ("回檔中", lambda d: pull.get(d, False)))


def episodes(ds):
    n, last = 0, -999
    for d in ds:
        if idx[d] - last > 20:
            n += 1
        last = idx[d]
    return n


def stats(ds):
    ds = [d for d in ds if d in fwd]
    if not ds:
        return None
    f = [fwd[d] for d in ds]
    return dict(n=len(ds), ep=episodes(ds),
                up20=sum(x["r20"] > 0 for x in f) / len(f) * 100, md20=st.median(x["r20"] for x in f) * 100,
                up60=sum(x["r60"] > 0 for x in f) / len(f) * 100, md60=st.median(x["r60"] for x in f) * 100,
                mn20=st.median(x["m20"] for x in f) * 100, mn60=st.median(x["m60"] for x in f) * 100)


def fmt(s):
    if not s:
        return "（沒有樣本）"
    return (f"{s['n']:5d}天/{s['ep']:3d}段 | 20天 上漲{s['up20']:3.0f}% 中位{s['md20']:+5.1f}% 途中最低{s['mn20']:+5.1f}% | "
            f"60天 上漲{s['up60']:3.0f}% 中位{s['md60']:+5.1f}% 途中最低{s['mn60']:+5.1f}%")


def judge(g, b):
    if not g or not b or min(g["n"], b["n"]) < MIN_N or min(g["ep"], b["ep"]) < MIN_EP:
        return "樣本不足", None
    e = g["up20"] - b["up20"]
    m = g["md20"] - b["md20"]
    if e >= EDGE and m > 0:
        return "有效", e
    if e <= -EDGE and m < 0:
        return "反向", e
    return "無效", e


VERDICT = {}   # (key, subset) → (總評, [各段評])
print("說明：每列＝之後 20／60 天加權的表現；『途中最低』＝期間最低價相對起點的中位數；n 天/段＝樣本天數/獨立段數")
print("     門檻：轉好上漲比例要比轉壞高 ≥5 個百分點，且 20 天中位數也比較高；兩段都過才算有效\n")
for x in I.INDICATORS:
    print("=" * 100)
    print(f"【{x.layer}】{x.name}：{x.desc}")
    for sname, sf in SUBSETS:
        res = []
        for pname, a, b in PERIODS[x.start_train]:
            ds = [d for d in days if a <= d <= b and sf(d) and S[x.key].get(d) is not None]
            if not ds:
                res.append(("樣本不足", None)); continue
            base = stats(ds)
            g = stats([d for d in ds if S[x.key][d] == 1])
            nu = stats([d for d in ds if S[x.key][d] == 0])
            bd = stats([d for d in ds if S[x.key][d] == -1])
            v, e = judge(g, bd)
            res.append((v, e))
            print(f"  {sname}・{pname}（{ds[0][:4]}～{ds[-1][:4]}）→ {v}" + (f"（差 {e:+.1f} 個百分點）" if e is not None else ""))
            print(f"     對照 {fmt(base)}")
            print(f"     轉好 {fmt(g)}")
            print(f"     中性 {fmt(nu)}")
            print(f"     轉壞 {fmt(bd)}")
        vs = [r[0] for r in res]
        tot = "有效" if vs == ["有效", "有效"] else ("反向（穩定）" if vs == ["反向", "反向"] else "不留")
        VERDICT[(x.key, sname)] = (tot, vs, res)
        print(f"  ▶ {sname}：{tot}（定規則段 {vs[0]}、驗證段 {vs[1]}）")

print("\n" + "=" * 100)
print("總表")
for x in I.INDICATORS:
    a = VERDICT[(x.key, "所有日子")]; p = VERDICT[(x.key, "回檔中")]
    f = lambda r: "/".join(f"{v}{'' if e is None else f'({e:+.0f})'}" for v, e in r[2])
    print(f"  {x.name:26s} 所有日子：{a[0]:8s} [{f(a)}]   回檔中：{p[0]:8s} [{f(p)}]")


# ---------- 合成分數 ----------
def composite(keys_signs, ds):
    out = {}
    for d in ds:
        vals = [S[k][d] * sg for k, sg in keys_signs if S[k].get(d) is not None]
        out[d] = sum(vals) if vals else None
    return out


def bucket_report(title, keys_signs, a, b, sf=lambda d: True):
    ds = [d for d in days if a <= d <= b and sf(d)]
    sc = composite(keys_signs, ds)
    ds = [d for d in ds if sc[d] is not None]
    print(f"\n{title}（{a[:4]}～{b[:4] if b < '2090' else '今'}，{len(keys_signs)} 個指標）")
    print(f"   對照 {fmt(stats(ds))}")
    vals = sorted(set(sc[d] for d in ds))
    groups = []
    lo, hi = -2, 2
    groups.append((f"≤{lo}", [d for d in ds if sc[d] <= lo]))
    for v in range(lo + 1, hi):
        groups.append((f"{v:+d}", [d for d in ds if sc[d] == v]))
    groups.append((f"≥{hi:+d}", [d for d in ds if sc[d] >= hi]))
    for lab, g in groups:
        print(f"   分數 {lab:>4} {fmt(stats(g))}")
    return sc


print("\n" + "=" * 100)
print("合成分數（分數＝留下來的指標 +1/−1 加總；檢查分數越高後面是不是越好）")
# A. 誠實版：只用 2005～2017 定規則段就判有效的指標，拿去 2018～ 驗證（完全沒看過驗證段）
train_ok = []
for x in I.INDICATORS:
    if x.start_train != "2005":
        continue
    v = VERDICT[(x.key, "所有日子")][1][0]
    if v == "有效":
        train_ok.append((x.key, 1))
    elif v == "反向":
        train_ok.append((x.key, -1))
print("A 誠實版指標（只看定規則段選出來）：", [(k, s) for k, s in train_ok])
bucket_report("A 定規則段", train_ok, "2005-01-01", "2017-12-31")
bucket_report("A 驗證段（沒看過）", train_ok, "2018-01-01", "2099")
bucket_report("A 驗證段・回檔中", train_ok, "2018-01-01", "2099", lambda d: pull.get(d, False))

# B. 最終版：兩段都有效（或都穩定反向）的
final = [(x.key, 1 if VERDICT[(x.key, "所有日子")][0] == "有效" else -1) for x in I.INDICATORS
         if VERDICT[(x.key, "所有日子")][0] != "不留"]
print("\nB 最終版指標（兩段都過）：", final)
if final:
    bucket_report("B 2018～（部分是選指標用過的資料）", final, "2018-01-01", "2099")
    bucket_report("B 2023～", final, "2023-01-01", "2099")

# ---------- 今天 ----------
d = days[-1]
print("\n" + "=" * 100)
print(f"今天 {d} 加權 {c[-1]:,.0f}；離 60 天高點 {c[-1] / max(c[-60:]) * 100 - 100:+.1f}%（{'回檔中' if pull.get(d) else '非回檔'}）")
lab = {1: "轉好", 0: "中性", -1: "轉壞", None: "無資料"}
for x in I.INDICATORS:
    v = VERDICT[(x.key, "所有日子")][0]
    print(f"  {x.name:26s} {lab[S[x.key][d]]:4s}  回測：{v}")
for name, ks in (("A", train_ok), ("B", final)):
    vals = [S[k][d] * s for k, s in ks if S[k].get(d) is not None]
    print(f"  合成分數 {name}：{sum(vals):+d}（{len(vals)} 個指標有值）")
