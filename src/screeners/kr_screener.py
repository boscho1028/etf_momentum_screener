"""pykrx 기반 국내 ETF 모멘텀 스크리너."""
import concurrent.futures
import contextlib
import logging
import os
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# pykrx import 전 글로벌 socket 타임아웃 — TCP 레벨 hang 방지
socket.setdefaulttimeout(15)

_FETCH_TIMEOUT_SEC = 12  # fetch_ohlcv 호출 한 건의 hard timeout

# pykrx import 시점에 stdout으로 토하는 KRX 안내문 차단
_devnull_for_import = open(os.devnull, "w", encoding="utf-8", errors="ignore")
_saved_stdout, _saved_stderr = sys.stdout, sys.stderr
try:
    sys.stdout = _devnull_for_import
    sys.stderr = _devnull_for_import
    from pykrx import stock
finally:
    sys.stdout = _saved_stdout
    sys.stderr = _saved_stderr
    _devnull_for_import.close()

from src.utils.indicators import atr, is_uptrend, momentum_return, stop_loss_price

logger = logging.getLogger(__name__)

# pykrx 내부 로거의 traceback/print 차단
logging.getLogger("pykrx").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)


@contextlib.contextmanager
def _suppress_stderr():
    """KRX 차단 시 pykrx가 stderr/stdout/logging으로 토하는 traceback 차단."""
    devnull = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    old_err, old_out = sys.stderr, sys.stdout
    old_disable = logging.root.manager.disable

    saved_streams: list[tuple[logging.Handler, object]] = []
    for h in logging.root.handlers + [
        h for lg in logging.Logger.manager.loggerDict.values()
        if isinstance(lg, logging.Logger) for h in lg.handlers
    ]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            saved_streams.append((h, h.stream))
            h.stream = devnull

    try:
        sys.stderr = devnull
        sys.stdout = devnull
        logging.disable(logging.CRITICAL)
        yield
    finally:
        sys.stderr = old_err
        sys.stdout = old_out
        logging.disable(old_disable)
        for h, s in saved_streams:
            h.stream = s
        devnull.close()

_UNIVERSE_FALLBACK_PATH = (
    Path(__file__).parent.parent.parent / "data" / "kr_etf_universe_fallback.csv"
)
_MAPPING_PATH = (
    Path(__file__).parent.parent.parent / "data" / "kr_us_mapping.csv"
)

# 전략 상수
MIN_TRADING_VALUE_100M = 100_0000_0000  # 100억원
MOMENTUM_1D_DAYS = 1
MOMENTUM_1W_DAYS = 5
MOMENTUM_1M_DAYS = 21
MOMENTUM_3M_DAYS = 63
MIN_PRICE_HISTORY_DAYS = 70  # 최소 필요 거래일


def _date_str(days_ago: int = 0) -> str:
    return (datetime.today() - timedelta(days=days_ago)).strftime("%Y%m%d")


def _load_fallback_universe() -> list[str]:
    """정적 유니버스 CSV + 매핑 CSV의 KR 티커 병합.

    KRX 로그인 차단 시 pykrx 유니버스 조회 불가 → 정적 리스트 사용.
    """
    tickers: set[str] = set()
    if _UNIVERSE_FALLBACK_PATH.exists():
        df = pd.read_csv(_UNIVERSE_FALLBACK_PATH, dtype={"ticker": str})
        tickers.update(df["ticker"].dropna().str.zfill(6).tolist())
    if _MAPPING_PATH.exists():
        df = pd.read_csv(_MAPPING_PATH, dtype={"kr_ticker": str})
        tickers.update(df["kr_ticker"].dropna().str.zfill(6).tolist())
    return sorted(tickers)


def fetch_kr_etf_universe() -> list[str]:
    """국내 ETF 티커 리스트.

    pykrx 시도 → 실패 시 정적 fallback CSV 사용.
    KRX가 2025~2026년경 data.krx.co.kr을 로그인 필수로 전환해서
    `get_etf_ticker_list` 가 빈 결과를 반환하는 환경에서의 대비.

    Returns:
        6자리 문자열 티커 리스트.
    """
    try:
        with _suppress_stderr():
            tickers = stock.get_etf_ticker_list(date=_date_str())
        if tickers:
            logger.info("국내 ETF 전종목 (pykrx): %d개", len(tickers))
            return tickers
    except Exception as e:
        logger.warning("pykrx 유니버스 조회 실패, fallback CSV 사용 (%s)", str(e)[:80])

    tickers = _load_fallback_universe()
    logger.info("국내 ETF 유니버스 (fallback CSV): %d개", len(tickers))
    return tickers


