# python fetch_market_inst.py [回補天數]  → market_inst.json
# 大盤（上市）三大法人買賣金額，來源：證交所「三大法人買賣金額統計表」BFI82U（官方）。
# 給消息排程核對新聞裡的法人金額用（2026-10-19 起：新聞卡曾把 3,025 億抄成 302 億）。
# 每次重抓最近 5 個日曆天以外加上沒抓過的日子：證交所事後會修正投信數字，最近幾天要重抓。
# 只放全市場資料，沒有任何個人持股資訊。
import json, os, sys, time, datetime as dt
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "market_inst.json")
URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={d}&type=day&response=json"  # 注意：要用 dayDate，用 date 會回當天
H = {"User-Agent": "Mozilla/5.0"}
KEEP = 60  # 保留最近 60 個交易日


def fetch(day):
    r = requests.get(URL.format(d=day.strftime("%Y%m%d")), headers=H, timeout=30)
    j = r.json()
    if j.get("stat") != "OK" or not j.get("data"):
        return None
    roc = f"{day.year - 1911}年{day.month:02d}月{day.day:02d}日"
    if roc not in j.get("title", ""):
        raise RuntimeError(f"證交所回的日期不對：要 {day}，回 {j.get('title')}")
    v = {row[0].strip(): int(row[3].replace(",", "")) / 1e8 for row in j["data"]}
    f = v.get("外資及陸資(不含外資自營商)", 0) + v.get("外資自營商", 0)
    ds, dh = v.get("自營商(自行買賣)", 0), v.get("自營商(避險)", 0)
    return {"date": day.isoformat(), "foreign": round(f, 2), "trust": round(v.get("投信", 0), 2),
            "dealerSelf": round(ds, 2), "dealerHedge": round(dh, 2), "dealer": round(ds + dh, 2), "total": round(v.get("合計", 0), 2)}


def main():
    back = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    old = {}
    if os.path.exists(OUT):
        old = {x["date"]: x for x in json.load(open(OUT, encoding="utf-8")).get("items", [])}
    today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    got = 0
    for k in range(back):
        day = today - dt.timedelta(days=k)
        if day.weekday() >= 5:
            continue
        if day.isoformat() in old and k > 5:
            continue
        try:
            row = fetch(day)
        except Exception as e:  # 單日失敗不影響其他天
            print(f"{day} 失敗：{e}")
            row = None
        if row:
            old[row["date"]] = row; got += 1
        time.sleep(1.2)
    items = sorted(old.values(), key=lambda x: x["date"], reverse=True)[:KEEP]
    json.dump({"note": "上市三大法人買賣金額（億元，正＝買超、負＝賣超），來源：證交所三大法人買賣金額統計表（BFI82U）；foreign＝外資及陸資＋外資自營商；dealer＝自營商自行買賣＋避險；證交所事後可能修正，最近 5 天每次重抓。最新在前。",
               "updatedAt": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"OK 這次抓到 {got} 天，檔案共 {len(items)} 天，最新 {items[0]['date'] if items else '-'}")


if __name__ == "__main__":
    main()
