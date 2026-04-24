"""Turso(libSQL) 연결 및 ETF 스크리닝 데이터 저장."""
import logging
import os
from contextlib import contextmanager
from datetime import date
from typing import Generator

import libsql
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_DB_URL = os.getenv("TURSO_DATABASE_URL", "")
_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# DDL: ETF 스크리닝 테이블 3개
_DDL = """
CREATE TABLE IF NOT EXISTS etf_screen_kr (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_date TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    return_1m   REAL    NOT NULL,
    return_3m   REAL    NOT NULL,
    momentum_score REAL NOT NULL,
    avg_trading_value REAL,
    current_price REAL,
    atr14       REAL,
    stop_loss   REAL,
    created_at  TEXT    DEFAULT (datetime('now','localtime')),
    UNIQUE (screen_date, ticker)
);

CREATE TABLE IF NOT EXISTS etf_screen_us (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_date TEXT    NOT NULL,
    ticker      TEXT    NOT NULL,
    return_1m   REAL    NOT NULL,
    return_3m   REAL    NOT NULL,
    momentum_score REAL NOT NULL,
    avg_volume_usd REAL,
    current_price REAL,
    atr14       REAL,
    stop_loss   REAL,
    created_at  TEXT    DEFAULT (datetime('now','localtime')),
    UNIQUE (screen_date, ticker)
);

CREATE TABLE IF NOT EXISTS etf_screen_unified (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_date     TEXT    NOT NULL,
    theme           TEXT    NOT NULL,
    us_ticker       TEXT    NOT NULL,
    us_return_1m    REAL,
    us_return_3m    REAL,
    kr_ticker       TEXT    NOT NULL,
    kr_ticker_name  TEXT,
    kr_return_1m    REAL,
    kr_return_3m    REAL,
    match_score     REAL,
    created_at      TEXT    DEFAULT (datetime('now','localtime')),
    UNIQUE (screen_date, theme, kr_ticker)
);
"""


@contextmanager
def get_conn() -> Generator[libsql.Connection, None, None]:
    """Turso 연결 컨텍스트 매니저.

    Yields:
        libsql Connection 객체.
    """
    if not _DB_URL or not _AUTH_TOKEN:
        raise EnvironmentError("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not set in .env")
    conn = libsql.connect(database=_DB_URL, auth_token=_AUTH_TOKEN)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_tables() -> None:
    """ETF 스크리닝 테이블 생성 (없을 경우에만)."""
    with get_conn() as conn:
        conn.executescript(_DDL)
    logger.info("ETF 테이블 초기화 완료")


def save_kr_screen(df: pd.DataFrame, screen_date: date | None = None) -> int:
    """국내 ETF 스크리닝 결과 저장.

    Args:
        df: screen_kr_etfs() 반환 DataFrame.
        screen_date: 스크리닝 날짜 (기본 오늘).

    Returns:
        저장된 행 수.
    """
    if df.empty:
        return 0
    sd = (screen_date or date.today()).isoformat()

    rows = [
        (
            sd,
            row["ticker"],
            row["return_1m"],
            row["return_3m"],
            row["momentum_score"],
            row.get("avg_trading_value"),
            row.get("current_price"),
            row.get("atr14"),
            row.get("stop_loss"),
        )
        for _, row in df.iterrows()
    ]

    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO etf_screen_kr
               (screen_date, ticker, return_1m, return_3m, momentum_score,
                avg_trading_value, current_price, atr14, stop_loss)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger.info("국내 ETF %d건 저장 (date=%s)", len(rows), sd)
    return len(rows)


def save_us_screen(df: pd.DataFrame, screen_date: date | None = None) -> int:
    """미국 ETF 스크리닝 결과 저장.

    Args:
        df: screen_us_etfs() 반환 DataFrame.
        screen_date: 스크리닝 날짜 (기본 오늘).

    Returns:
        저장된 행 수.
    """
    if df.empty:
        return 0
    sd = (screen_date or date.today()).isoformat()

    rows = [
        (
            sd,
            row["ticker"],
            row["return_1m"],
            row["return_3m"],
            row["momentum_score"],
            row.get("avg_volume_usd"),
            row.get("current_price"),
            row.get("atr14"),
            row.get("stop_loss"),
        )
        for _, row in df.iterrows()
    ]

    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO etf_screen_us
               (screen_date, ticker, return_1m, return_3m, momentum_score,
                avg_volume_usd, current_price, atr14, stop_loss)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger.info("미국 ETF %d건 저장 (date=%s)", len(rows), sd)
    return len(rows)


def save_unified_screen(df: pd.DataFrame, screen_date: date | None = None) -> int:
    """한미 통합 매칭 결과 저장.

    Args:
        df: run_unified_screen() 반환 DataFrame.
        screen_date: 스크리닝 날짜 (기본 오늘).

    Returns:
        저장된 행 수.
    """
    if df.empty:
        return 0
    sd = (screen_date or date.today()).isoformat()

    rows = [
        (
            sd,
            row["theme"],
            row["us_ticker"],
            row.get("us_return_1m"),
            row.get("us_return_3m"),
            row["kr_ticker"],
            row.get("kr_ticker_name"),
            row.get("kr_return_1m"),
            row.get("kr_return_3m"),
            row.get("match_score"),
        )
        for _, row in df.iterrows()
    ]

    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO etf_screen_unified
               (screen_date, theme, us_ticker, us_return_1m, us_return_3m,
                kr_ticker, kr_ticker_name, kr_return_1m, kr_return_3m, match_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger.info("통합 매칭 %d건 저장 (date=%s)", len(rows), sd)
    return len(rows)


def load_unified_screen(screen_date: date | None = None) -> pd.DataFrame:
    """저장된 통합 매칭 결과 조회.

    Args:
        screen_date: 조회 날짜 (기본 오늘).

    Returns:
        etf_screen_unified 테이블 DataFrame.
    """
    sd = (screen_date or date.today()).isoformat()
    with get_conn() as conn:
        cursor = conn.execute(
            "SELECT * FROM etf_screen_unified WHERE screen_date = ? ORDER BY match_score DESC",
            (sd,),
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
    return pd.DataFrame(rows, columns=cols)
