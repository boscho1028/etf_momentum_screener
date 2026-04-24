# KIS Open API 특이사항 메모

실제 API 호출로 검증된 내용만 기록. 추측 또는 공식 문서만 기반인 항목은 별도 표시.

---

## 인증

- 토큰 유효기간: 1일 (86400초). 재발급 시 기존 토큰 즉시 무효화.
- 멀티 프로세스에서 동시에 `_issue_token()` 호출 시 토큰 충돌 가능 → 파일 캐시로 방지.
- 토큰 캐시 경로: `.env`의 `KIS_TOKEN_CACHE_DIR` 우선. 없으면 `./tokens/token_{mode}.json`.
- `G:/내 드라이브/` 같은 Google Drive 경로 사용 시 OSError 발생 가능 → 로컬 경로 권장.

---

## TR_ID 및 엔드포인트 (검증 완료)

| 기능 | 엔드포인트 | TR_ID | 실전/모의 |
|------|-----------|-------|----------|
| 국내 ETF 현재가 + NAV | `/uapi/etfetn/v1/quotations/inquire-price` | `FHPST02400000` | 동일 |
| 국내 일반주식 현재가 | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | 동일 |
| 해외주식 현재가 | `/uapi/overseas-price/v1/quotations/price` | `HHDFS76200200` | 동일 |
| 국내 매수 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0802U` | `VTTC0802U` |
| 국내 매도 | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0801U` | `VTTC0801U` |

> **주의**: 국내 ETF에 `FHKST01010100` 사용 시 NAV 필드 없음.
> NAV가 필요하면 반드시 `FHPST02400000` + `fid_cond_mrkt_div_code=J` 사용.

---

## 국내 ETF 현재가 파라미터 (FHPST02400000)

```
GET /uapi/etfetn/v1/quotations/inquire-price
fid_cond_mrkt_div_code = J   ← 반드시 J (E나 ETF 입력 시 오류)
fid_input_iscd         = 069500
```

### 주요 응답 필드 (output)

| 필드명 | 설명 | 예시 |
|--------|------|------|
| `stck_prpr` | 현재가 | `98355` |
| `nav` | 순자산가치(NAV) | `98491.20` |
| `prdy_last_nav` | 전일 NAV | `97371.55` |
| `nav_prdy_ctrt` | NAV 전일 대비율(%) | `1.15` |
| `prdy_ctrt` | 현재가 전일 대비율(%) | `1.08` |
| `acml_vol` | 누적 거래량 | `21141722` |
| `acml_tr_pbmn` | 누적 거래대금(원) | — |
| `etf_rprs_bstp_kor_isnm` | 대표 업종명 | `KOSPI200` |
| `etf_div_name` | ETF 분류명 | `국내주식형` |

---

## 해외 ETF 현재가 파라미터 (HHDFS76200200)

```
GET /uapi/overseas-price/v1/quotations/price
AUTH = ""
EXCD = NAS
SYMB = QQQ
```

### 주요 응답 필드 (output)

| 필드명 | 설명 |
|--------|------|
| `last` | 현재가(달러) |
| `pvol` | 전일 거래량 |
| `t_rate` | 거래대금(달러) |

> `rate` 필드(등락률)는 응답에 있는 경우도 있고 없는 경우도 있음. 없으면 빈 문자열 반환 → `float()` 변환 시 ValueError 주의.

---

## EXCD 거래소 코드 (실제 검증)

KIS 공식 문서와 다른 경우가 있으므로 아래 실측값을 우선.

| 티커 | EXCD | 비고 |
|------|------|------|
| QQQ | NAS | 나스닥 |
| TQQQ | NAS | 나스닥 |
| SOXX | NAS | 나스닥 |
| SMH | NAS | **공식문서는 AMS라 표기되나 실제론 NAS** |
| TLT | NAS | 나스닥 |
| SPY | AMS | NYSE ARCA → AMS |
| UPRO | AMS | NYSE ARCA → AMS |
| SOXL | AMS | NYSE ARCA → AMS |
| GLD | AMS | NYSE ARCA → AMS |
| GDX | AMS | NYSE ARCA → AMS |
| VUG | AMS | NYSE ARCA → AMS |
| XLE | AMS | NYSE ARCA → AMS |
| VNQ | AMS | NYSE ARCA → AMS |
| SHY | AMS | NYSE ARCA → AMS |
| EWJ | AMS | NYSE ARCA → AMS |
| INDA | AMS | NYSE ARCA → AMS |

> **규칙**: NYSE ARCA 상장 ETF → `AMS`, NASDAQ 상장 ETF → `NAS`.
> 새 티커 추가 시 `NYS`, `AMS`, `NAS` 순으로 시도해서 `last` 필드가 비어있지 않은 코드 사용.

---

## 괴리율 계산

KIS API에 직접 괴리율 필드 없음. `nav`와 `stck_prpr`로 직접 계산:

```python
괴리율(%) = (현재가 - NAV) / NAV * 100
```

- NAV가 0으로 반환되는 경우: 장외 시간 또는 당일 NAV 미산출 → 괴리율 계산 불가, 0.0 처리.
- 전략 기준: 절댓값 ±1% 초과 시 진입 금지.

---

## Rate Limit

- 초당 20건 제한 (실전/모의 동일).
- `get_kr_etf_batch()` / `get_us_etf_batch()` 내부에서 50ms 슬립 적용.
- 순위 API 최대 30건 → 전종목 스크리닝은 pykrx 사용.

---

## 기타 주의사항

- `fid_cond_mrkt_div_code`에 `E` 또는 `ETF` 입력 시 → `ERROR INVALID INPUT_FILED_SIZE` 오류.
- 모의투자 계좌번호는 실전과 다름 → `.env KIS_ACCOUNT_NO` 값 반드시 모드에 맞게 확인.
- 장외 시간대에도 API 호출 가능. 시세는 전일 종가 반환.
- `hts_kor_isnm` 필드: `FHPST02400000` 응답에 없음. 대신 `etf_rprs_bstp_kor_isnm`(업종명) 사용.
