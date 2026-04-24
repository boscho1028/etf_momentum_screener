# ETF Momentum Screener

한국/미국 ETF 모멘텀 스크리너. 매일 아침 Jegadeesh-Titman 모멘텀으로 ETF를 필터링하고, 한미 테마 교차검증 후 텔레그램 채널로 리포트를 보냅니다.

## 전략

- **모멘텀 점수**: `1W × 0.4 + 1M × 0.4 + 3M × 0.2` (최근 구간 가중)
- **거래대금**: 국내 일평균 100억원↑, 미국 $10M↑
- **체제 필터**: SPY가 200일선 아래면 전체 중단
- **손절**: 진입가 − 2×ATR(14)
- **괴리율**: KIS API로 실시간 확인, ±1% 이내만 진입

## 다른 PC에 설치 (최소 세팅)

```bash
git clone https://github.com/boscho1028/etf_momentum_screener.git
cd etf_momentum_screener

# Python 3.12 64-bit 권장
py -3.12 -m venv venv_mom_etf
./venv_mom_etf/Scripts/activate     # Windows
# source venv_mom_etf/bin/activate  # Linux/Mac

pip install -r requirements.txt

# .env 준비 (.env.example 참고)
cp .env.example .env
# 편집: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO,
#       TURSO_DATABASE_URL, TURSO_AUTH_TOKEN,
#       TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

# 실행
python main.py
```

첫 실행 시 Turso DB에 테이블 자동 생성 + 구버전 스키마가 있으면 ALTER TABLE로 마이그레이션됩니다.

## 스케줄러 (Windows)

```powershell
.\setup_scheduler.ps1
```

평일 08:50 자동 실행.

## 구조

```
src/
  kis/       - KIS Open API (auth, quote, order)
  screeners/ - 국내/미국/통합 스크리너
  db/        - Turso(libSQL) 저장소
  notify/    - 텔레그램 채널 전송
  utils/     - ATR, RSI, 이동평균
data/
  kr_us_mapping.csv              - 테마별 한미 ETF 매핑 (24 페어)
  kr_etf_universe_fallback.csv   - KRX 로그인 차단 대비 정적 유니버스
main.py      - 진입점
```

## 알려진 제약

- **pykrx KRX 로그인**: 2025~2026년경 KRX가 data.krx.co.kr 접근을 로그인 필수로 전환했습니다. 기본값으로는 `data/kr_etf_universe_fallback.csv`의 194종목을 스캔합니다. 전체 800종목 스캔이 필요하면 [data.krx.co.kr](https://data.krx.co.kr) 계정을 만들고 `.env`에 `KRX_ID`, `KRX_PW`를 추가하세요.
- **KIS Access Token**: 하루 만료. 토큰 캐시는 `./tokens/`에 저장되며 재발급 시 자동 갱신. `KIS_TOKEN_CACHE_DIR`로 경로 변경 가능.

## 라이선스

MIT
