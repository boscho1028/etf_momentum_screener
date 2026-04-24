"""yfinance 기반 미국 ETF 모멘텀 스크리너."""
import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.utils.indicators import atr, is_uptrend, momentum_return, stop_loss_price

logger = logging.getLogger(__name__)

# 전략 상수
MIN_TRADING_VALUE_USD = 10_000_000  # $10M
MOMENTUM_1D_DAYS = 1
MOMENTUM_1W_DAYS = 5
MOMENTUM_1M_DAYS = 21
MOMENTUM_3M_DAYS = 63
SPY_200D_FILTER = True  # 시장 체제 필터 (SPY < 200일선이면 전체 중단)

_UNIVERSE_PATH = Path(__file__).parent.parent.parent / "data" / "us_etf_universe.csv"

# 기본 유니버스 (CSV 없을 때 폴백)
_DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "VTI", "VUG", "VTV",
    "TQQQ", "UPRO", "SOXL", "TECL",
    "SOXX", "SMH", "XLK",
    "XLE", "XLF", "XLV", "XLI", "XLU", "XLP", "XLRE",
    "GLD", "GDX", "GDXJ",
    "KWEB", "EWJ", "INDA", "EEM",
    "TLT", "SHY", "LQD", "HYG",
    "VNQ", "IYR",
]


def _load_universe() -> list[str]:
    if _UNIVERSE_PATH.exists():
        df = pd.read_csv(_UNIVERSE_PATH)
        return df["ticker"].dropna().str.upper().tolist()
    logger.info("us_etf_universe.csv 없음 — 기본 유니버스 %d개 사용", len(_DEFAULT_UNIVERSE))
    return _DEFAULT_UNIVERSE


def _check_spy_regime() -> bool:
    """SPY가 200일 이동평균 위에 있는지 확인 (시장 체제 필터).

    Returns:
        True = 매매 허용, False = 전체 중단.
    """
    spy = yf.Ticker("SPY").history(period="1y")
    if spy.empty or len(spy) < 200:
        logger.warning("SPY 데이터 부족 — 체제 필터 통과 처리")
        return True
    current = float(spy["Close"].iloc[-1])
    ma200 = spy["Close"].rolling(200).mean().iloc[-1]
    ok = current > ma200
    if not ok:
        logger.warning("SPY(%.2f) < 200일선(%.2f) — 매매 중단 신호", current, ma200)
    return ok


def fetch_us_ohlcv(ticker: str) -> pd.DataFrame:
    """미국 ETF OHLCV 조회 (약 6개월).

    Args:
        ticker: 대문자 티커 (예: "QQQ").

    Returns:
        yfinance 표준 OHLCV DataFrame.
    """
    data = yf.Ticker(ticker).history(period="6mo")
    if data is None or data.empty:
        return pd.DataFrame()
    return data.sort_index()


def screen_us_etfs(
    top_n: int = 20,
    min_1m_return: float = 0.0,
    min_3m_return: float = 0.0,
    skip_regime_filter: bool = False,
) -> pd.DataFrame:
    """미국 ETF 모멘텀 스크리닝.

    전략 원칙:
    - SPY 200일선 체제 필터 통과 시에만 실행
    - 1M & 3M 수익률 모두 양수
    - 5일 평균 거래대금 $10M 이상
    - 5일선 > 20일선 정배열

    Args:
        top_n: 반환할 상위 종목 수.
        min_1m_return: 최소 1개월 수익률 필터.
        min_3m_return: 최소 3개월 수익률 필터.
        skip_regime_filter: True이면 SPY 200일선 필터 건너뜀 (테스트용).

    Returns:
        컬럼: ticker, return_1m, return_3m, avg_volume_usd,
               current_price, atr14, stop_loss, momentum_score
    """
    if SPY_200D_FILTER and not skip_regime_filter:
        if not _check_spy_regime():
            logger.warning("시장 체제 필터: 전 종목 스크리닝 중단")
            return pd.DataFrame()

    universe = _load_universe()
    results = []

    for ticker in universe:
        try:
            df = fetch_us_ohlcv(ticker)
            if len(df) < 65:
                continue

            close = df["Close"]
            ret_1d = momentum_return(close, MOMENTUM_1D_DAYS)
            ret_1w = momentum_return(close, MOMENTUM_1W_DAYS)
            ret_1m = momentum_return(close, MOMENTUM_1M_DAYS)
            ret_3m = momentum_return(close, MOMENTUM_3M_DAYS)

            if pd.isna(ret_1m) or pd.isna(ret_3m):
                continue
            if ret_1m <= min_1m_return or ret_3m <= min_3m_return:
                continue

            # 거래대금: 종가 × 거래량
            avg_tv = (df["Close"] * df["Volume"]).tail(5).mean()
            if avg_tv < MIN_TRADING_VALUE_USD:
                continue

            if not is_uptrend(close, fast=5, slow=20):
                continue

            current_atr = atr(df).iloc[-1]
            current_price = float(close.iloc[-1])

            results.append(
                {
                    "ticker": ticker,
                    "return_1d": round(ret_1d, 4) if not pd.isna(ret_1d) else 0.0,
                    "return_1w": round(ret_1w, 4) if not pd.isna(ret_1w) else 0.0,
                    "return_1m": round(ret_1m, 4),
                    "return_3m": round(ret_3m, 4),
                    "avg_volume_usd": round(avg_tv),
                    "current_price": round(current_price, 2),
                    "atr14": round(current_atr, 4),
                    "stop_loss": round(stop_loss_price(current_price, current_atr), 2),
                }
            )
            logger.debug("통과: %s (1D=%.1f%% 1W=%.1f%% 1M=%.1f%% 3M=%.1f%%)",
                         ticker, ret_1d*100, ret_1w*100, ret_1m*100, ret_3m*100)

        except Exception as e:
            logger.warning("티커 %s 처리 오류: %s", ticker, e)
            continue

    result_df = pd.DataFrame(results)
    if result_df.empty:
        logger.warning("미국 ETF 스크리닝 결과 없음")
        return result_df

    result_df["momentum_score"] = (
        result_df["return_1w"] * 0.4
        + result_df["return_1m"] * 0.4
        + result_df["return_3m"] * 0.2
    )
    result_df = result_df.sort_values("momentum_score", ascending=False).head(top_n)
    result_df = result_df.reset_index(drop=True)

    logger.info("미국 ETF 스크리닝 완료: %d개 통과 → 상위 %d개 반환", len(results), len(result_df))
    return result_df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = screen_us_etfs(top_n=10)
    print(df.to_string(index=False))
