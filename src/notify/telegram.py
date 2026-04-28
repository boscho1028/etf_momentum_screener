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
_SECTOR_TOP_N = 5     # 섹터는 상위 5개만
_MATCHED_TOP_N = 3    # 한미 동시 매칭 테마는 전체 상위 N개만


def send_photo(image_path: str, caption: str = "") -> bool:
    """텔레그램 채널에 이미지 전송.

    Args:
        image_path: PNG 등 이미지 파일 경로.
        caption: 이미지 캡션 (HTML 허용, 1024자 제한).

    Returns:
        전송 성공 여부.
    """
    if not _BOT_TOKEN or not _CHANNEL_ID:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID not set in .env")
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{_API_BASE}/sendPhoto",
            data={
                "chat_id": _CHANNEL_ID,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            },
            files={"photo": f},
            timeout=30,
        )
    if not resp.ok:
        logger.error("텔레그램 이미지 전송 실패: %s", resp.text)
        return False
    return True


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
    us_flow = _flow_label_from(row, "us_flow_signal", "us_net_flow_5d")
    kr_flow = _flow_label_from(row, "kr_flow_signal", "kr_net_flow_5d")
    lines = [
        f"\n<b>{rank}. {theme_label}</b>",
        (f"🇺🇸 <code>{row['us_ticker']}</code>{us_flow}  "
         f"1D {_fmt_ret(us_1d)}  1W {_fmt_ret(us_1w)}  "
         f"1M {_fmt_ret(us_1m)}  3M {_fmt_ret(us_3m)}"),
        f"🇰🇷 <code>{row['kr_ticker']}</code> {row.get('kr_ticker_name', '')}{kr_flow}",
        (f"     "
         f"1D {_fmt_ret(kr_1d)}  1W {_fmt_ret(kr_1w)}  "
         f"1M {_fmt_ret(kr_1m)}  3M {_fmt_ret(kr_3m)}"),
    ]
    if disc is not None:
        lines.append(f"     괴리율 {_fmt_discount(disc)}")
    if stop is not None and atr_v is not None:
        lines.append(f"     📍 손절선 {stop:,.0f}원  (ATR±{atr_v:,.0f})")
    return lines


def _flow_label_from(row: pd.Series, sig_col: str, nf_col: str) -> str:
    """임의 컬럼명으로 flow 라벨 생성 (매칭 행에서 us_/kr_ 접두 사용)."""
    sig = row.get(sig_col)
    if sig is None or (isinstance(sig, float) and pd.isna(sig)):
        return ""
    emo = _FLOW_EMOJI.get(sig, "")
    label = _FLOW_LABEL_KR.get(sig, sig)
    nf = row.get(nf_col)
    if pd.notna(nf) and nf:
        unit = "억" if abs(nf) > 1e8 else "백만"
        scale = 1e8 if unit == "억" else 1e6
        return f"  {emo}{label}({nf/scale:+.0f}{unit})"
    return f"  {emo}{label}"


_FLOW_EMOJI = {
    "INFLOW": "💰",
    "OUTFLOW": "💸",
    "INTEREST_UP": "🔼",
    "INTEREST_DOWN": "🔽",
    "FLAT": "▪️",
    "N/A": "📊",
}
_FLOW_LABEL_KR = {
    "INFLOW": "유입",
    "OUTFLOW": "유출",
    "INTEREST_UP": "관심↑",
    "INTEREST_DOWN": "관심↓",
    "FLAT": "보합",
    "N/A": "수집중",
}


def _flow_label(row: pd.Series) -> str:
    sig = row.get("flow_signal")
    if not sig or pd.isna(sig):
        return ""
    emo = _FLOW_EMOJI.get(sig, "")
    label = _FLOW_LABEL_KR.get(sig, sig)
    nf = row.get("net_flow_5d")
    if pd.notna(nf) and nf:
        unit = "억" if abs(nf) > 1e8 else "백만"
        scale = 1e8 if unit == "억" else 1e6
        return f"  {emo}{label} ({nf/scale:+.0f}{unit})"
    return f"  {emo}{label}"


def _build_solo_entry(rank: int, row: pd.Series, is_kr: bool) -> list[str]:
    """단독 ETF 1개의 메시지 라인 리스트 반환."""
    r_1d = row.get("return_1d") or 0
    r_1w = row.get("return_1w") or 0
    r_1m = row.get("return_1m") or 0
    r_3m = row.get("return_3m") or 0
    flag = "🇰🇷" if is_kr else "🇺🇸"
    name = row.get("name") or row.get("kr_ticker_name") or ""
    flow = _flow_label(row)
    return [
        (f"\n{rank}. {flag} <code>{row['ticker']}</code> {name}{flow}".rstrip()),
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

    # 자금유입 수집 진척도 표기
    flow_days = 0
    for df in (kr_df, us_df):
        if df is not None and not df.empty and "days_collected" in df.columns:
            flow_days = max(flow_days, int(df["days_collected"].max() or 0))

    if flow_days >= 6:
        flow_status = f"💰 자금유입 추적 활성 (Day {flow_days})"
    elif flow_days >= 2:
        flow_status = f"📊 거래대금 관심도 활성 (Day {flow_days}, 5일 이상부터 INFLOW/OUTFLOW)"
    else:
        flow_status = f"📊 자금유입 수집 중 (Day {max(flow_days, 1)} / 5일 후 활성)"

    lines = [
        f"📊 <b>ETF 모멘텀 스크리너</b>  |  {now}",
        "",
        f"국내 통과 {kr_count}종목  ·  미국 통과 {us_count}종목",
        f"한미 동시 모멘텀  섹터 <b>{sector_count}개</b>  |  전체 <b>{len(result)}개</b> 테마",
        f"<i>{flow_status}</i>",
        "─" * 30,
    ]

    # ── 1. 한미 동시 주도 (전체 상위 N개) ─────────────
    lines.append(f"\n<b>🔥 한미 동시 주도 TOP {_MATCHED_TOP_N}</b>")
    if result.empty:
        lines.append("\n⚠️ 매칭 테마 없음 (모멘텀/SPY 필터 미달)")
    else:
        # match_score 기준 정렬 후 top N
        if "match_score" in result.columns:
            top_matched = result.sort_values("match_score", ascending=False).head(_MATCHED_TOP_N)
        else:
            top_matched = result.head(_MATCHED_TOP_N)
        for rank, (_, row) in enumerate(top_matched.iterrows(), 1):
            lines.extend(_build_entry(rank, row))

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
