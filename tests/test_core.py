import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from top30_core import build_summary, cap_bucket, daily_top, is_excluded  # noqa: E402


def test_is_excluded():
    assert is_excluded("삼성전자우")
    assert is_excluded("현대차2우B")
    assert is_excluded("대신증권우B")
    assert is_excluded("한화솔루션우(전환)")
    assert is_excluded("하나30호스팩")
    assert not is_excluded("삼성전자")
    assert not is_excluded("우리기술")       # '우'로 시작하는 건 정상
    assert not is_excluded("대우건설")
    assert is_excluded("삼성전자우", "005935")
    assert not is_excluded("가상대우", "123450")   # 이름이 '우'로 끝나도 보통주 코드면 포함


def test_cap_bucket_boundaries():
    assert cap_bucket(5e10) == "1천억 미만"
    assert cap_bucket(1e11) == "1천억~5천억"
    assert cap_bucket(5e11) == "5천억~1조"
    assert cap_bucket(1e12) == "1조 이상"


def _frame(d):
    idx = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12"])
    return pd.DataFrame(d, index=idx)


META = {
    "A00001": {"name": "가", "sector": "반도체", "cap": 2e11},
    "A00002": {"name": "나", "sector": "제약", "cap": 9e11},
    "A00003": {"name": "다", "sector": "반도체", "cap": 3e12},
    "A00004": {"name": "라", "sector": "조선", "cap": 5e10},
}


def test_daily_top_basic_and_rules():
    price = _frame({
        "A00001": [100, 110, 121],           # +10%, +10%
        "A00002": [100, np.nan, 120],        # 정지 후 재개: 11-12는 직전 유효(100) 대비 +20%
        "A00003": [100, 50, 52],             # -50%(분할 추정 → 제외), +4%
        "A00004": [np.nan, 100, 99],         # 신규상장: 11-11 제외, 11-12 -1%
    })
    boards = daily_top(price, META, n=2)
    assert list(boards) == ["20261111", "20261112"]
    d11 = boards["20261111"]
    assert [r["code"] for r in d11] == ["A00001"]           # 2·3·4 모두 제외
    d12 = boards["20261112"]
    assert [r["code"] for r in d12] == ["A00002", "A00001"]
    assert d12[0]["rank"] == 1 and d12[0]["chg"] == 20.0
    # 과거 시총 근사: 현재시총 × 당일종가/최근종가 (최근일이면 그대로)
    assert d12[0]["cap"] == 9e11 and d12[0]["bucket"] == "5천억~1조"
    assert d11[0]["cap"] == round(2e11 * 110 / 121)


def test_daily_top_skips_unknown_and_excluded():
    meta = dict(META)
    meta["A00005"] = {"name": "가나우", "sector": "제약", "cap": 1e11}
    price = _frame({"A00001": [100, 101, 102], "A00005": [100, 120, 130], "ZZZ999": [1, 2, 3]})
    boards = daily_top(price, meta, n=30)
    assert all(r["code"] == "A00001" for rows in boards.values() for r in rows)


def _row(code, name="x", sector="s"):
    return {"code": code, "name": name, "sector": sector, "chg": 10.0, "cap": 1e11, "bucket": "1천억~5천억"}


def test_build_summary_streaks():
    boards = {
        "20261110": [_row("A"), _row("B")],
        "20261111": [_row("A"), _row("C")],
        "20261112": [_row("A"), _row("B"), _row("C")],
    }
    s = build_summary(boards)
    assert s["dates"] == ["20261110", "20261111", "20261112"]
    assert s["months"] == ["202611"]
    assert s["latest_date"] == "20261112"
    mom = {m["code"]: m for m in s["momentum"]}
    assert mom["A"]["current_streak"] == 3 and mom["A"]["max_streak"] == 3
    assert mom["C"]["current_streak"] == 2
    assert "B" not in mom                          # 최신 연속 1일
    assert s["momentum"][0]["code"] == "A"
    rec = {r["code"]: r["count"] for r in s["recurring"]}
    assert rec == {"A": 3, "B": 2, "C": 2}


def test_is_excluded_products():
    assert is_excluded("미래에셋 인버스 2X 반도체 ETN", "520036")
    assert is_excluded("SOL SK하이닉스선물단일종목인버스2X", "123450")
    assert is_excluded("KODEX 200", "069500")
    assert is_excluded("아무거나", "123450", sector="기타") is False   # 기타라도 상품명이 아니면 포함


def test_official_override_ranks_by_krx_ratio():
    price = _frame({
        "A00001": [100, 100, 127.8],   # 차트(넥스트레이드 포함) +27.8%
        "A00002": [100, 100, 128.5],   # 차트 +28.5%
    })
    meta = {"A00001": {"name": "가", "sector": "s", "cap": 1e11},
            "A00002": {"name": "나", "sector": "s", "cap": 1e11}}
    official = {("20261112", "A00001"): {"chg": 30.0, "close": 130.0, "vol": 10.0}}
    rows = daily_top(price, meta, n=2, official=official)["20261112"]
    assert [r["code"] for r in rows] == ["A00001", "A00002"]
    assert rows[0]["chg"] == 30.0 and rows[0]["close"] == 130.0 and rows[0]["src"] == "krx"
    assert rows[0]["amount"] == 1300 and rows[0]["limit"] is True
    assert rows[1]["src"] == "chart" and rows[1]["limit"] is False


def test_min_amount_filters_illiquid():
    price = _frame({"A00001": [100, 100, 120], "A00002": [100, 100, 110], "A00003": [100, 100, 105]})
    meta = {c: {"name": c, "sector": "s", "cap": 1e11} for c in price.columns}
    official = {("20261112", "A00001"): {"chg": 20.0, "close": 120.0, "vol": 1000.0},        # 12만원 → 제외
                ("20261112", "A00002"): {"chg": 10.0, "close": 110.0, "vol": 10_000_000.0},  # 11억 → 포함
                ("20261112", "A00003"): {"chg": 5.0, "close": 105.0, "vol": None}}           # 거래량 모름 → 포함
    rows = daily_top(price, meta, n=30, official=official, min_amount=5e8)["20261112"]
    assert [r["code"] for r in rows] == ["A00002", "A00003"]
    assert rows[0]["rank"] == 1
