"""텔레그램 명령 봇 — 폴링 기반 long-running 워커.

명령어:
    /help               — 사용법 안내
    /screen             — 즉시 스크리닝 실행
    /status             — 마지막 결과 요약
    /flow <ticker>      — 특정 ETF 자금유입 추이
    /top kr|us [N]      — 시장별 상위 N개

권한:
    .env의 TELEGRAM_AUTHORIZED_USER_IDS (쉼표 구분)에 등록된 사용자만.
    값이 비어 있으면 모두 허용 (테스트용).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_AUTHORIZED = {
    s.strip()
    for s in (
        os.getenv("TELEGRAM_AUTHORIZED_USER_IDS", "")
        + ","
        + os.getenv("TELEGRAM_ALLOWED_IDS", "")
    ).split(",")
    if s.strip()
}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PYTHON = _PROJECT_ROOT / "venv_mom_etf" / "Scripts" / "python.exe"
_MAIN_PY = _PROJECT_ROOT / "main.py"


# ─────────────────────────────────────────────────
# 권한 체크
# ─────────────────────────────────────────────────
def _is_authorized(update: Update) -> bool:
    if not _AUTHORIZED:
        return True  # 화이트리스트 비어 있으면 누구나
    user = update.effective_user
    return bool(user) and str(user.id) in _AUTHORIZED


async def _deny(update: Update) -> None:
    await update.message.reply_text(
        f"⛔ 권한이 없습니다. 본인의 Telegram user_id를 .env의 "
        f"TELEGRAM_AUTHORIZED_USER_IDS에 추가하세요.\n"
        f"  당신의 user_id: <code>{update.effective_user.id}</code>",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────────
# 명령어 핸들러
# ─────────────────────────────────────────────────
async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return await _deny(update)
    text = (
        "<b>📊 ETF 모멘텀 스크리너 봇</b>\n\n"
        "<b>명령어</b>\n"
        "/screen — 즉시 스크리닝 실행 (1~2분 소요)\n"
        "/status — 마지막 결과 요약\n"
        "/top kr [N] — 국내 상위 N개 (기본 10)\n"
        "/top us [N] — 미국 상위 N개\n"
        "/flow &lt;ticker&gt; — 자금유입 추이\n"
        "/help — 이 도움말\n\n"
        "<b>자동 실행</b>: 평일 08:00 (Windows 작업 스케줄러)\n"
        f"<b>권한</b>: {'화이트리스트 ' + str(len(_AUTHORIZED)) + '명' if _AUTHORIZED else '⚠ 누구나 사용 가능'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_screen(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return await _deny(update)
    await update.message.reply_text("⏳ 스크리닝 시작합니다 (1~2분 소요)…")

    def _run() -> tuple[int, str]:
        proc = subprocess.run(
            [str(_PYTHON), str(_MAIN_PY)],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    loop = asyncio.get_running_loop()
    try:
        code, _out = await loop.run_in_executor(None, _run)
        if code == 0:
            await update.message.reply_text("✅ 완료. 채널을 확인하세요.")
        else:
            await update.message.reply_text(f"❌ 실패 (exit {code}). 로그 확인 필요.")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏰ 10분 초과 — 강제 종료됨.")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return await _deny(update)
    from src.db.turso import get_conn

    try:
        with get_conn() as c:
            sd = c.execute(
                "SELECT MAX(screen_date) FROM etf_screen_kr"
            ).fetchone()[0]
            if not sd:
                await update.message.reply_text("⚠ 저장된 결과 없음")
                return
            kr_n = c.execute(
                "SELECT COUNT(*) FROM etf_screen_kr WHERE screen_date=?", [sd]
            ).fetchone()[0]
            us_n = c.execute(
                "SELECT COUNT(*) FROM etf_screen_us WHERE screen_date=?", [sd]
            ).fetchone()[0]
            unified = c.execute(
                "SELECT theme, us_ticker, kr_ticker, kr_ticker_name "
                "FROM etf_screen_unified WHERE screen_date=? "
                "ORDER BY match_score DESC LIMIT 5",
                [sd],
            ).fetchall()
        lines = [
            f"<b>📊 마지막 결과</b>  ({sd})",
            f"국내 통과 {kr_n}종목 · 미국 통과 {us_n}종목",
            "",
            "<b>한미 매칭 TOP</b>",
        ]
        if unified:
            for theme, us, kr, kr_name in unified:
                lines.append(
                    f"• {theme.upper()}  🇺🇸<code>{us}</code> / 🇰🇷<code>{kr}</code> {kr_name}"
                )
        else:
            lines.append("매칭 없음")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ 조회 오류: {e}")


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return await _deny(update)
    args = ctx.args
    if not args or args[0].lower() not in ("kr", "us"):
        await update.message.reply_text("사용법: /top kr [N]   또는   /top us [N]")
        return
    market = args[0].lower()
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10

    from src.db.turso import get_conn

    table = "etf_screen_kr" if market == "kr" else "etf_screen_us"
    flag = "🇰🇷" if market == "kr" else "🇺🇸"
    try:
        with get_conn() as c:
            sd = c.execute(f"SELECT MAX(screen_date) FROM {table}").fetchone()[0]
            rows = c.execute(
                f"SELECT ticker, COALESCE(name,''), return_1w, return_1m, return_3m "
                f"FROM {table} WHERE screen_date=? "
                f"ORDER BY momentum_score DESC LIMIT ?",
                [sd, n],
            ).fetchall()
        if not rows:
            await update.message.reply_text("결과 없음")
            return
        lines = [f"<b>{flag} TOP {len(rows)}</b>  ({sd})"]
        for i, (t, name, r1w, r1m, r3m) in enumerate(rows, 1):
            r1w = (r1w or 0) * 100
            r1m = (r1m or 0) * 100
            r3m = (r3m or 0) * 100
            lines.append(
                f"{i:>2}. <code>{t}</code> {name[:18]}\n"
                f"     1W {r1w:+.1f}%  1M {r1m:+.1f}%  3M {r3m:+.1f}%"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def cmd_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return await _deny(update)
    if not ctx.args:
        await update.message.reply_text("사용법: /flow <ticker>   예) /flow 381180  또는  /flow SMH")
        return
    ticker = ctx.args[0].strip()
    market = "KR" if ticker.isdigit() else "US"
    if market == "KR":
        ticker = ticker.zfill(6)

    from src.db.turso import get_conn

    try:
        with get_conn() as c:
            rows = c.execute(
                "SELECT snapshot_date, nav, shares_out, aum, trading_value "
                "FROM etf_aum_history WHERE market=? AND ticker=? "
                "ORDER BY snapshot_date DESC LIMIT 14",
                [market, ticker],
            ).fetchall()
        if not rows:
            await update.message.reply_text(f"<code>{ticker}</code> 자금유입 데이터 없음", parse_mode=ParseMode.HTML)
            return

        rows = list(reversed(rows))  # 오래된 순
        lines = [f"<b>💰 자금유입 추이</b>  {market} <code>{ticker}</code>"]
        prev_shares = None
        for sd_, nav, shares, aum, tv in rows:
            net_flow = ""
            if prev_shares is not None and nav and shares:
                nf = (shares - prev_shares) * nav
                if abs(nf) > 1e8:
                    net_flow = f"  💰{nf/1e8:+.1f}억" if market == "KR" else f"  💰${nf/1e6:+.1f}M"
            prev_shares = shares
            unit_aum = "조" if market == "KR" else "B"
            scale_aum = 1e12 if market == "KR" else 1e9
            lines.append(
                f"{sd_}  AUM {aum/scale_aum:.2f}{unit_aum}{net_flow}"
            )
        if len(rows) < 2:
            lines.append("\n<i>📊 수집중 (최소 2일 필요)</i>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")


async def cmd_unknown(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return  # 권한 없으면 조용히 무시
    await update.message.reply_text("❓ 알 수 없는 명령. /help 참고.")


# ─────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────
def run() -> None:
    if not _BOT_TOKEN:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN not set")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/bot.log", encoding="utf-8"),
        ],
    )
    # 시스템 cp949 회피
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    app = Application.builder().token(_BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("screen", cmd_screen))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("flow", cmd_flow))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    auth_note = (
        f"화이트리스트 {len(_AUTHORIZED)}명"
        if _AUTHORIZED
        else "⚠ 누구나 사용 가능 (TELEGRAM_AUTHORIZED_USER_IDS 미설정)"
    )
    logger.info("ETF 봇 시작 — %s", auth_note)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    run()
