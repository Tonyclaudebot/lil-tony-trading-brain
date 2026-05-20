from datetime import datetime

from scanner.strategies.base import TradePlan


def format_html_alert(plan: TradePlan) -> str:
    is_call = plan.opt_type == "call"
    accent      = "#00C853" if is_call else "#FF3D3D"
    badge_bg    = "#0a2016" if is_call else "#200a0a"
    target_bg   = "#0d1f12" if is_call else "#1f0d0d"
    direction   = "&#9650;&nbsp;BULLISH CALL" if is_call else "&#9660;&nbsp;BEARISH PUT"

    conf_pct    = 85 if plan.confidence == "HIGH" else 50
    conf_color  = "#00C853" if plan.confidence == "HIGH" else "#FFD700"
    conf_empty  = 100 - conf_pct

    exp_display  = _fmt_date(plan.expiration)
    target_pct   = f"+{plan.target_pct:.0f}%"
    webull_url   = f"https://www.webull.com/quote/{plan.ticker}"

    parts = [
        _head(),
        _header(),
        _ticker_block(plan, accent, badge_bg, direction, exp_display),
        _price_table(plan, target_pct, accent, target_bg),
        _confidence_bar(plan, conf_pct, conf_empty, conf_color),
    ]

    if plan.earnings_warning:
        parts.append(_earnings_section(plan))

    if plan.binary_event_flag:
        parts.append(_binary_event_section(plan))
    elif plan.macro_warning:
        parts.append(_macro_section(plan))

    if plan.risk_score:
        parts.append(_risk_section(plan))

    if plan.news_context:
        parts.append(_news_section(plan))

    parts += [
        _webull_button(webull_url, plan.ticker),
        _footer(),
        _tail(),
    ]

    return "".join(parts)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%b %-d")
    except ValueError:
        return iso_date


def _td(style=""):
    return f' style="{style}"' if style else ""


# ── Sections ──────────────────────────────────────────────────────────────────

def _head() -> str:
    return """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Lil Tony Alert</title>
<style type="text/css">
  body { margin:0; padding:0; background-color:#1a1a1a; -webkit-text-size-adjust:100%; }
  img  { border:0; display:block; }
  @media only screen and (max-width:620px) {
    .wrapper  { width:100% !important; }
    .ticker   { font-size:52px !important; }
    .pcol     { display:block !important; width:100% !important; box-sizing:border-box; }
    .btn      { width:90% !important; }
  }
</style>
</head>
<body bgcolor="#1a1a1a" style="margin:0;padding:0;background-color:#1a1a1a;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#1a1a1a">
<tr><td align="center" style="padding:20px 10px;">
<table class="wrapper" width="600" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;width:100%;border-radius:14px;overflow:hidden;
              box-shadow:0 8px 40px rgba(255,215,0,0.15);">
"""


def _header() -> str:
    return """\
<!-- HEADER -->
<tr>
  <td bgcolor="#FFD700" align="center"
      style="background:#FFD700;padding:18px 24px;border-radius:14px 14px 0 0;">
    <p style="margin:0;font-family:Arial Black,Arial,sans-serif;font-size:13px;
              font-weight:900;color:#1a1a1a;letter-spacing:5px;
              text-transform:uppercase;">&#9889;&nbsp;LIL TONY TRADING BRAIN&nbsp;&#9889;</p>
  </td>
</tr>
"""


def _ticker_block(plan, accent, badge_bg, direction, exp_display) -> str:
    iv_str  = f"IV {plan.iv:.0f}%" if plan.iv else ""
    spot_str = f"Spot ${plan.spot:.2f}&nbsp;&nbsp;·&nbsp;&nbsp;" if plan.spot else ""
    return f"""\
<!-- TICKER BLOCK -->
<tr>
  <td bgcolor="#111111" style="background:#111111;padding:28px 28px 20px;">
    <!-- Direction badge + meta -->
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td>
          <span style="display:inline-block;background:{badge_bg};color:{accent};
                       font-family:Arial,sans-serif;font-size:11px;font-weight:700;
                       letter-spacing:2px;padding:5px 14px;border-radius:20px;
                       border:1px solid {accent};">{direction}</span>
        </td>
        <td align="right">
          <span style="font-family:Arial,sans-serif;font-size:11px;color:#555;">
            {spot_str}{iv_str}
          </span>
        </td>
      </tr>
    </table>
    <!-- Big ticker -->
    <p class="ticker"
       style="margin:16px 0 2px;font-family:Arial Black,Arial,sans-serif;
              font-size:68px;font-weight:900;color:#FFFFFF;letter-spacing:-2px;
              line-height:1;">{plan.ticker}</p>
    <!-- Strategy -->
    <p style="margin:0 0 10px;font-family:Arial,sans-serif;font-size:13px;
              color:#FFD700;font-weight:700;letter-spacing:2px;
              text-transform:uppercase;">{plan.strategy_name}</p>
    <!-- Contract line -->
    <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#666;">
      Buy&nbsp;<strong style="color:#ccc;">${plan.strike:.0f}&nbsp;{plan.opt_type.upper()}</strong>
      &nbsp;&nbsp;·&nbsp;&nbsp;exp&nbsp;<strong style="color:#ccc;">{exp_display}</strong>
      &nbsp;&nbsp;·&nbsp;&nbsp;{plan.dte}d to expiry
    </p>
  </td>
</tr>
"""


