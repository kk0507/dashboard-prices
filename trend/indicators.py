# 趨勢確認系統：每天算 17 個指標的狀態（+1 轉好／0 中性／−1 轉壞）（2026-10-19 交易助手）
# 規則在看回測結果之前寫死，之後不准為了結果改（改了就要重新跑前後段驗證並記在 WORKING_MEMORY）
# 門檻一律用「過去 250 個交易日的相對位置」，只用當天以前的資料，不偷看
# 時間對齊：台股日 d 收盤時，美股只看得到「美國日期 < d」的收盤
import bisect
import os
import sys
from collections import namedtuple
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data

P_HI, P_LO = 80, 20     # 相對位置門檻（百分位）
WIN = 250

Ind = namedtuple("Ind", "key name layer start_train desc")
INDICATORS = [
    Ind("sox20", "費半 20 天漲跌", "美股", "2005", "前 20% 強＝轉好；後 20% 弱＝轉壞"),
    Ind("tsm20", "台積電 ADR 20 天漲跌", "美股", "2005", "前 20% 強＝轉好；後 20% 弱＝轉壞"),
    Ind("adrprem", "台積電 ADR 溢價", "美股", "2005", "溢價在 250 天前 20% 高＝轉好；後 20% 低＝轉壞"),
    Ind("credit", "信用（HYG／LQD）20 天變化", "美股", "2005", "高收益債相對變強前 20%＝轉好；後 20%＝轉壞（2008 起才有）"),
    Ind("tnx20", "美 10 年期殖利率 20 天變化", "美股", "2005", "升幅前 20%＝轉壞；降幅前 20%＝轉好"),
    Ind("vix", "VIX 恐慌回落", "美股", "2005", "10 天內高點在 250 天前 20%，且已從高點回落 ≥20%＝轉好；在高檔且還在 10 天高點附近（≥95%）＝轉壞"),
    Ind("oil20", "油價 20 天變化", "美股", "2005", "漲幅前 20%＝轉壞；跌幅前 20%＝轉好"),
    Ind("twd20", "台幣 20 天變化", "台股價格", "2005", "台幣升值前 20%＝轉好；貶值前 20%＝轉壞"),
    Ind("ma60", "加權 vs 季線", "台股價格", "2005", "收盤在季線上且季線上彎＝轉好；在下且下彎＝轉壞"),
    Ind("ma20", "加權 vs 月線", "台股價格", "2005", "收盤在月線上且月線上彎＝轉好；在下且下彎＝轉壞"),
    Ind("struct", "高低點結構", "台股價格", "2005", "近 20 天高點、低點都比前 20 天高＝轉好；都比較低＝轉壞"),
    Ind("fstreak", "外資現貨連買連賣", "籌碼", "2005", "連買 ≥5 天＝轉好；連賣 ≥5 天＝轉壞"),
    Ind("fspot20", "外資現貨 20 天累計", "籌碼", "2005", "20 天淨買超÷買賣總額，前 20%＝轉好；後 20%＝轉壞"),
    Ind("ffut20", "外資期貨淨部位 20 天變化", "籌碼", "2018", "淨多增加前 20%＝轉好；淨空增加前 20%＝轉壞（2018 起）"),
    Ind("retail", "散戶多空比（小台＋微台）", "籌碼", "2018", "散戶偏多前 20%＝轉壞；偏空前 20%＝轉好（2018 起）"),
    Ind("margin20", "融資餘額 20 天變化", "籌碼", "2005", "增加前 20%＝轉壞；減少前 20%＝轉好"),
    Ind("pcr", "Put/Call 未平倉比", "籌碼", "2005", "前 20% 高＝轉好；後 20% 低＝轉壞"),
]


def pct_rank(vals):
    """vals: list（可含 None）；回傳每點在前 WIN 個有效值裡的百分位（嚴格小於的比例×100），不足 WIN 回 None"""
    out = [None] * len(vals)
    win = []          # 依時間的有效值
    srt = []
    for i, v in enumerate(vals):
        if v is None:
            continue
        if len(win) >= WIN:
            out[i] = bisect.bisect_left(srt, v) / WIN * 100
            old = win.pop(0)
            del srt[bisect.bisect_left(srt, old)]
        win.append(v)
        bisect.insort(srt, v)
    return out


