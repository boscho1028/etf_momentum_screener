"""한미 ETF 통합 스크리너 — 테마 매칭 후 교집합 추출."""
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.db.turso import init_tables, save_kr_screen, save_unified_screen, save_us_screen
from src.screeners.kr_screener import screen_kr_etfs
from src.screeners.us_screener import screen_us_etfs

logger = logging.getLogger(__name__)

_MAPPING_PATH = Path(__file__).parent.parent.parent / "data" / "kr_us_mapping.csv"
_RESULTS_DIR = Path(__file__).parent.parent.parent / "data" / "results"
_RESULTS_DIR.mkdir(exist_ok=True)


def load_mapping() -> pd.DataFrame:
    """테마별 한미 ETF 매핑 테이블 로드.

    Returns:
        컬럼: theme, us_ticker, kr_ticker, kr_ticker_name, ...
    """
    if not _MAPPING_PATH.exists():
        raise FileNotFoundError(f"매핑 파일 없음: {_MAPPING_PATH}")
    df = pd.read_csv(_MAPPING_PATH, dtype={"kr_ticker": str})
    # kr_ticker를 6자리 0-패딩 문자열로 정규화
    df["kr_ticker"] = df["kr_ticker"].str.zfill(6)
    return df


def run_unified_screen(
    kr_top_n: int = 30,
    us_top_n: int = 30,
    discount_rate_penalty: float = 0.5,
) -> pd.DataFrame:
    """한미 동시 모멘텀 상위 테마 추출.

    매핑 테이블로 JOIN한 뒤 양쪽 모두 스크리닝 통과한 테마를 반환.
    매칭 스코어 = (한국 1M + 미국 1M) / 2 - 괴리율_페널티.

    Args:
        kr_top_n: 국내 스크리닝 상위 N개.
        us_top_n: 미국 스크리닝 상위 N개.
        discount_rate_penalty: 괴리율 1%당 스코어 차감 배수.

    Returns:
        컬럼: theme, us_ticker, us_return_1m, us_return_3m,
               kr_ticker, kr_ticker_name, kr_return_1m, kr_return_3m,
               match_score
    """
    logger.info("=== 국내 ETF 스크리닝 시작 ===")
    kr_df = screen_kr_etfs(top_n=kr_top_n)

    logger.info("=== 미국 ETF 스크리닝 시작 ===")
    us_df = screen_us_etfs(top_n=us_top_n)

    if kr_df.empty or us_df.empty:
        logger.warning("스크리닝 결과 부족 — 매칭 불가")
        return pd.DataFrame()

    mapping = load_mapping()

    # 미국 스크리닝 통과 티커와 매핑 JOIN (kr_* 컬럼은 kr_hit에서 가져오므로 제거)
    us_hit = mapping.drop(columns=["kr_ticker", "kr_ticker_name"]).merge(
        us_df[["ticker", "return_1d", "return_1w", "return_1m", "return_3m", "momentum_score"]].rename(
            columns={
                "ticker": "us_ticker",
                "return_1d": "us_return_1d",
                "return_1w": "us_return_1w",
                "return_1m": "us_return_1m",
                "return_3m": "us_return_3m",
                "momentum_score": "us_score",
            }
        ),
        on="us_ticker",
        how="inner",
    )

    # 국내 스크리닝 통과 티커와 매핑 JOIN
    kr_hit = mapping.merge(
        kr_df[["ticker", "return_1d", "return_1w", "return_1m", "return_3m", "momentum_score",
               "atr14", "stop_loss"]].rename(
            columns={
                "ticker": "kr_ticker",
                "return_1d": "kr_return_1d",
                "return_1w": "kr_return_1w",
                "return_1m": "kr_return_1m",
                "return_3m": "kr_return_3m",
                "momentum_score": "kr_score",
            }
        ),
        on="kr_ticker",
        how="inner",
    )

    # 양쪽 모두 통과한 테마만 선택
    matched = us_hit.merge(
        kr_hit[["theme", "kr_ticker", "kr_ticker_name",
                "kr_return_1d", "kr_return_1w", "kr_return_1m", "kr_return_3m",
                "kr_score", "atr14", "stop_loss"]],
        on="theme",
        how="inner",
    )

    if matched.empty:
        logger.info("한미 동시 통과 테마 없음")
        return matched

    # 매칭 스코어 계산
    # 1W/1M/3M 가중 (0.4/0.4/0.2), 한미 평균
    _w1w, _w1m, _w3m = 0.4, 0.4, 0.2
    matched["match_score"] = (
        (
            (matched["us_return_1w"] * _w1w + matched["us_return_1m"] * _w1m
             + matched["us_return_3m"] * _w3m)
            + (matched["kr_return_1w"] * _w1w + matched["kr_return_1m"] * _w1m
               + matched["kr_return_3m"] * _w3m)
        ) / 2
    ).round(4)

    # 카테고리별 정렬 우선순위: sector > international > reit > commodity > index > bond
    _CATEGORY_ORDER = {"sector": 0, "international": 1, "reit": 2,
                       "commodity": 3, "index": 4, "bond": 5}
    matched["_cat_order"] = matched["category"].map(_CATEGORY_ORDER).fillna(9)
    matched = matched.sort_values(["_cat_order", "match_score"], ascending=[True, False])

    output_cols = [
        "theme", "category",
        "us_ticker",
        "us_return_1d", "us_return_1w", "us_return_1m", "us_return_3m",
        "kr_ticker", "kr_ticker_name",
        "kr_return_1d", "kr_return_1w", "kr_return_1m", "kr_return_3m",
        "atr14", "stop_loss",
        "match_score",
    ]
    result = matched[output_cols].reset_index(drop=True)

    logger.info("=== 한미 매칭 결과: %d개 테마 ===", len(result))
    return result


def save_results(df: pd.DataFrame) -> Path:
    """결과를 날짜별 CSV로 저장.

    Args:
        df: run_unified_screen() 결과 DataFrame.

    Returns:
        저장된 파일 경로.
    """
    today = datetime.today().strftime("%Y%m%d")
    path = _RESULTS_DIR / f"{today}_unified_screen.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("결과 저장: %s", path)
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    init_tables()

    logger.info("=== 국내 ETF 스크리닝 ===")
    kr_df = screen_kr_etfs(top_n=30)
    save_kr_screen(kr_df)

    logger.info("=== 미국 ETF 스크리닝 ===")
    us_df = screen_us_etfs(top_n=30)
    save_us_screen(us_df)

    logger.info("=== 한미 통합 매칭 ===")
    result = run_unified_screen()

    if result.empty:
        print("한미 동시 모멘텀 상위 테마 없음")
    else:
        print("\n=== 한미 동시 모멘텀 상위 테마 ===")
        print(result.to_string(index=False))
        save_unified_screen(result)
        save_results(result)
