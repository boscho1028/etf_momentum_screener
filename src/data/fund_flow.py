"""ETF 순자금 유입(net flow) 수집 + 계산 모듈.

전략:
    net_flow_t = (shares_out_t - shares_out_{t-1}) * NAV_t

데이터 소스:
    - KR: KIS API inquire-price (etf_crcl_stcn=유통좌수, nav, etf_crcl_ntas_ttam=AUM)
    - US: yfinance Ticker.fast_info (totalAssets, sharesOutstanding) — 폴백 .info
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd
import requests
import yfinance as yf

from src.kis.auth import KisAuth

logger = logging.getLogger(__name__)

_KIS_TR_KR = "FHPST02400000"


def fetch_kr_aum(ticker: str, auth: KisAuth) -> dict | None:
    """KR ETF AUM 스냅샷 (KIS API).

    Returns:
        {ticker, nav, shares_out, aum, trading_value} or None.
    """
    try:
        resp = requests.get(
            f"{auth.base_url}/uapi/etfetn/v1/quotations/inquire-price",
            headers=auth.get_headers(_KIS_TR_KR),
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            logger.warning("KIS AUM 응답 오류 [%s]: %s", ticker, data.get("msg1"))
            return None
        out = data["output"]
        nav = float(out.get("nav") or 0)
        shares = float(out.get("etf_crcl_stcn") or out.get("lstn_stcn") or 0)
        aum = float(out.get("etf_crcl_ntas_ttam") or 0)
        if aum == 0 and nav and shares:
            aum = nav * shares  # fallback
        return {
            "market": "KR",
            "ticker": ticker,
            "nav": nav,
            "shares_out": shares,
            "aum": aum,
            "trading_value": float(out.get("acml_tr_pbmn") or 0),
        }
    except Exception as e:
        logger.warning("KR AUM 조회 실패 [%s]: %s", ticker, e)
        return None


def fetch_us_aum(ticker: str) -> dict | None:
    """US ETF AUM 스냅샷 (yfinance).

    Returns:
        {ticker, nav, shares_out, aum, trading_value} or None.
    """
    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = dict(t.fast_info)
        except Exception:
            pass
        # fast_info가 부족하면 .info 폴백
        total_assets = info.get("totalAssets") or info.get("total_assets") or 0
        shares = info.get("shares") or info.get("sharesOutstanding") or 0
        last_price = info.get("lastPrice") or info.get("last_price") or 0

        if not total_assets or not shares:
            full = t.info or {}
            total_assets = total_assets or full.get("totalAssets") or 0
            shares = shares or full.get("sharesOutstanding") or 0
            last_price = last_price or full.get("regularMarketPrice") or 0

        # NAV proxy: yfinance는 NAV 미제공 → totalAssets / shares
        nav = (total_assets / shares) if (total_assets and shares) else last_price

        # 일거래대금: 최근 종가 × 거래량
        try:
            hist = t.history(period="2d")
            if not hist.empty:
                last_row = hist.iloc[-1]
                trading_value = float(last_row["Close"] * last_row["Volume"])
            else:
                trading_value = 0.0
        except Exception:
            trading_value = 0.0

        return {
            "market": "US",
            "ticker": ticker,
            "nav": float(nav),
            "shares_out": float(shares),
            "aum": float(total_assets),
            "trading_value": trading_value,
        }
    except Exception as e:
        logger.warning("US AUM 조회 실패 [%s]: %s", ticker, e)
        return None


def collect_aum_snapshots(
    kr_tickers: Iterable[str],
    us_tickers: Iterable[str],
    auth: KisAuth | None = None,
    rate_limit_sec: float = 0.05,
) -> list[dict]:
    """KR + US 일괄 AUM 스냅샷 수집."""
    auth = auth or KisAuth()
    rows: list[dict] = []

    for t in kr_tickers:
        r = fetch_kr_aum(str(t).zfill(6), auth)
        if r:
            rows.append(r)
        time.sleep(rate_limit_sec)

    for t in us_tickers:
        r = fetch_us_aum(t)
        if r:
            rows.append(r)

    logger.info("AUM 스냅샷 수집: KR %d, US %d",
                sum(1 for r in rows if r["market"] == "KR"),
                sum(1 for r in rows if r["market"] == "US"))
    return rows


def compute_net_flow(history_df: pd.DataFrame) -> pd.DataFrame:
    """AUM 히스토리에서 일별 순자금 유입 계산.

    net_flow = (shares_out_t - shares_out_{t-1}) * nav_t

    Args:
        history_df: load_aum_history() 결과.

    Returns:
        DataFrame with columns: market, ticker, snapshot_date, net_flow,
                                  net_flow_5d, net_flow_20d, tv_ratio_5_20.
    """
    if history_df.empty:
        return pd.DataFrame()

    df = history_df.copy()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df = df.sort_values(["market", "ticker", "snapshot_date"])

    df["d_shares"] = df.groupby(["market", "ticker"])["shares_out"].diff()
    df["net_flow"] = df["d_shares"] * df["nav"]

    # 5일 / 20일 누적 net flow
    g = df.groupby(["market", "ticker"])
    df["net_flow_5d"] = g["net_flow"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    df["net_flow_20d"] = g["net_flow"].transform(lambda s: s.rolling(20, min_periods=1).sum())

    # 거래대금 5/20 비율 (자금 관심도 proxy — 히스토리 짧을 때 사용)
    df["tv_5d"] = g["trading_value"].transform(lambda s: s.rolling(5, min_periods=1).mean())
    df["tv_20d"] = g["trading_value"].transform(lambda s: s.rolling(20, min_periods=1).mean())
    df["tv_ratio_5_20"] = df["tv_5d"] / df["tv_20d"].replace(0, pd.NA)

    return df


def latest_flow_summary(flow_df: pd.DataFrame) -> pd.DataFrame:
    """티커별 최신 자금유입 요약.

    Returns:
        DataFrame: market, ticker, days_collected, net_flow_5d, net_flow_20d,
                   tv_ratio_5_20, flow_signal.
    """
    if flow_df.empty:
        return pd.DataFrame()

    last = flow_df.groupby(["market", "ticker"]).tail(1).copy()
    counts = flow_df.groupby(["market", "ticker"]).size().rename("days_collected")
    last = last.merge(counts, on=["market", "ticker"], how="left")

    def _signal(row: pd.Series) -> str:
        nf = row.get("net_flow_5d")
        tv = row.get("tv_ratio_5_20")
        if pd.notna(nf) and nf != 0:
            return "INFLOW" if nf > 0 else "OUTFLOW"
        if pd.notna(tv):
            return "INTEREST_UP" if tv > 1.2 else ("INTEREST_DOWN" if tv < 0.8 else "FLAT")
        return "N/A"

    last["flow_signal"] = last.apply(_signal, axis=1)
    return last[
        [
            "market", "ticker", "days_collected",
            "net_flow_5d", "net_flow_20d", "tv_ratio_5_20", "flow_signal",
        ]
    ].reset_index(drop=True)