def _price_table(plan, target_pct, accent, target_bg) -> str:
    return f"""\
<!-- PRICE TABLE -->
<tr>
  <td bgcolor="#111111" style="background:#111111;padding:0;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <!-- ENTRY -->
        <td class="pcol" width="33%" align="center" bgcolor="#111111"
            style="padding:22px 8px;border-right:1px solid #2a2a2a;background:#111111;">
          <p style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:9px;
                    color:#555;letter-spacing:3px;text-transform:uppercase;">Entry</p>
          <p style="margin:0;font-family:Arial Black,Arial,sans-serif;font-size:28px;
                    font-weight:900;color:#FFD700;">${plan.entry:.2f}</p>
          <p style="margin:4px 0 0;font-family:Arial,sans-serif;font-size:11px;color:#555;">
            per contract</p>
        </td>
        <!-- TARGET -->
        <td class="pcol" width="33%" align="center" bgcolor="{target_bg}"
            style="padding:22px 8px;border-right:1px solid #2a2a2a;background:{target_bg};">
          <p style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:9px;
                    color:#00C853;letter-spacing:3px;text-transform:uppercase;">Target</p>
          <p style="margin:0;font-family:Arial Black,Arial,sans-serif;font-size:28px;
                    font-weight:900;color:#00C853;">${plan.target:.2f}</p>
          <p style="margin:4px 0 0;font-family:Arial,sans-serif;font-size:12px;
                    color:#00C853;font-weight:700;">{target_pct}</p>
        </td>
        <!-- STOP -->
        <td class="pcol" width="33%" align="center" bgcolor="#1f0a0a"
            style="padding:22px 8px;background:#1f0a0a;">
          <p style="margin:0 0 6px;font-family:Arial,sans-serif;font-size:9px;
                    color:#FF3D3D;letter-spacing:3px;text-transform:uppercase;">Stop</p>
          <p style="margin:0;font-family:Arial Black,Arial,sans-serif;font-size:28px;
                    font-weight:900;color:#FF3D3D;">${plan.stop:.2f}</p>
          <p style="margin:4px 0 0;font-family:Arial,sans-serif;font-size:12px;
                    color:#FF3D3D;font-weight:700;">&#8722;50%</p>
        </td>
      </tr>
    </table>
  </td>
</tr>
"""


def _confidence_bar(plan, conf_pct, conf_empty, conf_color) -> str:
    return f"""\
<!-- CONFIDENCE BAR -->
<tr>
  <td bgcolor="#111111" style="background:#111111;padding:20px 28px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="font-family:Arial,sans-serif;font-size:10px;color:#666;
                   letter-spacing:3px;text-transform:uppercase;padding-bottom:8px;">
          Confidence
        </td>
        <td align="right" style="font-family:Arial Black,Arial,sans-serif;
                                  font-size:13px;font-weight:900;color:{conf_color};
                                  letter-spacing:2px;padding-bottom:8px;">
          {plan.confidence}
        </td>
      </tr>
    </table>
    <!-- Bar track -->
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="border-radius:6px;overflow:hidden;">
      <tr>
        <td width="{conf_pct}%" height="10" bgcolor="{conf_color}"
            style="background:{conf_color};border-radius:6px 0 0 6px;"></td>
        <td width="{conf_empty}%" height="10" bgcolor="#2a2a2a"
            style="background:#2a2a2a;border-radius:0 6px 6px 0;"></td>
      </tr>
    </table>
  </td>
</tr>
"""


