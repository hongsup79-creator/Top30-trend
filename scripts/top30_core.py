"""TOP30 계산 로직 (네트워크 없이 테스트 가능한 순수 함수)"""

import re

import numpy as np
import pandas as pd

BUCKETS = ["1천억 미만", "1천억~5천억", "5천억~1조", "1조 이상"]
MAX_MOVE = 30.5          # 가격제한폭(±30%) 초과 = 분할·병합 등 기업행위로 보고 제외
PREF_RE = re.compile(r"(\d?우[A-Z]?|우\(전환\))$")


def is_excluded(name: str, code: str = "") -> bool:
    """스팩·우선주 제외. 우선주는 이름 끝(우/2우B/우(전환))에 더해,
    코드가 주어지면 끝자리가 0이 아닌 경우로 한정(보통주 코드는 0으로 끝남) → '…우'로 끝나는 보통주 오판 방지"""
    name = str(name).strip()
    if "스팩" in name:
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


def daily_top(price: pd.DataFrame, meta: dict, vol: pd.DataFrame | None = None, n: int = 30) -> dict:
    """일자별 등락률 상위 n종목.
    price: 행=날짜, 열=종목코드 종가. 등락률은 직전 '유효' 종가 대비(거래정지 후 재개 대응)."""
    cols = [c for c in price.columns if c in meta and not is_excluded(meta[c]["name"], c)]
    price = price[cols].sort_index()
    filled = price.ffill()
    prev = filled.shift(1)
    chg = (price / prev - 1) * 100
    chg = chg.where(price.notna() & prev.notna())
    chg = chg.where(chg.abs() <= MAX_MOVE)
    last_close = filled.iloc[-1] if len(filled) else pd.Series(dtype=float)
    amount = (price * vol.reindex(index=price.index, columns=cols)) if vol is not None else None

    boards = {}
    for dt, row in chg.iterrows():
        s = row.dropna()
        if s.empty:
            continue
        top = sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        rows = []
        for rank, (code, c) in enumerate(top, 1):
            m = meta[code]
            close = float(price.at[dt, code])
            cap_now = float(m.get("cap") or 0)
            lc = float(last_close.get(code, np.nan))
            cap = round(cap_now * close / lc) if cap_now and lc > 0 else 0
            amt = float(amount.at[dt, code]) if amount is not None else np.nan
            rows.append({
                "rank": rank, "code": code, "name": m["name"], "sector": m.get("sector") or "기타",
                "chg": round(float(c), 2), "close": close, "cap": cap,
                "amount": 0 if np.isnan(amt) else round(amt), "bucket": cap_bucket(cap),
            })
        boards[dt.strftime("%Y%m%d")] = rows
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
