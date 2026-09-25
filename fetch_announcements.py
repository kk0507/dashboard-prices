# python fetch_announcements.py
# 官方公告：證交所／櫃買的「每日重大訊息」「處置股」「注意股」（官方公開 API，原文照錄，只裁短過長的說明）。
# 官方 API 每次只給最新一天，所以每天累積到 data/announcements_store.json（保留 21 天），輸出 announcements.json。
# 重大訊息只留成交值前 300 檔股票池（含 KK 的持股但不標示是誰的）；處置股與注意股保留全市場（量小）。
import datetime as dt
import hashlib
import json
import re
import sys

from store_lib import *  # noqa: F401,F403

KEEP_DAYS = 21
MAX_DETAIL = 500
SRC_MOPS = {"label": "公開資訊觀測站－每日重大訊息（證交所開放資料）", "url": "https://mops.twse.com.tw/"}
SRC_MOPS_O = {"label": "公開資訊觀測站－每日重大訊息（櫃買開放資料）", "url": "https://mops.twse.com.tw/"}
SRC_PUNISH = {"label": "證交所－公布處置股票", "url": "https://www.twse.com.tw/zh/announcement/punish.html"}
SRC_PUNISH_O = {"label": "櫃買中心－處置有價證券資訊", "url": "https://www.tpex.org.tw/"}
SRC_NOTICE = {"label": "證交所－公布注意股票", "url": "https://www.twse.com.tw/zh/announcement/notice.html"}
SRC_NOTICE_O = {"label": "櫃買中心－公布注意股票資訊", "url": "https://www.tpex.org.tw/"}

CATS = [
    ("法說會", r"法人說明會|法說會"),
    ("籌資", r"現金增資|私募|公司債|可轉換|海外存託憑證|GDR|募集|增資發行"),
    ("股利", r"股利|盈餘分派|除息|除權|盈餘轉增資"),
    ("資本支出", r"取得.{0,12}(設備|不動產|廠房|土地)|資本支出|購置|處分.{0,8}(不動產|廠房)"),
    ("股權異動", r"質押|設質|減持|轉讓|申報|庫藏股|持股"),
    ("澄清", r"澄清|媒體報導"),
    ("董事會", r"董事會"),
    ("營運財務", r"營收|財務報告|自結|損益|盈餘"),
]


def category(title):
    for name, pat in CATS:
        if re.search(pat, title):
            return name
    return "其他"


def hhmmss(t):
    t = str(t).strip().zfill(6)
    return f"{t[0:2]}:{t[2:4]}:{t[4:6]}"


def clean(s, n=MAX_DETAIL):
    s = re.sub(r"[\r\t]+", " ", str(s or "")).strip()
    s = re.sub(r"\n{2,}", "\n", s)
    return s if len(s) <= n else s[:n] + "…（原文較長，以公開資訊觀測站為準）"


def make(kind, code, name, date, time_, title, src, detail="", **extra):
    key = f"{kind}|{code}|{date}|{time_}|{title[:60]}"
    it = {"id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "type": kind, "code": code, "name": name, "date": date, "time": time_,
          "title": clean(title, 200), "detail": clean(detail), "source": src["label"], "sourceUrl": src["url"], "sourceType": "官方"}
    it.update(extra)
    return it