def _earnings_section(plan) -> str:
    is_high = plan.earnings_proximity_risk == "HIGH"
    bg      = "#1f0a00" if is_high else "#1a1600"
    border  = "#FF6B00" if is_high else "#FFD700"
    title_c = "#FF6B00" if is_high else "#FFD700"
    label   = "&#9888;&nbsp;EARNINGS RISK" if is_high else "&#128197;&nbsp;EARNINGS NOTICE"

    beat_row = ""
    if plan.earnings_beat_rate is not None:
        pct = int(plan.earnings_beat_rate * 100)
        avg = f"&nbsp;&nbsp;·&nbsp;&nbsp;avg &#177;{plan.earnings_avg_move}% move" if plan.earnings_avg_move else ""
        beat_row = (
            f'<p style="margin:6px 0 0;font-family:Arial,sans-serif;font-size:12px;color:#aaa;">'
            f'Historical beat rate: <strong style="color:#fff;">{pct}%</strong>{avg}</p>'
        )

    days_str = f"{plan.days_to_earnings}d away" if plan.days_to_earnings is not None else ""
    confirm  = ('<p style="margin:10px 0 0;font-family:Arial,sans-serif;font-size:12px;'
                'color:#FF6B00;font-weight:700;">&#9888;&nbsp;Big Tony must confirm before trading</p>'
                if is_high else "")

    return f"""\
<!-- EARNINGS SECTION -->
<tr>
  <td bgcolor="{bg}" style="background:{bg};padding:18px 28px;
      border-left:3px solid {border};border-top:1px solid #2a2a2a;">
    <p style="margin:0 0 4px;font-family:Arial Black,Arial,sans-serif;font-size:11px;
              font-weight:900;color:{title_c};letter-spacing:2px;">{label}</p>
    <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#fff;font-weight:600;">
      Earnings in <strong style="color:{title_c};">{days_str}</strong>
      &nbsp;·&nbsp; IV crush risk if holding through report
    </p>
    {beat_row}
    {confirm}
  </td>
</tr>
"""


def _binary_event_section(plan) -> str:
    detail = ""
    if plan.binary_event_detail:
        lines = plan.binary_event_detail.replace("\n", "<br>")
        detail = f'<p style="margin:8px 0 0;font-family:Arial,sans-serif;font-size:12px;color:#ffaaaa;">{lines}</p>'

    return f"""\
<!-- BINARY EVENT -->
<tr>
  <td bgcolor="#2b0000" style="background:#2b0000;padding:18px 28px;
      border-left:3px solid #FF3D3D;border-top:1px solid #3a0000;">
    <p style="margin:0 0 6px;font-family:Arial Black,Arial,sans-serif;font-size:12px;
              font-weight:900;color:#FF3D3D;letter-spacing:2px;">
      &#9888;&nbsp;BINARY EVENT &mdash; CONFIRMATION REQUIRED
    </p>
    <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#fff;">
      This position spans a major binary event. Reply to confirm or skip this trade.
    </p>
    {detail}
  </td>
</tr>
"""


def _macro_section(plan) -> str:
    lines = (plan.macro_warning or "").replace("\n", "<br>")
    return f"""\
<!-- MACRO WARNING -->
<tr>
  <td bgcolor="#1a1500" style="background:#1a1500;padding:18px 28px;
      border-left:3px solid #FFD700;border-top:1px solid #2a2a2a;">
    <p style="margin:0 0 6px;font-family:Arial Black,Arial,sans-serif;font-size:11px;
              font-weight:900;color:#FFD700;letter-spacing:2px;">
      &#128197;&nbsp;MACRO EVENT ALERT
    </p>
    <p style="margin:0;font-family:Arial,sans-serif;font-size:13px;color:#ddd;">{lines}</p>
  </td>
</tr>
"""


