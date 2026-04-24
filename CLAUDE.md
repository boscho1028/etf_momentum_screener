# ETF Momentum Trading System

## 프로젝트 목적
한국과 미국 ETF의 **모멘텀 전략 기반 단기 매매 시스템**.
- 최근 급등 ETF를 스크리닝
- 한미 ETF 중 같은 테마의 '공통분모'를 찾아 교차 검증
- 한국투자증권(KIS) Open API로 실시간 확인 및 주문

## 전략 원칙 (Non-negotiable)
- **1개월 AND 3개월 수익률 모두 상위**인 종목만 후보 (Jegadeesh-Titman 모멘텀)
- **거래대금**: 국내 일평균 100억원 이상, 미국 $10M 이상
- **괴리율**: NAV 대비 ±1% 이내만 진입
- **손절**: 진입가 - 2×ATR(14). 고정 % 손절 금지.
- **레버리지 ETF**: 최대 보유 10거래일. 그 이상 필요 시 비레버리지로 교체.
- **시장 체제 필터**: SPY가 200일선 아래면 전체 중단.
- **섹터 중복 제거**: 상위권에서 기초자산 겹치면 대표 1~2개만.

## 기술 스택
- Python 3.11+
- `pykrx`: 국내 ETF 전종목 OHLCV + NAV + 거래대금
- `yfinance`: 미국 ETF 데이터
- KIS Open API: 실시간 시세, 괴리율, 주문
- pandas, numpy: 데이터 처리

## 코딩 컨벤션
- 함수명: snake_case
- 클래스명: PascalCase
- 모든 함수에 타입 힌트 필수
- docstring: Google 스타일
- print 대신 logging 모듈 사용
- API 키는 절대 하드코딩 금지 → `.env`에서만 로드
- 국내 ETF 티커: 6자리 문자열 ("069500"). 절대 int로 변환 금지.
- 미국 ETF 티커: 대문자 ("SPY", "QQQ")

## KIS API 주요 특이사항
- REST Base URL (실전): `https://openapi.koreainvestment.com:9443`
- REST Base URL (모의): `https://openapivts.koreainvestment.com:29443`
- Access Token 유효기간: 1일, 재발급 시 기존 토큰 무효화
- 순위 API 최대 30건 제한 → 더 필요 시 pykrx로 우회
- ETF 괴리율: `inquire-price` 호출 후 직접 계산 ((현재가 - NAV) / NAV * 100)
- 해외주식 EXCD 파라미터: NAS(나스닥), NYS(뉴욕), AMS(아멕스)
- TR_ID 실전/모의 분기:
  - 국내 매수: TTTC0802U (실전) / VTTC0802U (모의)
  - 국내 매도: TTTC0801U (실전) / VTTC0801U (모의)

## 데이터베이스
- Turso(libSQL) 원격 DB — stock_agent 프로젝트와 동일 인스턴스, 테이블만 분리
- `src/db/turso.py`: 연결, 테이블 초기화, 저장/조회 함수
- 테이블: `etf_screen_kr`, `etf_screen_us`, `etf_screen_unified`
- UNIQUE(screen_date, ticker) → 같은 날 재실행 시 INSERT OR REPLACE로 덮어씀
- 토큰 캐시 경로: `KIS_TOKEN_CACHE_DIR` 환경변수 우선, 없으면 `./tokens/`

## 파일 매핑
- `src/kis/auth.py`: Access Token 발급/갱신/캐싱
- `src/kis/quote.py`: 국내외 ETF 시세/괴리율 조회
- `src/kis/order.py`: 매수/매도 주문 (항상 모의 우선)
- `src/screeners/kr_screener.py`: pykrx 기반 국내 ETF 스크리너
- `src/screeners/us_screener.py`: yfinance 기반 미국 ETF 스크리너
- `src/screeners/unified.py`: 한미 매칭 후 교집합 추출
- `src/utils/indicators.py`: ATR, RSI, 이동평균 등 공통 지표
- `data/kr_us_mapping.csv`: 테마별 한미 ETF 매핑 테이블

## 커맨드
- 스크리닝 실행: `python -m src.screeners.unified`
- 테스트: `pytest tests/`
- 린트: `ruff check src/`

## 중요 주의사항
- **이 시스템은 실제 돈을 다룬다.** 주문 관련 코드는 반드시:
  1. 모의투자 계좌에서 먼저 검증
  2. 주문 전 사용자 확인 단계 (y/n) 포함
  3. 일일 최대 주문 금액 하드코딩 제한
- KIS API TR_ID/파라미터명은 공식 포털(apiportal.koreainvestment.com)과 반드시 대조
- 백테스트 결과: 생존 편향 및 거래비용 과소평가 가능성 상시 인지
