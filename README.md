# TOP30 트렌드 대시보드

코스피·코스닥 통합 **등락률 상위 30종목**을 매일 자동으로 모아, 그날의 상승 종목과 섹터 흐름을 보여주는 대시보드입니다.

- 데이터: 네이버 증권 업종 분류 + 일별 종가 (로그인 불필요)
- 갱신: 평일 20:30 KST, GitHub Actions 자동 실행 (첫 실행 시 약 3개월치 채움)
- 제외: 스팩·우선주, ±30% 초과 변동(분할·병합 등), 신규상장 첫날
- 화면: 하루 TOP30 · 섹터 순위 · 일자×섹터 히트맵 · 연속/반복 등장 · 시총 구간 비중 · 엑셀 다운로드

## 처음 한 번 설정
1. Settings → Pages → Source: **Deploy from a branch** → `main` / `(root)` → Save
2. Actions 탭 → "TOP30 데이터 갱신" → **Run workflow** (첫 데이터 채우기, 5~10분)
3. `https://hongsup79-creator.github.io/top30-trend/` 접속

## 로컬 미리보기
```bash
pip install -r requirements.txt
python scripts/update_top30.py --demo   # 가짜 데이터 생성 (커밋하지 말 것)
python -m http.server                   # http://localhost:8000
```

## 파일
- `scripts/top30_core.py` 계산 로직 (테스트: `tests/test_core.py`)
- `scripts/update_top30.py` 수집·저장
- `data/boards/YYYYMM.json` 일자별 TOP30, `data/summary.json` 요약
