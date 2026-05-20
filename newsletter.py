#!/usr/bin/env python3
"""
newsletter.py — Lil Tony Trading Brain
End-of-day newsletter: HTML email + iMessage PNG callout card.
Runs at 4:30 PM CT via launchd.
"""

import logging
import sys
import textwrap
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from scanner.ranker import scan_universe, select_top_candidates
from brain.macro_filter import load_macro_calendar, check_macro_risk
from alerts.email_sender import send_email_alert
from alerts.imessage import send_imessage, send_imessage_with_image
from config import settings

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [newsletter] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "newsletter.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")


def _direction(ticker: dict) -> str:
    """Guess CALL vs PUT from momentum signals."""
    ret_1d = ticker.get("ret_1d", 0)
    ret_5d = ticker.get("ret_5d", 0)
    rsi    = ticker.get("rsi", 50)
    if ret_1d > 0 and ret_5d > 0 and rsi < 75:
        return "CALL"
    if ret_1d < 0 and ret_5d < 0 and rsi > 25:
        return "PUT"
    return "CALL"


_RSI_READS = {
    (0,  30): "Oversold — mean-reversion setup",
    (30, 50): "Recovering — early momentum",
    (50, 65): "Healthy momentum, room to run",
    (65, 75): "Strong trend, slightly stretched",
    (75, 100): "Overbought — skip or fade",
}


def _rsi_read(rsi: float) -> str:
    for (lo, hi), label in _RSI_READS.items():
        if lo <= rsi < hi:
            return label
    return "—"


def _ticker_read(t: dict) -> str:
    rsi    = t.get("rsi", 50)
    ret_1d = t.get("ret_1d", 0)
    ret_5d = t.get("ret_5d", 0)
    vol    = t.get("volume_ratio", 1.0)

    lines = []
    if rsi >= 75:
        lines.append("Skip — RSI overbought, extended")
    elif ret_5d > 0.08 and rsi < 70:
        lines.append("Strong 5d trend with healthy RSI")
    elif abs(ret_1d) > abs(ret_5d) * 0.6:
        lines.append("Biggest 1d mover, RSI healthy")

    if vol >= 1.5:
        lines.append(f"Elevated vol {vol:.1f}x — conviction")
    if ret_5d < 0.03 and ret_1d > 0.02:
        lines.append("Decent but 5d move weak")

    return ", ".join(lines) if lines else _rsi_read(rsi)