def fetch_ohlcv(ticker: str, days: int = 100) -> pd.DataFrame:
    """ETF OHLCV 조회.

    `get_market_ohlcv_by_date`를 사용 (ETF도 정상 동작, 로그인 불필요).

    Args:
        ticker: 6자리 ETF 티커.
        days: 조회 거래일 기준 달력일 (여유분 포함).

    Returns:
        컬럼: Open, High, Low, Close, Volume, TradingValue DataFrame.
    """
    from_date = _date_str(days_ago=days * 2)
    to_date = _date_str()
    df = stock.get_market_ohlcv_by_date(from_date, to_date, ticker)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(
        columns={
            "시가": "Open",
            "고가": "High",
            "저가": "Low",
            "종가": "Close",
            "거래량": "Volume",
            "거래대금": "TradingValue",
            "등락률": "ChangePct",
        }
    )
    # get_market_ohlcv_by_date는 거래대금 미제공 — Close*Volume 근사
    if "TradingValue" not in df.columns:
        df["TradingValue"] = df["Close"] * df["Volume"]
    return df.sort_index()


def screen_kr_etfs(
    top_n: int = 20,
    min_1m_return: float = 0.0,
    min_3m_return: float = 0.0,
) -> pd.DataFrame:
    """국내 ETF 모멘텀 스크리닝.

    전략 원칙:
    - 1M & 3M 수익률 모두 양수
    - 거래대금 100억원 이상 (5일 평균)
    - 5일선 > 20일선 정배열

    Args:
        top_n: 반환할 상위 종목 수.
        min_1m_return: 최소 1개월 수익률 필터 (기본 0 = 양수).
        min_3m_return: 최소 3개월 수익률 필터 (기본 0 = 양수).

    Returns:
        컬럼: ticker, name, return_1m, return_3m, avg_trading_value,
               current_price, atr14, stop_loss
    """
    tickers = fetch_kr_etf_universe()
    results = []
    total = len(tickers)

    # 단일 워커 ThreadPool로 fetch_ohlcv 호출별 hard timeout 강제
    # (KRX 서버가 특정 티커에 대해 응답을 안 주고 hang 거는 케이스 대응)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        for idx, ticker in enumerate(tickers, 1):
            if idx % 25 == 0 or idx == 1 or idx == total:
                logger.info("KR 진행: %d/%d (%d개 통과)", idx, total, len(results))
            try:
                future = ex.submit(fetch_ohlcv, ticker, 100)
                try:
                    df = future.result(timeout=_FETCH_TIMEOUT_SEC)
                except concurrent.futures.TimeoutError:
                    logger.warning("티커 %s 타임아웃 (%ds) — 건너뜀", ticker, _FETCH_TIMEOUT_SEC)
                    continue

                if len(df) < MIN_PRICE_HISTORY_DAYS:
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

                avg_tv = df["TradingValue"].tail(5).mean()
                if avg_tv < MIN_TRADING_VALUE_100M:
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
                        "avg_trading_value": round(avg_tv),
                        "current_price": current_price,
                        "atr14": round(current_atr, 2),
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
        logger.warning("스크리닝 결과 없음")
        return result_df

    # 최근 기간 가중 (1W 0.4, 1M 0.4, 3M 0.2)
    result_df["momentum_score"] = (
        result_df["return_1w"] * 0.4
        + result_df["return_1m"] * 0.4
        + result_df["return_3m"] * 0.2
    )
    result_df = result_df.sort_values("momentum_score", ascending=False).head(top_n)
    result_df = result_df.reset_index(drop=True)

    logger.info("국내 ETF 스크리닝 완료: %d개 통과 → 상위 %d개 반환", len(results), len(result_df))
    return result_df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    df = screen_kr_etfs(top_n=10)
    print(df.to_string(index=False))
