# python fetch_dividends.py
# 抓證交所＋櫃買「除權除息預告表」（公開資料，約預告未來 5 週），輸出 dividends.json。
# 不含任何持股資訊；雲端排程再依戰情室的持股篩出相關的。內容沒變就不會產生 commit（不寫時間戳）。
import json, re, time
import requests

H = {"User-Agent": "Mozilla/5.0"}
TWSE = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
TPEX = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"


def get_json(url, tries=5):
    err = None
    for i in range(tries):
        if i:
            time.sleep(4 * i)
        try:
            r = requests.get(url, headers=H, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"{url} 抓取失敗：{err}")


def roc(d):
    d = str(d).strip()
    return f"{int(d[:-4]) + 1911:04d}-{d[-4:-2]}-{d[-2:]}"


def num(x):
    try:
        v = float(str(x).replace(",", "").strip())
    except ValueError:
        return None
    return v


def kind(s):
    s = s.strip()
    return {"息": "息", "權": "權", "權息": "權息", "除息": "息", "除權": "權", "除權息": "權息"}.get(s, s)


def main():
    events = []
    for r in get_json(TWSE):
        events.append({
            "code": r["Code"].strip(), "name": r.get("Name", "").strip(), "market": "上市",
            "exDate": roc(r["Date"]), "kind": kind(r.get("Exdividend", "")),
            "cash": num(r.get("CashDividend")), "stockRatio": num(r.get("StockDividendRatio")),
        })
    for r in get_json(TPEX):
        events.append({
            "code": r["SecuritiesCompanyCode"].strip(), "name": r.get("CompanyName", "").strip(), "market": "上櫃",
            "exDate": roc(r["ExRrightsExDividendDate"]), "kind": kind(r.get("ExRrightsExDividend", "")),
            "cash": num(r.get("CashDividend")), "stockRatio": num(r.get("StockDividendRatio")),
        })
    events = [e for e in events if re.fullmatch(r"\d{4,6}[A-Z]?", e["code"])]
    if len(events) < 20:
        raise RuntimeError(f"除權息預告筆數異常：{len(events)}")
    events.sort(key=lambda e: (e["exDate"], e["code"]))
    with open("dividends.json", "w", encoding="utf-8") as f:
        json.dump({"events": events}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK dividends events={len(events)} range={events[0]['exDate']}~{events[-1]['exDate']}")


if __name__ == "__main__":
    main()