def chg(series, n, mode="ret"):
    """series: [(date, value)]；回傳 {date: n 期變化}"""
    out = {}
    for i in range(n, len(series)):
        a, b = series[i - n][1], series[i][1]
        if a is None or b is None:
            continue
        if mode == "ret":
            if a > 0 and b > 0:
                out[series[i][0]] = b / a - 1
        else:
            out[series[i][0]] = b - a
    return out


def ranked(d):
    """{date: value} → {date: (value, pct)}，以該序列自己的日子算 250 天位置"""
    ks = sorted(d)
    p = pct_rank([d[k] for k in ks])
    return {k: (d[k], p[i]) for i, k in enumerate(ks) if p[i] is not None}


def asof(d, day, strict):
    """d: {date: x}，取 date < day（strict）或 <= day 的最後一筆"""
    ks = asof.cache.get(id(d))
    if ks is None:
        ks = asof.cache[id(d)] = sorted(d)
    i = (bisect.bisect_left(ks, day) if strict else bisect.bisect_right(ks, day)) - 1
    if i < 0:
        return None
    if (date.fromisoformat(day) - date.fromisoformat(ks[i])).days > 7:  # 太舊視為沒資料
        return None
    return d[ks[i]]


asof.cache = {}


def hilo(r, hi_good=True):
    if r is None or r[1] is None:
        return None
    p = r[1]
    s = 1 if p >= P_HI else (-1 if p < P_LO else 0)
    return s if hi_good else -s


