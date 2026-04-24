# Screen ETFs

오늘 날짜 기준으로 한미 ETF 통합 스크리닝을 실행해줘.

1. `src/screeners/unified.py`를 실행해서 한미 동시 모멘텀 상위 테마를 찾아
2. 결과를 `data/results/YYYYMMDD_unified_screen.csv`에 저장
3. 상위 5개 후보를 테마, 티커, 수익률, 매칭스코어 포함해서 요약 출력
4. 레버리지 ETF가 있으면 별도 표시하고 "최대 10거래일 보유" 경고 추가

추가 필터 조건: $ARGUMENTS
