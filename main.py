"""ETF 모멘텀 스크리너 — 메인 실행 진입점.

매일 아침 스케줄러로 실행:
    1. 한미 ETF 스크리닝
    2. 괴리율 실시간 확인 (KIS API)
    3. Turso DB 저장
    4. 텔레그램 채널 전송
"""
import logging
import sys
import traceback
from datetime import datetime

import pandas as pd

# Windows 콘솔 cp949 한계 회피 — UTF-8로 재설정
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.data.fund_flow import (
    collect_aum_snapshots,
    compute_net_flow,
    latest_flow_summary,
)
from src.db.turso import (
    init_tables,
    load_aum_history,
    save_aum_snapshots,
    save_kr_screen,
    save_unified_screen,
    save_us_screen,
)
from src.kis.auth import KisAuth
from src.kis.quote import KisQuote
from src.notify.chart import build_returns_chart
from src.notify.telegram import send_error, send_photo, send_unified_result
from src.screeners.kr_screener import screen_kr_etfs
from src.screeners.unified import load_mapping, run_unified_screen
from src.screeners.us_screener import screen_us_etfs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/screener.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# 주요 미국 ETF 이름 (정적)
_US_ETF_NAMES: dict[str, str] = {
    "SPY": "SPDR S&P 500", "QQQ": "Invesco QQQ (NASDAQ 100)",
    "IWM": "iShares Russell 2000", "VTI": "Vanguard Total Stock Market",
    "VUG": "Vanguard Growth", "VTV": "Vanguard Value",
    "TQQQ": "ProShares UltraPro QQQ (3x)", "UPRO": "ProShares UltraPro S&P500 (3x)",
    "SOXL": "Direxion Semi Bull 3x", "TECL": "Direxion Technology Bull 3x",
    "SOXX": "iShares Semiconductor", "SMH": "VanEck Semiconductor",
    "XLK": "Tech Select Sector", "XLE": "Energy Select", "XLF": "Financials Select",
    "XLV": "Health Care Select", "XLI": "Industrials Select", "XLU": "Utilities Select",
    "XLP": "Consumer Staples Select", "XLRE": "Real Estate Select",
    "XLY": "Consumer Discretionary Select", "XLB": "Materials Select",
    "XBI": "SPDR Biotech", "ICLN": "iShares Clean Energy",
    "ITA": "iShares Aerospace & Defense", "LIT": "Global X Lithium",
    "GLD": "SPDR Gold Trust", "GDX": "VanEck Gold Miners",
    "GDXJ": "VanEck Junior Gold Miners",
    "KWEB": "KraneShares CSI China Internet", "EWJ": "iShares MSCI Japan",
    "INDA": "iShares MSCI India", "EEM": "iShares MSCI Emerging Markets",
    "TLT": "iShares 20+ Year Treasury", "SHY": "iShares 1-3 Year Treasury",
    "LQD": "iShares Investment Grade Corp Bond", "HYG": "iShares High Yield Corp Bond",
    "VNQ": "Vanguard REIT", "IYR": "iShares US Real Estate",
}


def _build_kr_name_map(tickers: list[str], quote: KisQuote) -> dict[str, str]:
    """KR 티커 → 이름 맵 생성.

    mapping.csv 먼저, 모자란 건 KIS API로 보강.
    """
    import pandas as pd
    from pathlib import Path
    name_map: dict[str, str] = {}
    mp = Path(__file__).parent / "data" / "kr_us_mapping.csv"
    if mp.exists():
        mdf = pd.read_csv(mp, dtype={"kr_ticker": str})
        for _, r in mdf.iterrows():
            t = str(r["kr_ticker"]).zfill(6)
            if t and pd.notna(r.get("kr_ticker_name")):
                name_map[t] = r["kr_ticker_name"]
    for t in tickers:
        t = str(t).zfill(6)
        if t in name_map:
            continue
        try:
            q = quote.get_kr_etf(t)
            name_map[t] = q.name or ""
        except Exception as e:
            logger.warning("이름 조회 실패 [%s]: %s", t, e)
            name_map[t] = ""
    return name_map


def _enrich_with_discount_rate(
    result_df, quote: KisQuote
):
    """통합 결과에 실시간 괴리율 + 손절선 추가.

    KIS API로 국내 ETF 괴리율을 조회해서 result_df에 컬럼 추가.
    API 오류 시 해당 종목만 None 처리하고 계속 진행.
    """
    import pandas as pd

    discount_rates = []
    stop_losses = []
    atrs = []

    for _, row in result_df.iterrows():
        kr_ticker = str(row["kr_ticker"]).zfill(6)
        try:
            q = quote.get_kr_etf(kr_ticker)
            discount_rates.append(q.discount_rate)

            # 손절선/ATR은 kr_screener 결과에서 가져옴 (이미 계산됨)
            stop_losses.append(row.get("stop_loss"))
            atrs.append(row.get("atr14"))
        except Exception as e:
            logger.warning("괴리율 조회 실패 [%s]: %s", kr_ticker, e)
            discount_rates.append(None)
            stop_losses.append(None)
            atrs.append(None)

    result_df = result_df.copy()
    result_df["discount_rate"] = discount_rates
    result_df["stop_loss"] = stop_losses
    result_df["atr14"] = atrs
    return result_df