def _make_callout_image(top3: list, macro_warn: str | None, date_str: str) -> Path:
    """Generate a dark-theme PNG callout card showing Top 3 picks."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 800, 520
    BG       = (10, 12, 16)
    CARD_BG  = (18, 22, 30)
    GOLD     = (255, 196, 0)
    GREEN    = (0, 230, 118)
    RED      = (255, 69, 58)
    GRAY     = (120, 130, 145)
    WHITE    = (230, 235, 245)
    BORDER   = (40, 48, 62)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    def _font(size: int):
        for path in (
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSMono.ttf",
            "/System/Library/Fonts/Courier.ttc",
        ):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    font_title  = _font(28)
    font_ticker = _font(36)
    font_label  = _font(16)
    font_small  = _font(13)
    font_body   = _font(18)

    draw.rectangle([(0, 0), (W, 60)], fill=CARD_BG)
    draw.line([(0, 60), (W, 60)], fill=BORDER, width=1)
    draw.text((24, 16), "⚡ LIL TONY TRADING BRAIN", font=font_title, fill=GOLD)
    draw.text((W - 180, 20), date_str, font=font_label, fill=GRAY)

    draw.text((24, 72), "TOP 3 FOR TOMORROW", font=font_label, fill=GRAY)

    card_w = (W - 48 - 16) // 3
    card_h = 200
    card_y = 100
    medals = ["🥇", "🥈", "🥉"]

    for i, pick in enumerate(top3):
        cx = 24 + i * (card_w + 8)
        draw.rounded_rectangle(
            [(cx, card_y), (cx + card_w, card_y + card_h)],
            radius=10, fill=CARD_BG, outline=BORDER, width=1,
        )

        direction = _direction(pick)
        dir_color = GREEN if direction == "CALL" else RED

        draw.text((cx + 10, card_y + 8), medals[i], font=font_body, fill=WHITE)

        ticker_sym = pick.get("ticker", "???")
        draw.text((cx + 10, card_y + 36), ticker_sym, font=font_ticker, fill=WHITE)

        badge_x = cx + card_w - 68
        draw.rounded_rectangle(
            [(badge_x, card_y + 40), (badge_x + 58, card_y + 62)],
            radius=6, fill=dir_color,
        )
        draw.text((badge_x + 8, card_y + 44), direction, font=font_label, fill=BG)

        ret_1d = pick.get("ret_1d", 0)
        ret_5d = pick.get("ret_5d", 0)
        rsi    = pick.get("rsi", 0)
        vol    = pick.get("volume_ratio", 0)
        spot   = pick.get("spot", 0)
        score  = pick.get("composite_score") or pick.get("momentum_score", 0)

        stat_y = card_y + 74
        line_h = 22

        def stat_row(label, value, color=WHITE):
            nonlocal stat_y
            draw.text((cx + 10, stat_y), label, font=font_small, fill=GRAY)
            draw.text((cx + 90, stat_y), value, font=font_small, fill=color)
            stat_y += line_h

        stat_row("1d",    f"{ret_1d:+.1%}", GREEN if ret_1d >= 0 else RED)
        stat_row("5d",    f"{ret_5d:+.1%}", GREEN if ret_5d >= 0 else RED)
        stat_row("RSI",   f"{rsi:.0f}")
        stat_row("Vol",   f"{vol:.1f}x")
        stat_row("Spot",  f"${spot:.2f}")
        stat_row("Score", f"{score:.0f}", GOLD)

    warn_y = card_y + card_h + 18
    if macro_warn:
        draw.rounded_rectangle(
            [(24, warn_y), (W - 24, warn_y + 54)],
            radius=8, fill=(40, 20, 0), outline=(180, 100, 0), width=1,
        )
        warn_lines = textwrap.wrap(f"⚠️  {macro_warn}", width=90)
        for j, wl in enumerate(warn_lines[:2]):
            draw.text((36, warn_y + 8 + j * 20), wl, font=font_small, fill=(255, 180, 0))

    footer_y = H - 36
    draw.line([(0, footer_y - 8), (W, footer_y - 8)], fill=BORDER, width=1)
    draw.text((24, footer_y), "Max trade $100  •  Paper trading phase  •  Not financial advice",
              font=font_small, fill=GRAY)

    out_path = Path("/tmp/liltony_newsletter_callout.png")
    img.save(out_path, "PNG")
    log.info(f"Callout card saved → {out_path}")
    return out_path


def _build_html(candidates: list, top3: list, macro_warn: str | None,
                is_binary: bool, date_str: str) -> str:
    """Build dark-theme HTML newsletter."""

    def _pct(v):
        if v is None: return "—"
        color = "#00e676" if v >= 0 else "#ff453a"
        return f'<span style="color:{color}">{v:+.1%}</span>'

    def _score_bar(score):
        pct = min(100, max(0, score))
        color = "#ffc400" if pct >= 70 else "#00e676" if pct >= 50 else "#546e7a"
        return (
            f'<div style="background:#1e2430;border-radius:4px;height:6px;width:100px;display:inline-block;vertical-align:middle">'
            f'<div style="background:{color};width:{pct}%;height:100%;border-radius:4px"></div></div>'
            f'&nbsp;<span style="color:#788090;font-size:11px">{pct:.0f}</span>'
        )

    top3_tickers = {x.get("ticker") for x in top3}

    rows = ""
    for t in candidates:
        ticker = t.get("ticker", "?")
        ret_1d = t.get("ret_1d")
        ret_5d = t.get("ret_5d")
        rsi    = t.get("rsi", 0)
        vol    = t.get("volume_ratio", 0)
        spot   = t.get("spot", 0)
        score  = t.get("composite_score") or t.get("momentum_score", 0)
        read   = _ticker_read(t)
        in_top3 = ticker in top3_tickers
        row_bg  = "#0d1c12" if in_top3 else "#0f1620"
        star    = "⭐ " if in_top3 else ""

        rsi_color = "#ff453a" if rsi >= 75 else "#ffc400" if rsi >= 65 else "#00e676"

        rows += f"""
        <tr style="border-bottom:1px solid #1e2430;background:{row_bg}">
          <td style="padding:10px 14px;font-weight:700;color:#e8edf5;white-space:nowrap">{star}{ticker}</td>
          <td style="padding:10px 14px;text-align:center">{_pct(ret_1d)}</td>
          <td style="padding:10px 14px;text-align:center">{_pct(ret_5d)}</td>
          <td style="padding:10px 14px;text-align:center;color:{rsi_color};font-weight:600">{rsi:.0f}</td>
          <td style="padding:10px 14px;text-align:center;color:#a0aabb">{vol:.1f}x</td>
          <td style="padding:10px 14px;text-align:center;color:#e8edf5">${spot:.2f}</td>
          <td style="padding:10px 14px;text-align:center">{_score_bar(score)}</td>
          <td style="padding:10px 14px;color:#788090;font-size:12px;max-width:200px">{read}</td>
        </tr>"""

    top3_html = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, t in enumerate(top3):
        ticker    = t.get("ticker", "?")
        direction = _direction(t)
        dir_color = "#00e676" if direction == "CALL" else "#ff453a"
        read      = _ticker_read(t)
        top3_html += f"""
        <div style="display:inline-block;background:#0f1620;border:1px solid #1e2430;
                    border-radius:10px;padding:14px 20px;margin:0 8px 0 0;min-width:160px;vertical-align:top">
          <div style="font-size:22px;margin-bottom:4px">{medals[i]}</div>
          <div style="font-size:26px;font-weight:800;color:#e8edf5;letter-spacing:1px">{ticker}</div>
          <div style="display:inline-block;background:{dir_color};color:#0a0c10;font-weight:700;
                      font-size:12px;padding:2px 10px;border-radius:4px;margin:6px 0">{direction}</div>
          <div style="color:#788090;font-size:12px;margin-top:6px;line-height:1.4">{read}</div>
        </div>"""

    macro_block = ""
    if macro_warn:
        border_color = "#ff453a" if is_binary else "#ffc400"
        bg_color     = "#1a0a0a" if is_binary else "#1a1200"
        # macro_warn uses newlines; convert to <br> for HTML rendering
        macro_html = macro_warn.replace("\n", "<br>")
        macro_block  = f"""
        <div style="background:{bg_color};border:1px solid {border_color};border-radius:8px;
                    padding:14px 18px;margin:24px 0;color:{border_color};font-size:13px;line-height:1.6">
          ⚠️&nbsp; {macro_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lil Tony Trading Brain — {date_str}</title>
</head>
<body style="margin:0;padding:0;background:#080b10;font-family:'Courier New',monospace;color:#e8edf5">
<div style="max-width:760px;margin:0 auto;padding:24px 16px">

  <div style="border-bottom:2px solid #ffc400;padding-bottom:16px;margin-bottom:24px">
    <div style="font-size:11px;color:#546e7a;letter-spacing:3px;text-transform:uppercase;margin-bottom:6px">
      End-of-Day Report
    </div>
    <div style="font-size:26px;font-weight:800;color:#ffc400;letter-spacing:1px">
      ⚡ Lil Tony Trading Brain
    </div>
    <div style="font-size:13px;color:#788090;margin-top:4px">{date_str} · Dallas CT</div>
  </div>

  {macro_block}

  <div style="font-size:11px;color:#546e7a;letter-spacing:2px;margin-bottom:10px">SCAN RESULTS</div>
  <div style="overflow-x:auto">
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="border-bottom:2px solid #1e2430;color:#546e7a;font-size:11px;letter-spacing:1px;text-transform:uppercase">
        <th style="padding:8px 14px;text-align:left">Ticker</th>
        <th style="padding:8px 14px">1d</th>
        <th style="padding:8px 14px">5d</th>
        <th style="padding:8px 14px">RSI</th>
        <th style="padding:8px 14px">Vol</th>
        <th style="padding:8px 14px">Spot</th>
        <th style="padding:8px 14px">Score</th>
        <th style="padding:8px 14px;text-align:left">Read</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  </div>

  <div style="margin-top:32px">
    <div style="font-size:11px;color:#546e7a;letter-spacing:2px;margin-bottom:14px">TOP 3 FOR TOMORROW</div>
    <div>{top3_html}</div>
  </div>

  <div style="margin-top:40px;padding-top:16px;border-top:1px solid #1e2430;
              color:#3a4455;font-size:11px;line-height:1.8">
    Max trade $100 &nbsp;·&nbsp; Small trades $20–25 &nbsp;·&nbsp; Paper trading phase<br>
    Not financial advice. For educational use only.
  </div>

</div>
</body>
</html>"""


def run():
    start    = datetime.now(CT)
    date_str = start.strftime("%A, %B %-d %Y · %-I:%M %p CT")
    log.info(f"Newsletter run started — {date_str}")

    recipient          = settings.IMESSAGE_RECIPIENT
    email_ok           = False
    imessage_ok        = False
    imessage_had_image = False
    fatal_error: str | None = None

    try:
        # 1. scan
        log.info("Running scan_universe...")
        candidates_raw = scan_universe()
        if not candidates_raw:
            log.warning("No candidates returned — aborting newsletter.")
            fatal_error = "scan returned no candidates"
        else:
            top3_raw = select_top_candidates(candidates_raw)
            candidates = [asdict(c) for c in candidates_raw]
            top3       = [asdict(c) for c in top3_raw]
            log.info(f"Top 3: {[t['ticker'] for t in top3]}")

            # 2. macro warning
            try:
                macro_events = load_macro_calendar()
                is_binary, macro_warn = check_macro_risk(macro_events)
            except Exception as e:
                log.warning(f"Macro filter failed (non-fatal): {e}")
                is_binary, macro_warn = False, None

            # 3. HTML email
            subject = f"⚡ Lil Tony EOD · {start.strftime('%b %-d')} · Top 3: {', '.join(t['ticker'] for t in top3)}"
            html    = _build_html(candidates, top3, macro_warn, is_binary, date_str)
            text_body = (
                f"Lil Tony EOD — {date_str}\n"
                f"Top 3: {', '.join(t['ticker'] for t in top3)}\n"
                "(View in an HTML-capable client for the full report.)"
            )
            log.info("Sending HTML email...")
            email_ok = send_email_alert(subject=subject, body=text_body, html_body=html)
            if email_ok:
                log.info("Email sent ✓")
            else:
                log.warning("Email send failed — see prior log lines")

            # 4. iMessage caption + PNG callout card
            if not recipient:
                log.warning("IMESSAGE_RECIPIENT not set — skipping iMessage callout")
            else:
                caption = (
                    f"⚡ EOD · {start.strftime('%b %-d')}\n"
                    + "\n".join(
                        f"{i+1}. {t['ticker']} {_direction(t)}"
                        for i, t in enumerate(top3)
                    )
                    + "\n⚠️ Max $100/trade"
                )

                log.info("Generating callout card...")
                img_path = None
                try:
                    img_path = _make_callout_image(top3, macro_warn, start.strftime("%b %-d %Y"))
                except ImportError:
                    log.warning("Pillow not installed — sending caption without image")
                except Exception as e:
                    log.exception(f"PNG generation failed — sending caption without image: {e}")

                if img_path:
                    imessage_ok = send_imessage_with_image(recipient, caption, str(img_path))
                    imessage_had_image = imessage_ok
                    if imessage_ok:
                        log.info("iMessage caption + image sent ✓")
                    else:
                        log.warning("iMessage caption + image send failed — see prior log lines")
                else:
                    imessage_ok = send_imessage(recipient, caption)
                    if imessage_ok:
                        log.info("iMessage caption sent ✓ (no image)")
                    else:
                        log.warning("iMessage caption send failed — see prior log lines")
    except Exception as e:
        log.exception(f"Newsletter run crashed: {e}")
        fatal_error = f"{type(e).__name__}: {e}"

    log.info("Newsletter complete ✓")

    # Self-report: send a one-line status iMessage so Big Tony knows the run
    # finished, regardless of which stages succeeded.
    if recipient:
        duration_min = max(1, int((datetime.now(CT) - start).total_seconds() / 60))
        if fatal_error:
            status = (
                f"⚠️ Newsletter FAILED in {duration_min}m\n"
                f"{fatal_error}\n"
                f"Check logs/newsletter.log"
            )
        else:
            icon     = "✅" if (email_ok and imessage_ok) else "⚠️"
            img_note = " +img" if imessage_had_image else ""
            status = (
                f"{icon} Newsletter done in {duration_min}m\n"
                f"Email: {'✓' if email_ok else '✗'} · "
                f"iMessage: {'✓' if imessage_ok else '✗'}{img_note}"
            )
        send_imessage(recipient, status)


if __name__ == "__main__":
    run()
