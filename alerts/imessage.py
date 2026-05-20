import logging
import os
import subprocess

from scanner.setups import Setup

logger = logging.getLogger(__name__)


def _escape_for_applescript(text: str) -> str:
    """Escape a string for embedding in an AppleScript double-quoted literal.

    Order matters: backslash, then quote, then newline (the newline replacement
    injects literal AppleScript quotes that must not be re-escaped).
    """
    return (
        text
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "")
        .replace("\n", '" & return & "')
    )


def send_imessage(recipient: str, message: str) -> bool:
    """Send an iMessage via AppleScript. recipient is a phone number or Apple ID."""
    safe_msg = _escape_for_applescript(message)
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e", 'tell application "Messages"',
                "-e", f'send "{safe_msg}" to participant "{recipient}"',
                "-e", "end tell",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"iMessage failed: {result.stderr.strip()}")
            return False
        logger.info(f"iMessage sent to {recipient}")
        return True
    except subprocess.TimeoutExpired:
        logger.error("iMessage timed out")
        return False
    except Exception as e:
        logger.error(f"iMessage error: {e}")
        return False


def send_imessage_with_image(recipient: str, message: str, image_path: str) -> bool:
    """Send a caption + image attachment to one iMessage recipient.

    Both sends run inside one osascript call. If the first send succeeds and the
    second fails, you get a partial (caption only); osascript returns non-zero
    and the error is logged.
    """
    if not os.path.exists(image_path):
        logger.error(f"iMessage image not found: {image_path}")
        return False
    safe_msg  = _escape_for_applescript(message)
    safe_path = image_path.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Messages"\n'
        f'  send "{safe_msg}" to participant "{recipient}"\n'
        f'  send POSIX file "{safe_path}" to participant "{recipient}"\n'
        'end tell'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            logger.error(f"iMessage with image failed: {result.stderr.strip()}")
            return False
        logger.info(f"iMessage + image sent to {recipient}")
        return True
    except subprocess.TimeoutExpired:
        logger.error("iMessage with image timed out")
        return False
    except Exception as e:
        logger.error(f"iMessage with image error: {e}")
        return False


def format_alert(setup: Setup) -> str:
    """Format a Setup object into the standard iMessage alert."""
    direction = "CALL" if setup.opt_type == "call" else "PUT"
    score_str = f"{round(setup.score)}/100" if hasattr(setup, "score") and setup.score else "--/100"
    stop   = getattr(setup, "stop", None)
    target = getattr(setup, "target", None)
    stop_str   = f"${stop:.2f}"   if stop   is not None else "--"
    target_str = f"${target:.2f}" if target is not None else "--"
    spot_line  = f"▸ Spot:     ${setup.spot_price:.2f}\n" if setup.spot_price else ""
    return (
        f"🔥 {setup.ticker}  |  {direction}\n"
        f"▸ Strike:   ${setup.strike:.2f}\n"
        f"▸ Exp:      {setup.expiration}\n"
        f"▸ Entry:    ${setup.last_price:.2f}\n"
        f"▸ Stop:     {stop_str}\n"
        f"▸ Target:   {target_str}\n"
        f"{spot_line}"
        f"▸ Score:    {score_str}\n"
        f"▸ Type:     {setup.setup_type}\n"
        f"⚡ MAX TRADE: $100"
    )


def format_alert_dict(a: dict) -> str:
    """Format a phase-4 alert dict into the standard iMessage alert."""
    direction = "CALL" if a.get("opt_type") == "call" else "PUT"
    raw_exp   = a.get("expiration", "")
    if raw_exp and len(raw_exp) == 10:  # 2026-05-22 → 05/22/26
        parts = raw_exp.split("-")
        exp = f"{parts[1]}/{parts[2]}/{parts[0][2:]}"
    else:
        exp = raw_exp

    lines = [
        f"🔥 {a.get('ticker','??')}  |  {direction}",
        f"▸ Strike:   ${a.get('strike', 0):.2f}",
        f"▸ Exp:      {exp}",
        f"▸ Entry:    ${a.get('entry', 0):.2f}",
        f"▸ Stop:     ${a.get('stop', 0):.2f}",
        f"▸ Target:   ${a.get('target', 0):.2f}",
        f"▸ Score:    {round(a.get('score', 0))}/100",
    ]

    or_high = a.get("or_high")
    or_low  = a.get("or_low")
    if or_high is not None and or_low is not None:
        lines.append(f"── Opening Range ──────────────")
        lines.append(f"▸ OR High:  ${or_high:.2f}  |  OR Low: ${or_low:.2f}")
        t1, t2, t3 = a.get("or_t1"), a.get("or_t2"), a.get("or_t3")
        if t1 is not None:
            hit = a.get("or_target_hit")
            hit_str = f"  ← {hit} hit" if hit else ""
            lines.append(f"▸ Targets:  T1 ${t1:.2f}  T2 ${t2:.2f}  T3 ${t3:.2f}{hit_str}")

    risk_score = a.get("risk_score")
    if risk_score:
        _scales = {"LOW": "▰▰▱▱▱", "MEDIUM": "▰▰▰▱▱", "HIGH": "▰▰▰▰▰"}
        scale = _scales.get(risk_score, "▱▱▱▱▱")
        lines.append(f"▸ Risk: {scale} {risk_score}  |  IV: {a.get('risk_iv_label','?')}  |  Premium: {a.get('risk_premium_label','?')}")

    lines.append("⚡ MAX TRADE: $100")
    return "\n".join(lines)
