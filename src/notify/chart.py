"""ETF 수익률 시각화 — 텔레그램 전송용 PNG 차트 생성."""
from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import pandas as pd

logger = logging.getLogger(__name__)


def _set_korean_font() -> None:
    """OS별 한글 폰트 설정."""
    system = platform.system()
    if system == "Windows":
        plt.rcParams["font.family"] = "Malgun Gothic"
    elif system == "Darwin":
        plt.rcParams["font.family"] = "AppleGothic"
    else:
        # Linux — Noto, Nanum 시도
        for f in ("Noto Sans CJK KR", "NanumGothic", "DejaVu Sans"):
            try:
                plt.rcParams["font.family"] = f
                break
            except Exception:
                continue
    plt.rcParams["axes.unicode_minus"] = False


def _draw_panel(ax, df: pd.DataFrame, title: str, label_col: str = "label") -> None:
    """1W/1M/3M 그룹 막대 + 모멘텀 스코어 정렬 패널."""
    if df.empty:
        ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center")
        ax.set_title(title)
        ax.axis("off")
        return

    df = df.sort_values("momentum_score", ascending=True)  # 위쪽이 강함
    y = range(len(df))
    h = 0.27

    r1w = (df["return_1w"] * 100).fillna(0)
    r1m = (df["return_1m"] * 100).fillna(0)
    r3m = (df["return_3m"] * 100).fillna(0)

    ax.barh([i + h for i in y], r1w, h, color="#4FC3F7", label="1W")
    ax.barh(y,                  r1m, h, color="#42A5F5", label="1M")
    ax.barh([i - h for i in y], r3m, h, color="#1565C0", label="3M")

    ax.set_yticks(list(y))
    ax.set_yticklabels(df[label_col].tolist(), fontsize=9)
    ax.axvline(0, color="#999", linewidth=0.6)
    ax.set_xlabel("수익률 (%)")
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.legend(loc="lower right", fontsize=8, frameon=False)


def build_returns_chart(
    kr_df: pd.DataFrame,
    us_df: pd.DataFrame,
    matched: pd.DataFrame,
    output_path: Path,
    top_n: int = 12,
) -> Path:
    """KR / US / 매칭 테마 수익률 통합 시각화 PNG 생성.

    Args:
        kr_df: KR 스크리닝 결과 (ticker, name, return_1w, return_1m, return_3m, momentum_score).
        us_df: US 스크리닝 결과 (동일 컬럼).
        matched: 한미 매칭 결과 (theme, us_ticker, kr_ticker_name, *_return_*).
        output_path: 저장 경로 (.png).
        top_n: 패널당 표시 개수.

    Returns:
        저장된 PNG 경로.
    """
    _set_korean_font()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 라벨: "ticker  name(앞 14자)"
    def _make_label(t: str, n: str) -> str:
        n = (n or "").strip()
        if len(n) > 18:
            n = n[:18] + "…"
        return f"{t}  {n}".strip()

    kr_view = kr_df.head(top_n).copy() if not kr_df.empty else kr_df.copy()
    if not kr_view.empty:
        kr_view["label"] = [
            _make_label(str(t).zfill(6), n) for t, n in zip(kr_view["ticker"], kr_view.get("name", ""))
        ]

    us_view = us_df.head(top_n).copy() if not us_df.empty else us_df.copy()
    if not us_view.empty:
        us_view["label"] = [
            _make_label(t, n) for t, n in zip(us_view["ticker"], us_view.get("name", ""))
        ]

    has_matched = not matched.empty
    nrows = 3 if has_matched else 2
    fig, axes = plt.subplots(
        nrows, 1, figsize=(11, 3.2 + 0.42 * top_n + (3 if has_matched else 0)),
        gridspec_kw={"height_ratios": [4, 4, 2.5][:nrows]},
    )
    if nrows == 1:
        axes = [axes]

    _draw_panel(axes[0], kr_view, f"[KR] 국내 ETF 모멘텀 TOP {len(kr_view)}")
    _draw_panel(axes[1], us_view, f"[US] 미국 ETF 모멘텀 TOP {len(us_view)}")

    if has_matched:
        m = matched.copy()
        m["return_1w"] = (m.get("us_return_1w", 0).fillna(0) + m.get("kr_return_1w", 0).fillna(0)) / 2
        m["return_1m"] = (m.get("us_return_1m", 0).fillna(0) + m.get("kr_return_1m", 0).fillna(0)) / 2
        m["return_3m"] = (m.get("us_return_3m", 0).fillna(0) + m.get("kr_return_3m", 0).fillna(0)) / 2
        if "match_score" in m.columns:
            m["momentum_score"] = m["match_score"]
        else:
            m["momentum_score"] = m["return_1m"]
        # 매칭은 전체 상위 3개만
        m = m.sort_values("momentum_score", ascending=False).head(3)
        m["label"] = [
            f"{th[:14]}  ({us}/{kr})"
            for th, us, kr in zip(m["theme"], m["us_ticker"], m["kr_ticker"])
        ]
        _draw_panel(axes[2], m, f"[MATCHED] 한미 동시 주도 TOP {len(m)} (1W/1M/3M 평균)")

    fig.suptitle(
        f"ETF 모멘텀 스크리너  |  {pd.Timestamp.now():%Y-%m-%d %H:%M}",
        fontsize=13, fontweight="bold", y=0.995,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    logger.info("차트 저장: %s", output_path)
    return output_path