def _risk_section(plan) -> str:
    risk_colors = {"LOW": "#00C853", "MEDIUM": "#FFD700", "HIGH": "#FF3D3D"}
    label_colors = {"LOW": "#00C853", "MEDIUM": "#FFD700", "HIGH": "#FF3D3D", "CHEAP": "#00C853", "FAIR": "#FFD700", "EXPENSIVE": "#FF3D3D"}
    rc = risk_colors.get(plan.risk_score, "#888")
    safe_summary = (plan.risk_summary or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    filled_counts = {"LOW": 2, "MEDIUM": 3, "HIGH": 5}
    n_filled = filled_counts.get(plan.risk_score, 0)
    segs = "".join(
        f'<td width="28" height="10" bgcolor="{rc if i < n_filled else "#2a2a2a"}" '
        f'style="background:{rc if i < n_filled else "#2a2a2a"};border-radius:3px;"></td>'
        for i in range(5)
    )

    ic = label_colors.get(plan.risk_iv_label, "#888")
    pc = label_colors.get(plan.risk_premium_label, "#888")
    iv_str   = f'<span style="color:{ic};font-weight:700;">{plan.risk_iv_label}</span>'
    prem_str = f'<span style="color:{pc};font-weight:700;">{plan.risk_premium_label}</span>'

    return f"""\
<!-- RISK SCALE -->
<tr>
  <td bgcolor="#0e0e0e" style="background:#0e0e0e;padding:14px 28px;
      border-top:1px solid #2a2a2a;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="font-family:Arial,sans-serif;font-size:9px;color:#555;
                   letter-spacing:2px;text-transform:uppercase;padding-right:12px;
                   white-space:nowrap;">Risk</td>
        <td style="padding-right:10px;">
          <table cellpadding="0" cellspacing="3" border="0"><tr>{segs}</tr></table>
        </td>
        <td style="font-family:Arial Black,Arial,sans-serif;font-size:13px;
                   font-weight:900;color:{rc};padding-right:16px;white-space:nowrap;">{plan.risk_score}</td>
        <td align="right" style="font-family:Arial,sans-serif;font-size:11px;color:#555;white-space:nowrap;">
          IV: {iv_str}&nbsp;&nbsp;·&nbsp;&nbsp;Premium: {prem_str}
        </td>
      </tr>
    </table>
    {f'<p style="margin:6px 0 0;font-family:Arial,sans-serif;font-size:10px;color:#555;font-style:italic;">{safe_summary}</p>' if safe_summary else ""}
  </td>
</tr>
"""


def _news_section(plan) -> str:
    items = ""
    for headline in plan.news_context:
        safe = headline.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        items += (
            f'<tr><td style="padding:6px 0;border-bottom:1px solid #222;">'
            f'<span style="display:inline-block;background:#1a2a3a;color:#4A90E2;'
            f'font-family:Arial,sans-serif;font-size:9px;font-weight:700;'
            f'letter-spacing:1px;padding:2px 7px;border-radius:3px;margin-right:8px;">'
            f'NEWS</span>'
            f'<span style="font-family:Arial,sans-serif;font-size:12px;color:#ccc;">'
            f'{safe}</span></td></tr>'
        )

    return f"""\
<!-- NEWS SECTION -->
<tr>
  <td bgcolor="#0e1520" style="background:#0e1520;padding:18px 28px;
      border-top:1px solid #2a2a2a;">
    <p style="margin:0 0 12px;font-family:Arial Black,Arial,sans-serif;font-size:10px;
              font-weight:900;color:#4A90E2;letter-spacing:3px;text-transform:uppercase;">
      &#128240;&nbsp;Latest News
    </p>
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      {items}
    </table>
  </td>
</tr>
"""


def _webull_button(webull_url, ticker) -> str:
    return f"""\
<!-- WEBULL BUTTON -->
<tr>
  <td bgcolor="#111111" style="background:#111111;padding:28px;
      text-align:center;border-top:1px solid #2a2a2a;">
    <a class="btn" href="{webull_url}" target="_blank"
       style="display:inline-block;background:#FFD700;color:#1a1a1a;
              font-family:Arial Black,Arial,sans-serif;font-size:15px;
              font-weight:900;letter-spacing:2px;text-decoration:none;
              padding:16px 40px;border-radius:8px;text-transform:uppercase;
              min-width:260px;box-sizing:border-box;">
      &#9889;&nbsp;Open {ticker} in Webull
    </a>
    <p style="margin:12px 0 0;font-family:Arial,sans-serif;font-size:11px;color:#444;">
      Paper trade manually &mdash; no API required
    </p>
  </td>
</tr>
"""


def _footer() -> str:
    from datetime import date
    today = date.today().strftime("%B %-d, %Y")
    return f"""\
<!-- FOOTER -->
<tr>
  <td bgcolor="#1a1a1a" align="center"
      style="background:#1a1a1a;padding:20px 28px;border-top:1px solid #2a2a2a;
             border-radius:0 0 14px 14px;">
    <p style="margin:0;font-family:Arial,sans-serif;font-size:11px;color:#333;">
      Powered by&nbsp;<strong style="color:#FFD700;">Lil Tony</strong>
      &nbsp;&middot;&nbsp;Big Tony's House Rules
      &nbsp;&middot;&nbsp;{today}
    </p>
    <p style="margin:6px 0 0;font-family:Arial,sans-serif;font-size:10px;color:#222;">
      Not financial advice &mdash; for informational and paper trading use only.
    </p>
  </td>
</tr>
"""


def _tail() -> str:
    return """\
</table>
</td></tr>
</table>
</body>
</html>
"""