def build():
    """回傳 (days, px, states, raw)：days＝台股交易日；px[d]={c,h,l}；states[key][d]∈{1,0,-1,None}；raw[key][d]＝原始值與位置"""
    asof.cache.clear()  # 用 id() 當鍵，重建時舊 dict 的 id 可能被重用
    px = data.taiex()
    days = sorted(px)
    Y = {k: data.yahoo(s) for k, s in {**data.US, **data.TW}.items()}
    ser = {k: sorted((d, v["c"]) for d, v in Y[k].items()) for k in Y}

    # --- 美股（美國日期序列上算，再對齊）---
    R = {}
    R["sox20"] = ranked(chg(ser["sox"], 20))
    R["tsm20"] = ranked(chg(ser["tsm"], 20))
    # ADR 溢價：TSM×匯率÷(5×2330)−1；用美國日期 t、2330 取台股日期 ≤ t、匯率取 ≤ t
    tw = {d: v["c"] for d, v in Y["tw2330"].items()}
    fx = {d: v["c"] for d, v in Y["usdtwd"].items() if 20 < v["c"] < 45}
    prem = {}
    for d, c in ser["tsm"]:
        a, f = asof(tw, d, False), asof(fx, d, False)
        if a and f:
            prem[d] = c * f / (5 * a) - 1
    R["adrprem"] = ranked(prem)
    hyg = dict(ser["hyg"]); lqd = dict(ser["lqd"])
    ratio = sorted((d, hyg[d] / lqd[d]) for d in hyg if d in lqd and lqd[d])
    R["credit"] = ranked(chg(ratio, 20))
    R["tnx20"] = ranked(chg(ser["tnx"], 20, "diff"))
    R["oil20"] = ranked(chg([(d, v if v and v > 0 else None) for d, v in ser["oil"]], 20))
    R["twd20"] = ranked(chg([(d, v if v and 20 < v < 45 else None) for d, v in ser["usdtwd"]], 20))
    # VIX
    vx = ser["vix"]
    vlev = ranked({d: v for d, v in vx})
    vixs = {}
    for i in range(10, len(vx)):
        d, v = vx[i]
        m10 = max(x[1] for x in vx[i - 9:i + 1])
        dm = vx[max(range(i - 9, i + 1), key=lambda j: vx[j][1])][0]
        pm = vlev.get(dm)
        if pm is None:
            continue
        if pm[1] >= P_HI and v <= 0.8 * m10:
            s = 1
        elif pm[1] >= P_HI and v >= 0.95 * m10:
            s = -1
        else:
            s = 0
        vixs[d] = (v, s)

    # --- 台股價格 ---
    c = [px[d]["c"] for d in days]
    h = [px[d]["h"] for d in days]
    l = [px[d]["l"] for d in days]

    def ma(i, n):
        return sum(c[i - n + 1:i + 1]) / n

    # --- 籌碼 ---
    net, gross = data.foreign_spot()
    _, mmoney = data.margin_balance()
    ffut, retail = data.futures_inst()
    pcr = {d: v["oi"] for d, v in data.put_call().items()}
    # 外資 20 天累計（台股日序列）
    fd = [d for d in days if d in net]
    f20 = {}
    for i in range(19, len(fd)):
        g = sum(gross[x] for x in fd[i - 19:i + 1])
        if g:
            f20[fd[i]] = sum(net[x] for x in fd[i - 19:i + 1]) / g
    R["fspot20"] = ranked(f20)
    streak = {}
    k = 0
    for d in fd:
        v = net[d]
        k = (k + 1 if k > 0 else 1) if v > 0 else ((k - 1 if k < 0 else -1) if v < 0 else 0)
        streak[d] = k
    R["ffut20"] = ranked(chg(sorted(ffut.items()), 20, "diff"))
    R["retail"] = ranked(retail)
    R["margin20"] = ranked(chg(sorted(mmoney.items()), 20))
    R["pcr"] = ranked(pcr)

    states = {x.key: {} for x in INDICATORS}
    raw = {x.key: {} for x in INDICATORS}
    US_KEYS = {"sox20", "tsm20", "adrprem", "credit", "tnx20", "oil20", "twd20"}
    HI_GOOD = {"sox20": True, "tsm20": True, "adrprem": True, "credit": True, "tnx20": False, "oil20": False,
               "twd20": False, "fspot20": True, "ffut20": True, "retail": False, "margin20": False, "pcr": True}
    for i, d in enumerate(days):
        for key in HI_GOOD:
            r = asof(R[key], d, strict=True) if key in US_KEYS else R[key].get(d)
            states[key][d] = hilo(r, HI_GOOD[key])
            raw[key][d] = r
        v = asof(vixs, d, strict=True)
        states["vix"][d] = v[1] if v else None
        raw["vix"][d] = v
        if i >= 65:
            for key, n in (("ma60", 60), ("ma20", 20)):
                m, m5 = ma(i, n), ma(i - 5, n)
                states[key][d] = 1 if c[i] > m and m > m5 else (-1 if c[i] < m and m < m5 else 0)
                raw[key][d] = (c[i] / m - 1, None)
            H1, H0 = max(h[i - 19:i + 1]), max(h[i - 39:i - 19])
            L1, L0 = min(l[i - 19:i + 1]), min(l[i - 39:i - 19])
            states["struct"][d] = 1 if H1 > H0 and L1 > L0 else (-1 if H1 < H0 and L1 < L0 else 0)
            raw["struct"][d] = ((H1 / H0 - 1, L1 / L0 - 1), None)
        else:
            for key in ("ma60", "ma20", "struct"):
                states[key][d] = None
        s = streak.get(d)
        states["fstreak"][d] = None if s is None else (1 if s >= 5 else (-1 if s <= -5 else 0))
        raw["fstreak"][d] = (s, None)
    return days, px, states, raw


if __name__ == "__main__":
    days, px, st_, raw = build()
    d = days[-1]
    print("最新台股日", d, "加權", px[d]["c"])
    for x in INDICATORS:
        n = sum(1 for v in st_[x.key].values() if v is not None)
        first = next((dd for dd in days if st_[x.key][dd] is not None), None)
        print(f"{x.name:24s} 狀態 {st_[x.key][d]!s:>4}  原始 {raw[x.key][d]}  有資料 {n} 天 從 {first}")
