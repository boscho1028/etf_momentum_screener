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
    name        TEXT,
    return_1d   REAL,
    return_1w   REAL,
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
    name        TEXT,
    return_1d   REAL,
    return_1w   REAL,
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
    category        TEXT,
    us_ticker       TEXT    NOT NULL,
    us_return_1d    REAL,
    us_return_1w    REAL,
    us_return_1m    REAL,
    us_return_3m    REAL,
    kr_ticker       TEXT    NOT NULL,
    kr_ticker_name  TEXT,
    kr_return_1d    REAL,
    kr_return_1w    REAL,
    kr_return_1m    REAL,
    kr_return_3m    REAL,
    match_score     REAL,
    discount_rate   REAL,
    atr14           REAL,
    stop_loss       REAL,
    created_at      TEXT    DEFAULT (datetime('now','localtime')),
    UNIQUE (screen_date, theme, kr_ticker)
);
"""

# 기존 테이블에 추가될 수 있는 컬럼 (ALTER TABLE용)
_MIGRATIONS: list[tuple[str, str, str]] = [
    # (table, column, type)
    ("etf_screen_kr", "name", "TEXT"),
    ("etf_screen_kr", "return_1d", "REAL"),
    ("etf_screen_kr", "return_1w", "REAL"),
    ("etf_screen_us", "name", "TEXT"),
    ("etf_screen_us", "return_1d", "REAL"),
    ("etf_screen_us", "return_1w", "REAL"),
    ("etf_screen_unified", "category", "TEXT"),
    ("etf_screen_unified", "us_return_1d", "REAL"),
    ("etf_screen_unified", "us_return_1w", "REAL"),
    ("etf_screen_unified", "kr_return_1d", "REAL"),
    ("etf_screen_unified", "kr_return_1w", "REAL"),
    ("etf_screen_unified", "discount_rate", "REAL"),
    ("etf_screen_unified", "atr14", "REAL"),
    ("etf_screen_unified", "stop_loss", "REAL"),
]


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


def _existing_cols(conn: libsql.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def init_tables() -> None:
    """ETF 스크리닝 테이블 생성 + 구버전 스키마 자동 마이그레이션."""
    with get_conn() as conn:
        conn.executescript(_DDL)
        # 구버전 테이블에 누락 컬럼 추가 (ALTER TABLE은 IF NOT EXISTS 없음 → 직접 체크)
        for table, col, col_type in _MIGRATIONS:
            try:
                cols = _existing_cols(conn, table)
                if col not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                    logger.info("스키마 마이그레이션: %s.%s 추가", table, col)
            except Exception as e:
                logger.warning("마이그레이션 건너뜀 [%s.%s]: %s", table, col, e)
    logger.info("ETF 테이블 초기화 완료")


def save_kr_screen(df: pd.DataFrame, screen_date: date | None = None) -> int:
    """국내 ETF 스크리닝 결과 저장."""
    if df.empty:
        return 0
    sd = (screen_date or date.today()).isoformat()

    rows = [
        (
            sd,
            row["ticker"],
            row.get("name"),
            row.get("return_1d"),
            row.get("return_1w"),
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
               (screen_date, ticker, name, return_1d, return_1w,
                return_1m, return_3m, momentum_score,
                avg_trading_value, current_price, atr14, stop_loss)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger.info("국내 ETF %d건 저장 (date=%s)", len(rows), sd)
    return len(rows)


def save_us_screen(df: pd.DataFrame, screen_date: date | None = None) -> int:
    """미국 ETF 스크리닝 결과 저장."""
    if df.empty:
        return 0
    sd = (screen_date or date.today()).isoformat()

    rows = [
        (
            sd,
            row["ticker"],
            row.get("name"),
            row.get("return_1d"),
            row.get("return_1w"),
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
               (screen_date, ticker, name, return_1d, return_1w,
                return_1m, return_3m, momentum_score,
                avg_volume_usd, current_price, atr14, stop_loss)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    logger.info("미국 ETF %d건 저장 (date=%s)", len(rows), sd)
    return len(rows)


def save_unified_screen(df: pd.DataFrame, screen_date: date | None = None) -> int:
    """한미 통합 매칭 결과 저장."""
    if df.empty:
        return 0
    sd = (screen_date or date.today()).isoformat()

    rows = [
        (
            sd,
            row["theme"],
            row.get("category"),
            row["us_ticker"],
            row.get("us_return_1d"),
            row.get("us_return_1w"),
            row.get("us_return_1m"),
            row.get("us_return_3m"),
            row["kr_ticker"],
            row.get("kr_ticker_name"),
            row.get("kr_return_1d"),
            row.get("kr_return_1w"),
            row.get("kr_return_1m"),
            row.get("kr_return_3m"),
            row.get("match_score"),
            row.get("discount_rate"),
            row.get("atr14"),
            row.get("stop_loss"),
        )
        for _, row in df.iterrows()
    ]

    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO etf_screen_unified
               (screen_date, theme, category, us_ticker,
                us_return_1d, us_return_1w, us_return_1m, us_return_3m,
                kr_ticker, kr_ticker_name,
                kr_return_1d, kr_return_1w, kr_return_1m, kr_return_3m,
                match_score, discount_rate, atr14, stop_loss)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
