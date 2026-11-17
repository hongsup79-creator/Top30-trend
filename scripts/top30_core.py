"""TOP30 계산 로직 (네트워크 없이 테스트 가능한 순수 함수)"""

import re

import numpy as np
import pandas as pd

BUCKETS = ["1천억 미만", "1천억~5천억", "5천억~1조", "1조 이상"]
MAX_MOVE = 30.5
LIMIT_UP = 29.5          # 상한가(호가 단위 때문에 29.5% 이상이면 상한가로 표시)          # 가격제한폭(±30%) 초과 = 분할·병합 등 기업행위로 보고 제외
PREF_RE = re.compile(r"(\d?우[A-Z]?|우\(전환\))$")


PRODUCT_RE = re.compile(r"ETN|ETF|인버스|레버리지|선물|\d[Xx]\b|^(KODEX|TIGER|RISE|ACE|SOL|HANARO|KOSEF|ARIRANG|PLUS|KIWOOM|TIMEFOLIO|WON)\b")


def is_excluded(name: str, code: str = "", sector: str = "") -> bool:
    """스팩·우선주·ETN/ETF 같은 상품 제외.
    우선주는 이름 끝(우/2우B/우(전환))에 더해, 코드가 주어지면 끝자리가 0이 아닌 경우로 한정
    (보통주 코드는 0으로 끝남) → '…우'로 끝나는 보통주 오판 방지"""
    name = str(name).strip()
    if "스팩" in name or PRODUCT_RE.search(name):
        return True
    if not PREF_RE.search(name):
        return False
    return not code or str(code)[-1] != "0"


def cap_bucket(cap_won: float) -> str:
    if cap_won < 1e11:
        return BUCKETS[0]
    if cap_won < 5e11:
        return BUCKETS[1]
    if cap_won < 1e12:
        return BUCKETS[2]
    return BUCKETS[3]


def daily_top(price: pd.DataFrame, meta: dict, vol: pd.DataFrame | None = None, n: int = 30,
              official: dict | None = None, pool: int = 80) -> dict:
    """일자별 등락률 상위 n종목.
    price: 행=날짜, 열=종목코드 종가(네이버 차트, 넥스트레이드 애프터마켓이 섞일 수 있음).
    official: {(YYYYMMDD, 코드): {chg, close, vol}} 거래소 공식 등락률·종가. 있으면 우선 사용.
    차트 기준 상위 pool개를 후보로 잡고, 후보는 공식 값으로 바꿔 다시 순위를 매김."""
    official = official or {}
    cols = [c for c in price.columns if c in meta and not is_excluded(meta[c]["name"], c, meta[c].get("sector", ""))]
    price = price[cols].sort_index()
    filled = price.ffill()
    prev = filled.shift(1)
    chg = (price / prev - 1) * 100
    chg = chg.where(price.notna() & prev.notna())
    last_close = filled.iloc[-1] if len(filled) else pd.Series(dtype=float)
    vol = vol.reindex(index=price.index, columns=cols) if vol is not None else None

    boards = {}
    for dt, row in chg.iterrows():
        d = dt.strftime("%Y%m%d")
        s = row.dropna()
        s = s[s.abs() <= MAX_MOVE + 5]                     # 넥스트레이드 차이 감안해 넉넉히 후보로
        cand = sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))[:pool]
        items = []
        for code, c in cand:
            o = official.get((d, code))
            if o:
                items.append((code, float(o["chg"]), float(o["close"]), o.get("vol"), "krx"))
            else:
                v = float(vol.at[dt, code]) if vol is not None else np.nan
                items.append((code, float(c), float(price.at[dt, code]), v, "chart"))
        items = [it for it in items if abs(it[1]) <= MAX_MOVE]
        items.sort(key=lambda it: (-it[1], it[0]))
        if not items:
            continue
        rows = []
        for rank, (code, c, close, v, src) in enumerate(items[:n], 1):
            m = meta[code]
            cap_now = float(m.get("cap") or 0)
            lc = float(last_close.get(code, np.nan))
            cap = round(cap_now * float(price.at[dt, code]) / lc) if cap_now and lc > 0 else 0
            amt = close * float(v) if v is not None and not np.isnan(float(v)) else 0
            rows.append({
                "rank": rank, "code": code, "name": m["name"], "sector": m.get("sector") or "기타",
                "chg": round(c, 2), "close": close, "cap": cap, "amount": round(amt),
                "bucket": cap_bucket(cap), "limit": c >= LIMIT_UP, "src": src,
            })
        boards[d] = rows
    return boards


def build_summary(boards: dict) -> dict:
    dates = sorted(boards)
    present = {d: {r["code"] for r in boards[d]} for d in dates}
    info = {}
    for d in dates:
        for r in boards[d]:
            info[r["code"]] = r                      # 최근 정보로 갱신

    cur, best, count = {}, {}, {}
    for d in dates:
        today = present[d]
        for code in today:
            cur[code] = cur.get(code, 0) + 1
            best[code] = max(best.get(code, 0), cur[code])
            count[code] = count.get(code, 0) + 1
        for code in list(cur):
            if code not in today:
                cur[code] = 0

    def base(code):
        r = info[code]
        return {"code": code, "name": r["name"], "sector": r["sector"], "cap": r["cap"]}

    latest = dates[-1] if dates else None
    momentum = [
        {**base(c), "current_streak": cur[c], "max_streak": best[c]}
        for c in (present[latest] if latest else []) if cur[c] >= 2
    ]
    momentum.sort(key=lambda m: (-m["current_streak"], -m["max_streak"], m["name"]))
    recurring = [
        {**base(c), "count": k, "dates": [d for d in dates if c in present[d]]}
        for c, k in count.items() if k >= 2
    ]
    recurring.sort(key=lambda r: (-r["count"], r["name"]))
    return {
        "dates": dates,
        "months": sorted({d[:6] for d in dates}),
        "latest_date": latest,
        "bucket_labels": BUCKETS,
        "momentum": momentum,
        "recurring": recurring[:50],
    }
