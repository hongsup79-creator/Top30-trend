"""
TOP30 트렌드 데이터 생성 스크립트

실행:  python scripts/update_top30.py          # 실제 데이터
       python scripts/update_top30.py --demo   # 가짜 데이터로 화면 미리보기

데이터 출처 (로그인 불필요, Stock Trend Radar와 동일)
  - 종목·업종·시가총액 : 네이버 업종 분류 (m.stock.naver.com/api/stocks/industry)
  - 후보 선정용 종가   : 네이버 차트 (fchart.stock.naver.com) — 넥스트레이드 애프터마켓이 섞일 수 있음
  - 공식 등락률·종가   : 네이버 증권 일별 시세 (m.stock.naver.com/api/stock/{코드}/price) — 거래소 정규장 기준
    → 차트로 날마다 상위 80개 후보를 뽑고, 후보만 공식 값으로 다시 순위를 매김

결과
  data/boards/YYYYMM.json  {"YYYYMMDD": [TOP30 행, ...], ...}
  data/summary.json        일자 목록·연속 등장·반복 등장
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from top30_core import build_summary, daily_top  # noqa: E402

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BOARDS = DATA / "boards"
CACHE = DATA / "cache"

TOP_N = 30
BACKFILL_DAYS = 70       # 처음 실행 시 약 3개월
RECENT_DAYS = 10         # 평소: 최근 10거래일 다시 계산(정정 반영)
WORKERS = 8              # 동시 요청 수 (너무 크면 네이버가 막을 수 있음)
SCHEMA = 2               # 계산 방식이 바뀌면 올림 → 다음 실행에서 전 기간 다시 계산
MIN_UNIVERSE = 1000      # 이보다 적게 받아지면 수집 실패로 보고 중단(기존 데이터 보존)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}
CODE_KEYS = ("itemCode", "stockCode", "code", "symbolCode")
NAME_KEYS = ("stockName", "itemName", "name", "stockNameKor")


def warn(msg):
    print(f"::warning::{msg}")


def pmap(fn, items):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        return list(ex.map(fn, items))


# ---------------------------------------------------------------- 수집 도구 (Radar와 동일)
def _get(url, as_json=True):
    import requests
    last = None
    for _ in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=15)
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"{r.status_code} 거부")
            r.raise_for_status()
            return r.json() if as_json else r
        except RuntimeError:
            raise
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise RuntimeError(f"접속 실패: {url} ({last})")


def _cap_from(d):
    """시가총액(원). '432조 9,104억' 우선, 없으면 marketValue(억 단위)."""
    txt = str(d.get("marketValueHangeul") or "")
    m = re.search(r"(?:([\d,]+)조)?\s*(?:([\d,]+)억)?", txt)
    if txt and m and (m.group(1) or m.group(2)):
        jo = int((m.group(1) or "0").replace(",", ""))
        eok = int((m.group(2) or "0").replace(",", ""))
        return (jo * 10_000 + eok) * 100_000_000
    v = str(d.get("marketValue") or "").replace(",", "")
    return int(float(v)) * 100_000_000 if re.fullmatch(r"[\d.]+", v) else 0


def parse_stock_list(data):
    found = []

    def walk(o):
        if isinstance(o, dict):
            code = next((str(o[k]) for k in CODE_KEYS if k in o), "")
            name = next((str(o[k]) for k in NAME_KEYS if k in o), "")
            if re.fullmatch(r"[0-9A-Z]{6}", code) and name:
                found.append((code, name, _cap_from(o)))
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found


def _paged(url_tmpl, max_pages=10):
    stocks, seen = [], set()
    for page in range(1, max_pages + 1):
        rows = [r for r in parse_stock_list(_get(url_tmpl.format(page=page))) if r[0] not in seen]
        if not rows:
            break
        seen.update(r[0] for r in rows)
        stocks += rows
        time.sleep(0.05)
    return stocks


def load_cache(name, max_age_days):
    try:
        d = json.loads((CACHE / f"{name}.json").read_text(encoding="utf-8"))
        saved = datetime.fromisoformat(d["_saved"])
        return d if datetime.now(KST) - saved <= timedelta(days=max_age_days) else None
    except Exception:
        return None


def save_cache(name, d):
    CACHE.mkdir(parents=True, exist_ok=True)
    d["_saved"] = datetime.now(KST).isoformat(timespec="seconds")
    (CACHE / f"{name}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def fetch_universe():
    """네이버 업종 분류 → {코드: {name, sector, cap}}. 업종 구성은 7일마다, 시가총액은 매일 새로."""
    cached = load_cache("industry", max_age_days=7)
    groups = cached["groups"] if cached else None
    if not groups:
        groups, seen = [], set()
        for page in range(1, 6):
            try:
                data = _get(f"https://m.stock.naver.com/api/stocks/industry?page={page}")
            except Exception:
                break
            found = []

            def walk(o):
                if isinstance(o, dict):
                    no = next((o[k] for k in ("no", "industryCode", "upjongNo") if k in o), None)
                    nm = next((o[k] for k in ("name", "industryName", "upjongName") if k in o), None)
                    if no is not None and nm and str(no) not in seen:
                        found.append((str(no), str(nm)))
                        return
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)

            walk(data)
            if not found:
                break
            seen.update(g[0] for g in found)
            groups += found

    def one(g):
        for size in (50, 20):
            try:
                return _paged(f"https://m.stock.naver.com/api/stocks/industry/{g[0]}?page={{page}}&pageSize={size}",
                              max_pages=12)
            except Exception:
                continue
        return []

    meta = {}
    for (_, sector), rows in zip(groups, pmap(one, groups)):
        for code, name, cap in rows:
            meta[code] = {"name": name, "sector": sector, "cap": cap}
    print(f"  네이버 업종 {len(groups)}개, 종목 {len(meta)}개")
    if len(meta) >= MIN_UNIVERSE:
        save_cache("industry", {"groups": groups})
    return meta


def naver_history(code, count):
    """네이버 차트 일봉 → DataFrame[close, vol]. 한 줄 형식: 날짜|시가|고가|저가|종가|거래량"""
    url = (f"https://fchart.stock.naver.com/sise.nhn?symbol={code}"
           f"&timeframe=day&count={count}&requestType=0")
    text = _get(url, as_json=False).content.decode("euc-kr", errors="replace")
    rows = {pd.Timestamp(d): (float(c), float(v) if v else np.nan)
            for d, c, v in re.findall(r'data="(\d{8})\|[^|]*\|[^|]*\|[^|]*\|([\d.]+)\|([\d.]*)', text)}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["close", "vol"]).sort_index()


def fetch_prices(codes, count):
    def one(c):
        try:
            return naver_history(c, count)
        except Exception:
            return None
    closes, vols = {}, {}
    for c, df in zip(codes, pmap(one, codes)):
        if df is not None and len(df):
            closes[c], vols[c] = df["close"], df["vol"]
    print(f"  시세 {len(closes)}/{len(codes)}개 수집")
    price = pd.DataFrame(closes).sort_index().replace(0, np.nan)
    vol = pd.DataFrame(vols).reindex(index=price.index, columns=price.columns)
    return drop_intraday(price), vol


def drop_intraday(df):
    """네이버 차트는 넥스트레이드 애프터마켓(~20:00)까지 반영 → 20:05 전이면 오늘 값 제외"""
    now = datetime.now(KST)
    if len(df) and df.index[-1].date() == now.date() and (now.hour, now.minute) < (20, 5):
        print(f"  장중 실행({now:%H:%M}) → 오늘 값 제외")
        return df.iloc[:-1]
    return df


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("+", "").strip())
    except Exception:
        return None


def parse_official(data):
    """일별 시세 JSON → {YYYYMMDD: {chg, close, vol}}. 키 이름이 바뀌어도 찾도록 너그럽게 파싱"""
    out = {}

    def walk(o):
        if isinstance(o, dict):
            dkey = next((k for k in ("localTradedAt", "tradeDate", "localDate", "date") if k in o), None)
            if dkey and "closePrice" in o:
                d = re.sub(r"\D", "", str(o[dkey]))[:8]
                close = _num(o.get("closePrice"))
                chg = _num(o.get("fluctuationsRatio"))
                if chg is not None:
                    # 하락 표시는 compareToPreviousPrice.name(FALLING/LOWER_LIMIT) 또는 부호로 구분
                    kind = str((o.get("compareToPreviousPrice") or {}).get("name", "")).upper()
                    if chg > 0 and ("FALL" in kind or "LOWER" in kind):
                        chg = -chg
                if len(d) == 8 and close and chg is not None:
                    out[d] = {"chg": chg, "close": close,
                              "vol": _num(o.get("accumulatedTradingVolume"))}
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


def fetch_official(codes, days):
    """후보 종목의 거래소 공식 일별 등락률 → {(YYYYMMDD, 코드): {...}}"""
    size = min(60, days + 2)
    pages = (days + 2 + size - 1) // size

    def one(c):
        res = {}
        for page in range(1, pages + 1):
            try:
                res.update(parse_official(_get(
                    f"https://m.stock.naver.com/api/stock/{c}/price?pageSize={size}&page={page}")))
            except Exception:
                break
        return res

    official = {}
    for c, res in zip(codes, pmap(one, codes)):
        for d, v in res.items():
            official[(d, c)] = v
    got = len({c for _, c in official})
    print(f"  공식 시세 {got}/{len(codes)}종목")
    if codes and got < len(codes) * 0.5:
        warn(f"공식 시세를 {got}/{len(codes)}종목만 받았습니다 — 나머지는 차트 가격으로 계산합니다.")
    return official


# ---------------------------------------------------------------- 가짜 데이터
SECTORS = ["반도체와반도체장비", "제약", "생물공학", "조선", "기계", "전기장비", "소프트웨어",
           "화학", "자동차부품", "건설", "게임엔터테인먼트", "식품", "통신장비", "IT서비스",
           "디스플레이장비및부품", "전자장비와기기", "우주항공과국방", "에너지장비및서비스"]


def demo_inputs(days):
    rng = np.random.default_rng(7)
    n = 400
    codes = [f"{i:05d}0" for i in range(1, n + 1)]
    meta = {c: {"name": f"샘플종목{i + 1:03d}", "sector": SECTORS[i % len(SECTORS)],
                "cap": float(np.exp(rng.normal(26, 1.3)))} for i, c in enumerate(codes)}
    idx = pd.bdate_range(end=pd.Timestamp(datetime.now(KST).date()), periods=days)
    # 섹터별 공통 흐름 + 개별 노이즈 → 트렌드가 보이도록
    sec_drift = {s: rng.normal(0, 0.012, len(idx)) for s in SECTORS}
    rets = np.column_stack([sec_drift[meta[c]["sector"]] + rng.standard_t(3, len(idx)) * 0.02 for c in codes])
    rets = np.clip(rets, -0.29, 0.29)
    price = pd.DataFrame(1000 * np.cumprod(1 + rets, axis=0), index=idx, columns=codes).round()
    vol = pd.DataFrame(rng.integers(1e4, 5e6, price.shape), index=idx, columns=codes).astype(float)
    return meta, price, vol


# ---------------------------------------------------------------- 저장
def load_boards():
    boards = {}
    for f in sorted(BOARDS.glob("*.json")):
        boards.update(json.loads(f.read_text(encoding="utf-8")))
    return boards


def save_boards(boards):
    BOARDS.mkdir(parents=True, exist_ok=True)
    by_month = {}
    for d, rows in boards.items():
        by_month.setdefault(d[:6], {})[d] = rows
    for f in BOARDS.glob("*.json"):                 # 기간 밖으로 밀려난 옛 월 파일 정리
        if f.stem not in by_month:
            f.unlink()
    for ym, part in by_month.items():
        (BOARDS / f"{ym}.json").write_text(
            json.dumps(dict(sorted(part.items())), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def days_needed(boards):
    if not boards:
        return BACKFILL_DAYS
    last = pd.Timestamp(max(boards))
    gap = len(pd.bdate_range(last, pd.Timestamp(datetime.now(KST).date())))
    return int(min(300, max(RECENT_DAYS, gap + 5)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    boards = load_boards()
    try:
        old_schema = json.loads((DATA / "summary.json").read_text(encoding="utf-8")).get("schema", 1)
    except Exception:
        old_schema = SCHEMA
    if boards and old_schema != SCHEMA:
        print(f"  계산 방식 변경(v{old_schema}→v{SCHEMA}) → 전 기간 다시 계산")
        boards = {}
    count = days_needed(boards)
    if args.demo:
        meta, price, vol = demo_inputs(BACKFILL_DAYS)
        boards = {}
    else:
        print(f"1) 종목·업종 수집")
        meta = fetch_universe()
        if len(meta) < MIN_UNIVERSE:
            sys.exit(f"종목이 {len(meta)}개뿐 — 수집 실패로 보고 중단합니다(기존 데이터 보존).")
        print(f"2) 시세 수집 (최근 {count}거래일)")
        price, vol = fetch_prices(sorted(meta), count)
        if price.shape[1] < MIN_UNIVERSE:
            sys.exit(f"시세가 {price.shape[1]}종목뿐 — 수집 실패로 보고 중단합니다(기존 데이터 보존).")

    official = {}
    if not args.demo:
        cands = sorted({r["code"] for rows in daily_top(price, meta, vol, n=80).values() for r in rows})
        print(f"3) 공식 등락률 수집 (후보 {len(cands)}종목)")
        official = fetch_official(cands, count)
    new = daily_top(price, meta, vol, n=TOP_N, official=official)
    krx = sum(r["src"] == "krx" for rows in new.values() for r in rows)
    total = sum(len(rows) for rows in new.values())
    boards.update(new)
    save_boards(boards)
    summary = build_summary(boards)
    summary["generated_at"] = datetime.now(KST).isoformat(timespec="minutes")
    summary["demo"] = bool(args.demo)
    summary["schema"] = SCHEMA
    summary["krx_share"] = round(krx / total, 3) if total else 0     # 공식 시세로 계산된 비율(점검용)
    (DATA / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"4) 저장 (공식 시세 {krx}/{total}행): {len(new)}일 갱신, 전체 {len(summary['dates'])}일 (최신 {summary['latest_date']})")


if __name__ == "__main__":
    main()
