"""일회성 진단: 네이버 가격 출처별로 KRX 정규장 종가와 넥스트레이드 통합가가 어떻게 다른지 확인"""
import json, re, requests
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}
out = []
def get(u):
    try:
        r = requests.get(u, headers=UA, timeout=15)
        t = r.content.decode("euc-kr", errors="replace") if "fchart" in u else r.text
        out.append(f"\n===== {r.status_code} {u}\n{t[:1500]}")
    except Exception as e:
        out.append(f"\n===== ERR {u}\n{e}")
for c in ["166480", "190510"]:
    get(f"https://fchart.stock.naver.com/sise.nhn?symbol={c}&timeframe=day&count=3&requestType=0")
    get(f"https://api.finance.naver.com/siseJson.naver?symbol={c}&requestType=1&startTime=20261112&endTime=20261117&timeframe=day")
    get(f"https://m.stock.naver.com/api/stock/{c}/price?pageSize=3&page=1")
    get(f"https://m.stock.naver.com/api/stock/{c}/basic")
get("https://m.stock.naver.com/api/stocks/up/KOSDAQ?page=1&pageSize=3")
get("https://finance.naver.com/sise/sise_rise.naver?sosok=1")
open("debug/naver.txt", "w", encoding="utf-8").write("\n".join(out))
