"""KIS Open API 시세/NAV/괴리율 조회."""
import logging
import time
from dataclasses import dataclass

import requests

from src.kis.auth import KisAuth

logger = logging.getLogger(__name__)

# 국내 ETF 현재가 + NAV 조회 (FHPST02400000, 마켓코드 J 필수)
_TR_KR_PRICE = "FHPST02400000"
# 해외주식 현재가 조회 (실전/모의 동일)
_TR_US_PRICE = "HHDFS76200200"

# KIS API 초당 20건 제한 — 연속 호출 시 최소 간격
_RATE_LIMIT_SLEEP = 0.05  # 50ms


@dataclass
class KrEtfQuote:
    """국내 ETF 시세 + 괴리율."""
    ticker: str
    name: str
    current_price: float
    nav: float
    discount_rate: float      # (현재가 - NAV) / NAV * 100
    volume: int
    trading_value: float      # 원
    change_rate: float        # 전일 대비 등락률 (%)


@dataclass
class UsEtfQuote:
    """해외 ETF 현재가."""
    ticker: str
    exchange: str             # NAS / NYS / AMS
    current_price: float
    change_rate: float        # 전일 대비 등락률 (%)
    volume: int


class KisQuote:
    """KIS 시세 조회 클라이언트.

    Args:
        auth: KisAuth 인스턴스. None이면 환경변수 기본값으로 생성.
    """

    def __init__(self, auth: KisAuth | None = None) -> None:
        self._auth = auth or KisAuth()

    # ── 국내 ETF ────────────────────────────────────────────────

    def get_kr_etf(self, ticker: str) -> KrEtfQuote:
        """국내 ETF 현재가 + NAV + 괴리율 조회.

        Args:
            ticker: 6자리 문자열 티커 (예: "069500").

        Returns:
            KrEtfQuote 데이터클래스.

        Raises:
            requests.HTTPError: API 오류 시.
            ValueError: 응답 파싱 실패 시.
        """
        url = f"{self._auth.base_url}/uapi/etfetn/v1/quotations/inquire-price"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
        }
        resp = requests.get(
            url,
            headers=self._auth.get_headers(_TR_KR_PRICE),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise ValueError(f"KIS API 오류 [{ticker}]: {data.get('msg1', '')}")

        out = data["output"]

        current_price = float(out.get("stck_prpr", 0))
        nav = float(out.get("nav", 0) or 0)

        # NAV 미제공 시 괴리율 계산 불가
        discount_rate = (
            (current_price - nav) / nav * 100 if nav > 0 else 0.0
        )

        return KrEtfQuote(
            ticker=ticker,
            name=out.get("etf_rprs_bstp_kor_isnm", out.get("hts_kor_isnm", "")),
            current_price=current_price,
            nav=nav,
            discount_rate=round(discount_rate, 4),
            volume=int(out.get("acml_vol", 0)),
            trading_value=float(out.get("acml_tr_pbmn", 0)),
            change_rate=float(out.get("prdy_ctrt", 0)),
        )

    def get_kr_etf_batch(
        self, tickers: list[str], rate_limit: bool = True
    ) -> dict[str, KrEtfQuote]:
        """국내 ETF 복수 종목 일괄 조회.

        Args:
            tickers: 6자리 티커 리스트.
            rate_limit: True이면 KIS rate limit 대응 슬립 적용.

        Returns:
            {ticker: KrEtfQuote} 딕셔너리. 오류 종목은 제외.
        """
        result: dict[str, KrEtfQuote] = {}
        for ticker in tickers:
            try:
                result[ticker] = self.get_kr_etf(ticker)
                if rate_limit:
                    time.sleep(_RATE_LIMIT_SLEEP)
            except Exception as e:
                logger.warning("국내 ETF 시세 오류 [%s]: %s", ticker, e)
        return result

    def check_kr_discount_rate(
        self, ticker: str, threshold: float = 1.0
    ) -> tuple[float, bool]:
        """괴리율이 허용 범위 이내인지 확인.

        Args:
            ticker: 6자리 티커.
            threshold: 허용 괴리율 절댓값 (기본 1.0 = ±1%).

        Returns:
            (괴리율, 진입가능여부) 튜플.
        """
        quote = self.get_kr_etf(ticker)
        ok = abs(quote.discount_rate) <= threshold
        if not ok:
            logger.warning(
                "[%s] 괴리율 %.2f%% — 허용 범위(±%.1f%%) 초과. 진입 불가.",
                ticker, quote.discount_rate, threshold,
            )
        return quote.discount_rate, ok

    # ── 해외 ETF ────────────────────────────────────────────────

    def get_us_etf(self, ticker: str, exchange: str = "NAS") -> UsEtfQuote:
        """미국 ETF 현재가 조회.

        Args:
            ticker: 대문자 티커 (예: "QQQ").
            exchange: NAS(나스닥), NYS(뉴욕), AMS(아멕스).

        Returns:
            UsEtfQuote 데이터클래스.
        """
        url = f"{self._auth.base_url}/uapi/overseas-price/v1/quotations/price"
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": ticker,
        }
        resp = requests.get(
            url,
            headers=self._auth.get_headers(_TR_US_PRICE),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("rt_cd") != "0":
            raise ValueError(f"KIS API 오류 [{ticker}]: {data.get('msg1', '')}")

        out = data["output"]

        last = out.get("last", "") or "0"
        t_rate = out.get("t_rate", "") or "0"   # 거래대금(달러), rate 필드 부재 시 대체

        return UsEtfQuote(
            ticker=ticker,
            exchange=exchange,
            current_price=float(last),
            change_rate=float(out.get("rate", t_rate)),
            volume=int(out.get("pvol", out.get("tvol", 0)) or 0),
        )

    def get_us_etf_batch(
        self,
        tickers: list[str],
        exchange_map: dict[str, str] | None = None,
        rate_limit: bool = True,
    ) -> dict[str, UsEtfQuote]:
        """미국 ETF 복수 종목 일괄 조회.

        Args:
            tickers: 대문자 티커 리스트.
            exchange_map: {ticker: exchange} 매핑. 없으면 모두 NAS로 처리.
            rate_limit: True이면 rate limit 대응 슬립 적용.

        Returns:
            {ticker: UsEtfQuote} 딕셔너리. 오류 종목은 제외.
        """
        exchange_map = exchange_map or {}
        result: dict[str, UsEtfQuote] = {}
        for ticker in tickers:
            excd = exchange_map.get(ticker, "NAS")
            try:
                result[ticker] = self.get_us_etf(ticker, excd)
                if rate_limit:
                    time.sleep(_RATE_LIMIT_SLEEP)
            except Exception as e:
                logger.warning("미국 ETF 시세 오류 [%s]: %s", ticker, e)
        return result
