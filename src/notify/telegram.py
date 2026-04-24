"""텔레그램 채널 알림 — ETF 모멘텀 스크리너."""
import logging
import os
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
_API_BASE = f"https://api.telegram.org/bot{_BOT_TOKEN}"

_CATEGORY_LABEL = {
    "sector":        "🔥 주도 섹터",
    "international": "🌏 해외 시장",
    "reit":          "🏢 리츠",
    "commodity":     "🪙 원자재",
    "index":         "📈 지수",
    "bond":          "📋 채권",
}
_SECTOR_TOP_N = 5  # 섹터는 상위 5개만


def _send(text: str, parse_mode: str = "HTML") -> bool:
    if not _BOT_TOKEN or not _CHANNEL_ID:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID not set in .env")
    resp = requests.post(
        f"{_API_BASE}/sendMessage",
        json={
            "chat_id": _CHANNEL_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    if not resp.ok:
        logger.error("텔레그램 전송 실패: %s", resp.text)
        return False
    return True


def _fmt_ret(ret: float) -> str:
    arrow = "▲" if ret >= 0 else "▼"
    return f"{arrow}{ret * 100:.1f}%"


def _fmt_discount(rate: float) -> str:
    emoji = "🟢" if abs(rate) <= 0.3 else ("🟡" if abs(rate) <= 0.7 else "🔴")
    return f"{emoji}{rate:+.2f}%"


def _build_entry(rank: int, row: pd.Series) -> list[str]:
    """매칭 테마 1개의 메시지 라인 리스트 반환."""
    us_1d = row.get("us_return_1d") or 0
    us_1w = row.get("us_return_1w") or 0
    us_1m = row.get("us_return_1m") or 0
    us_3m = row.get("us_return_3m") or 0
    kr_1d = row.get("kr_return_1d") or 0
    kr_1w = row.get("kr_return_1w") or 0
    kr_1m = row.get("kr_return_1m") or 0
    kr_3m = row.get("kr_return_3m") or 0
    stop   = row.get("stop_loss")
    atr_v  = row.get("atr14")
    disc   = row.get("discount_rate")

    theme_label = row["theme"].replace("_", " ").upper()
    lines = [
        f"\n<b>{rank}. {theme_label}</b>",
        (f"🇺🇸 <code>{row['us_ticker']}</code>  "
         f"1D {_fmt_ret(us_1d)}  1W {_fmt_ret(us_1w)}  "
         f"1M {_fmt_ret(us_1m)}  3M {_fmt_ret(us_3m)}"),
        f"🇰🇷 <code>{row['kr_ticker']}</code> {row.get('kr_ticker_name', '')}",
        (f"     "
         f"1D {_fmt_ret(kr_1d)}  1W {_fmt_ret(kr_1w)}  "
         f"1M {_fmt_ret(kr_1m)}  3M {_fmt_ret(kr_3m)}"),
    ]
    if disc is not None:
        lines.append(f"     괴리율 {_fmt_discount(disc)}")
    if stop is not None and atr_v is not None:
        lines.append(f"     📍 손절선 {stop:,.0f}원  (ATR±{atr_v:,.0f})")
    return lines


def _build_solo_entry(rank: int, row: pd.Series, is_kr: bool) -> list[str]:
    """단독 ETF 1개의 메시지 라인 리스트 반환."""
    r_1d = row.get("return_1d") or 0
    r_1w = row.get("return_1w") or 0
    r_1m = row.get("return_1m") or 0
    r_3m = row.get("return_3m") or 0
    flag = "🇰🇷" if is_kr else "🇺🇸"
    name = row.get("name") or row.get("kr_ticker_name") or ""
    return [
        (f"\n{rank}. {flag} <code>{row['ticker']}</code> {name}".rstrip()),
        (f"     1D {_fmt_ret(r_1d)}  1W {_fmt_ret(r_1w)}  "
         f"1M {_fmt_ret(r_1m)}  3M {_fmt_ret(r_3m)}"),
    ]


def send_unified_result(
    result: pd.DataFrame,
    kr_df: pd.DataFrame,
    us_df: pd.DataFrame,
    kr_count: int,
    us_count: int,
    kr_solo_top_n: int = 10,
    us_solo_top_n: int = 10,
) -> bool:
    """한미 통합 스크리닝 결과 채널 전송.

    3개 섹션:
      1) 🔥 한미 동시 주도 (매핑 매칭, 섹터 top5)
      2) 🇰🇷 국내 단독 주도 (매핑 외 국내 모멘텀 통과)
      3) 🇺🇸 미국 단독 주도 (매핑 외 미국 모멘텀 통과)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sector_count = len(result[result.get("category", "") == "sector"]) if not result.empty else 0

    lines = [
        f"📊 <b>ETF 모멘텀 스크리너</b>  |  {now}",
        "",
        f"국내 통과 {kr_count}종목  ·  미국 통과 {us_count}종목",
        f"한미 동시 모멘텀  섹터 <b>{sector_count}개</b>  |  전체 <b>{len(result)}개</b> 테마",
        "─" * 30,
    ]

    # ── 1. 한미 동시 주도 ───────────────────────────
    lines.append("\n<b>🔥 한미 동시 주도</b>")
    if result.empty:
        lines.append("\n⚠️ 매칭 테마 없음 (모멘텀/SPY 필터 미달)")
    else:
        category_order = ["sector", "international", "reit", "commodity", "index", "bond"]
        rank = 1
        for cat in category_order:
            cat_df = result[result.get("category", pd.Series(dtype=str)) == cat]
            if cat_df.empty:
                continue
            if cat == "sector":
                cat_df = cat_df.head(_SECTOR_TOP_N)
            label = _CATEGORY_LABEL.get(cat, cat.upper())
            lines.append(f"\n<b>{label}</b>")
            for _, row in cat_df.iterrows():
                lines.extend(_build_entry(rank, row))
                rank += 1

    # 매칭된 티커 집합 (단독 섹션에서 제외)
    matched_kr = set(result["kr_ticker"].astype(str)) if not result.empty else set()
    matched_us = set(result["us_ticker"].astype(str)) if not result.empty else set()

    # ── 2. 국내 단독 주도 ────────────────────────────
    if kr_df is not None and not kr_df.empty:
        kr_solo = kr_df[~kr_df["ticker"].astype(str).isin(matched_kr)].head(kr_solo_top_n)
        if not kr_solo.empty:
            lines.append("\n" + "─" * 30)
            lines.append(f"\n<b>🇰🇷 국내 단독 주도</b>  (매칭 外 top {len(kr_solo)})")
            for i, (_, row) in enumerate(kr_solo.iterrows(), 1):
                lines.extend(_build_solo_entry(i, row, is_kr=True))

    # ── 3. 미국 단독 주도 ────────────────────────────
    if us_df is not None and not us_df.empty:
        us_solo = us_df[~us_df["ticker"].astype(str).isin(matched_us)].head(us_solo_top_n)
        if not us_solo.empty:
            lines.append("\n" + "─" * 30)
            lines.append(f"\n<b>🇺🇸 미국 단독 주도</b>  (매칭 外 top {len(us_solo)})")
            for i, (_, row) in enumerate(us_solo.iterrows(), 1):
                lines.extend(_build_solo_entry(i, row, is_kr=False))

    lines.append("")
    ok = _send("\n".join(lines))
    if ok:
        logger.info("텔레그램 전송 완료 (매칭 %d개)", len(result))
    return ok


def send_error(message: str) -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return _send(
        f"🚨 <b>ETF 스크리너 오류</b>  |  {now}\n\n<code>{message}</code>"
    )


def send_test() -> bool:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return _send(f"✅ <b>ETF 모멘텀 스크리너</b>\n\n채널 연결 테스트 성공\n{now}")