def run() -> int:
    """스크리닝 전체 플로우 실행.

    Returns:
        0: 성공, 1: 오류
    """
    logger.info("=" * 50)
    logger.info("ETF 모멘텀 스크리너 시작 %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 50)

    try:
        # ── 0. DB 테이블 초기화 ──────────────────────────
        init_tables()

        # ── 1. 스크리닝 ──────────────────────────────────
        logger.info("국내 ETF 스크리닝...")
        kr_df = screen_kr_etfs(top_n=30)
        logger.info("국내 통과: %d종목", len(kr_df))

        logger.info("미국 ETF 스크리닝...")
        us_df = screen_us_etfs(top_n=30)
        logger.info("미국 통과: %d종목", len(us_df))

        # ── 2. 한미 매칭 ─────────────────────────────────
        logger.info("한미 매칭 중...")
        result = run_unified_screen(kr_top_n=30, us_top_n=30)
        logger.info("매칭 결과: %d개 테마", len(result))

        # ── 3. 이름 + 실시간 괴리율 보강 ───────────────────
        auth = KisAuth()
        quote = KisQuote(auth)

        # KR 이름 맵 (mapping + KIS 보강)
        if not kr_df.empty and "ticker" in kr_df.columns:
            kr_name_map = _build_kr_name_map(kr_df["ticker"].astype(str).tolist(), quote)
            kr_df = kr_df.copy()
            kr_df["name"] = (
                kr_df["ticker"].astype(str).str.zfill(6).map(kr_name_map).fillna("")
            )

        # US 이름 맵 (정적 + 미매핑은 티커 그대로)
        if not us_df.empty and "ticker" in us_df.columns:
            us_df = us_df.copy()
            us_df["name"] = us_df["ticker"].map(_US_ETF_NAMES).fillna("")

        if not result.empty:
            logger.info("KIS API 괴리율 조회...")
            result = _enrich_with_discount_rate(result, quote)

            # 괴리율 초과 종목 경고 로깅
            for _, row in result.iterrows():
                dr = row.get("discount_rate")
                if dr is not None and abs(dr) > 1.0:
                    logger.warning(
                        "괴리율 초과 [%s] %.2f%% — 진입 주의",
                        row["kr_ticker"], dr
                    )

        # ── 4. DB 저장 ───────────────────────────────────
        logger.info("Turso DB 저장...")
        save_kr_screen(kr_df)
        save_us_screen(us_df)
        save_unified_screen(result)

        # ── 4-2. AUM 스냅샷 수집 (자금유입 추적용) ───────
        try:
            kr_tk = kr_df["ticker"].astype(str).tolist() if not kr_df.empty else []
            us_tk = us_df["ticker"].astype(str).tolist() if not us_df.empty else []
            snaps = collect_aum_snapshots(kr_tk, us_tk, auth=auth)
            save_aum_snapshots(snaps)

            # 히스토리 로드 + net flow 계산
            hist = pd.concat(
                [
                    load_aum_history("KR", kr_tk, days=30),
                    load_aum_history("US", us_tk, days=30),
                ],
                ignore_index=True,
            ) if (kr_tk or us_tk) else pd.DataFrame()
            flow_df = compute_net_flow(hist) if not hist.empty else pd.DataFrame()
            flow_summary = latest_flow_summary(flow_df) if not flow_df.empty else pd.DataFrame()

            # screen 결과 + 매칭 결과에 자금유입 신호 병합
            if not flow_summary.empty:
                kr_flow = flow_summary[flow_summary["market"] == "KR"].set_index("ticker")
                us_flow = flow_summary[flow_summary["market"] == "US"].set_index("ticker")
                if not kr_df.empty:
                    kr_df["flow_signal"] = kr_df["ticker"].astype(str).map(kr_flow["flow_signal"])
                    kr_df["net_flow_5d"] = kr_df["ticker"].astype(str).map(kr_flow["net_flow_5d"])
                    kr_df["days_collected"] = kr_df["ticker"].astype(str).map(kr_flow["days_collected"])
                if not us_df.empty:
                    us_df["flow_signal"] = us_df["ticker"].map(us_flow["flow_signal"])
                    us_df["net_flow_5d"] = us_df["ticker"].map(us_flow["net_flow_5d"])
                    us_df["days_collected"] = us_df["ticker"].map(us_flow["days_collected"])
                # 매칭 결과에도 us_/kr_ 접두로 병합
                if not result.empty:
                    result["us_flow_signal"] = result["us_ticker"].map(us_flow["flow_signal"])
                    result["us_net_flow_5d"] = result["us_ticker"].map(us_flow["net_flow_5d"])
                    result["kr_flow_signal"] = result["kr_ticker"].astype(str).map(kr_flow["flow_signal"])
                    result["kr_net_flow_5d"] = result["kr_ticker"].astype(str).map(kr_flow["net_flow_5d"])
        except Exception as e:
            logger.warning("AUM 스냅샷 처리 실패: %s", e)
            import traceback
            logger.debug(traceback.format_exc())

        # ── 5. 시각화 차트 생성 + 전송 ───────────────────
        try:
            from pathlib import Path
            chart_path = Path("data/results") / f"{datetime.now():%Y%m%d}_chart.png"
            build_returns_chart(kr_df, us_df, result, chart_path, top_n=12)
            send_photo(str(chart_path), caption="📊 ETF 모멘텀 스크리너 차트")
        except Exception as e:
            logger.warning("차트 생성/전송 실패: %s", e)

        # ── 6. 텔레그램 텍스트 결과 전송 ─────────────────
        logger.info("텔레그램 채널 전송...")
        send_unified_result(
            result,
            kr_df=kr_df,
            us_df=us_df,
            kr_count=len(kr_df),
            us_count=len(us_df),
        )

        logger.info("완료")
        return 0

    except Exception as e:
        msg = traceback.format_exc()
        logger.error("치명적 오류:\n%s", msg)
        try:
            send_error(str(e))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)
    sys.exit(run())