def main():
    uni = set(load_universe().get("codes", []))
    got, warn = [], []

    def try_(name, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            warn.append(f"{name} 失敗：{e}")
            return []

    for r in try_("證交所重大訊息", lambda: http("https://openapi.twse.com.tw/v1/opendata/t187ap04_L")):
        code = str(r.get("公司代號", "")).strip()
        if code in uni:
            title = r.get("主旨 ") or r.get("主旨") or ""
            got.append(make("重大訊息", code, r.get("公司名稱", "").strip(), roc(r["發言日期"]), hhmmss(r.get("發言時間", "0")), title, SRC_MOPS,
                            r.get("說明", ""), clause=str(r.get("符合條款", "")).strip(), category=category(title)))
    for r in try_("櫃買重大訊息", lambda: http("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O")):
        code = str(r.get("SecuritiesCompanyCode", "")).strip()
        if code in uni:
            title = r.get("主旨") or r.get("主旨 ") or ""
            got.append(make("重大訊息", code, r.get("CompanyName", "").strip(), roc(r["發言日期"]), hhmmss(r.get("發言時間", "0")), title, SRC_MOPS_O,
                            r.get("說明", ""), clause=str(r.get("符合條款", "")).strip(), category=category(title)))

    today = dt.datetime.now(TW).strftime("%Y-%m-%d")
    for r in try_("證交所處置股", lambda: http("https://openapi.twse.com.tw/v1/announcement/punish")):
        if r.get("Code"):
            per = str(r.get("DispositionPeriod", ""))
            end = re.findall(r"(\d{3})/(\d{2})/(\d{2})", per)
            end_d = f"{int(end[-1][0]) + 1911}-{end[-1][1]}-{end[-1][2]}" if end else ""
            got.append(make("處置股", r["Code"].strip(), r.get("Name", "").strip(), roc(r["Date"]), "00:00:00",
                            f"列入處置股票（{r.get('DispositionMeasures', '')}）：{r.get('ReasonsOfDisposition', '')}，期間 {per}", SRC_PUNISH,
                            r.get("Detail", ""), period=per, periodEnd=end_d, category="處置"))
    for r in try_("櫃買處置股", lambda: http("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information")):
        if r.get("SecuritiesCompanyCode"):
            per = str(r.get("DispositionPeriod", ""))
            m = re.findall(r"(\d{7})", per)
            end_d = roc(m[-1]) if m else ""
            got.append(make("處置股", r["SecuritiesCompanyCode"].strip(), r.get("CompanyName", "").strip(), roc(r["Date"]), "00:00:00",
                            f"列入處置有價證券：{r.get('DispositionReasons', '')}，期間 {per}", SRC_PUNISH_O,
                            r.get("DisposalCondition", ""), period=per, periodEnd=end_d, category="處置"))
    for r in try_("證交所注意股", lambda: http("https://openapi.twse.com.tw/v1/announcement/notice")):
        if r.get("Code"):
            got.append(make("注意股", r["Code"].strip(), r.get("Name", "").strip(), roc(r["Date"]), "00:00:00", "列入注意交易資訊", SRC_NOTICE,
                            r.get("TradingInfoForAttention", ""), category="注意"))
    for r in try_("櫃買注意股", lambda: http("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information")):
        if r.get("SecuritiesCompanyCode"):
            got.append(make("注意股", r["SecuritiesCompanyCode"].strip(), r.get("CompanyName", "").strip(), roc(r["Date"]), "00:00:00", "列入注意交易資訊",
                            SRC_NOTICE_O, r.get("TradingInformation", ""), category="注意"))

    if not got and warn:
        raise RuntimeError("；".join(warn))

    p = os.path.join(DATA, "announcements_store.json")
    try:
        store = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        store = {}
    new = 0
    for it in got:
        if it["id"] not in store:
            new += 1
        store[it["id"]] = it
    cutoff = (dt.datetime.now(TW).date() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    store = {k: v for k, v in store.items() if v["date"] >= cutoff or (v.get("periodEnd") or "") >= today}
    os.makedirs(DATA, exist_ok=True)
    json.dump(store, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    items = sorted(store.values(), key=lambda v: (v["date"], v["time"]), reverse=True)
    active = [v for v in items if v["type"] == "處置股" and (v.get("periodEnd") or "9999") >= today]
    out = {"note": "官方公開資料原文（證交所、櫃買中心、公開資訊觀測站開放資料）；重大訊息僅含成交值前約300檔股票池；處置股、注意股為全市場。不是買賣建議。",
           "dates": {"latest": items[0]["date"] if items else "", "keptDays": KEEP_DAYS},
           "activeDispositions": [{"code": v["code"], "name": v["name"], "period": v.get("period", ""), "periodEnd": v.get("periodEnd", "")} for v in active],
           "items": items}
    json.dump(out, open("announcements.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"OK announcements 新增={new} 累計={len(items)} 生效中處置={len(active)}")
    if warn:
        print("WARN:", " | ".join(warn))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("FAIL:", e)
        sys.exit(1)
