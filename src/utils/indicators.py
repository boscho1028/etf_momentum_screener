"""공통 기술 지표 계산 유틸리티."""
import numpy as np
import pandas as pd


def atr(ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range 계산.

    Args:
        ohlcv: 컬럼 'High', 'Low', 'Close' 포함 DataFrame.
        period: ATR 기간 (기본 14).

    Returns:
        ATR Series (ohlcv와 동일 인덱스).
    """
    high = ohlcv["High"]
    low = ohlcv["Low"]
    prev_close = ohlcv["Close"].shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    return tr.ewm(span=period, adjust=False).mean()


def momentum_return(close: pd.Series, days: int) -> float:
    """단순 수익률 계산 (최근 n거래일).

    Args:
        close: 종가 Series (날짜 오름차순 정렬).
        days: 조회 거래일 수.

    Returns:
        수익률 (0.1 = 10%).
    """
    if len(close) < days + 1:
        return float("nan")
    end = close.iloc[-1]
    start = close.iloc[-(days + 1)]
    if start == 0:
        return float("nan")
    return (end - start) / start


def sma(close: pd.Series, period: int) -> pd.Series:
    """단순 이동평균.

    Args:
        close: 종가 Series.
        period: 이동평균 기간.

    Returns:
        SMA Series.
    """
    return close.rolling(window=period).mean()


def is_uptrend(close: pd.Series, fast: int = 5, slow: int = 20) -> bool:
    """단기선이 장기선 위에 있는지 (정배열 여부).

    Args:
        close: 종가 Series (최소 slow+1 길이).
        fast: 단기 이동평균 기간.
        slow: 장기 이동평균 기간.

    Returns:
        정배열이면 True.
    """
    if len(close) < slow:
        return False
    fast_val = close.rolling(fast).mean().iloc[-1]
    slow_val = close.rolling(slow).mean().iloc[-1]
    return bool(fast_val > slow_val)


def stop_loss_price(entry: float, current_atr: float, multiplier: float = 2.0) -> float:
    """ATR 기반 손절가 계산.

    Args:
        entry: 진입가.
        current_atr: 진입 시점 ATR 값.
        multiplier: ATR 배수 (기본 2.0).

    Returns:
        손절가.
    """
    return entry - multiplier * current_atr
