"""
send_alert.py  —  Lil Tony Trading Brain
Sends dark flyer alert emails via Resend.
"""

import os
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
ALERT_TO       = os.getenv("ALERT_TO", "tpm7130@gmail.com")
ALERT_FROM     = os.getenv("ALERT_FROM", "Lil Tony Alerts <alerts@bigtonyalerts.com>")
TEMPLATE_PATH  = Path(__file__).parent / "lil_tony_alert_template.html"


def _calc_risk_reward(entry_str, target_str, stop_str):
    try:
        entry  = float(entry_str.replace("$","").replace(",",""))
        target = float(target_str.replace("$","").replace(",",""))
        stop   = float(stop_str.replace("$","").replace(",",""))
        reward = target - entry
        risk   = entry - stop
        if risk <= 0:
            return "N/A"
        return f"1 : {round(reward / risk, 1)}"
    except:
        return "N/A"


def _fill_template(data: dict) -> str:
    template  = TEMPLATE_PATH.read_text(encoding="utf-8")
    timestamp = datetime.now().strftime("%b %d, %Y · %I:%M %p")
    ct = (data.get("contract_type") or "CALL").upper()

    replacements = {
        "{{TIMESTAMP}}":       timestamp,
        "{{TICKER}}":          data["ticker"],
        "{{CONTRACT_TYPE}}":   ct,
        "{{DIRECTION_CLASS}}": "put" if ct == "PUT" else "call",
        "{{STRIKE}}":          data.get("strike", "--"),
        "{{EXPIRY}}":          data.get("expiry", "--"),
        "{{ENTRY}}":           data["entry"],
        "{{STOP}}":            data["stop"],
        "{{TARGET}}":          data["target"],
        "{{SCORE}}":           data.get("score", "--"),
    }

    for placeholder, value in replacements.items():
        template = template.replace(placeholder, str(value))
    return template


def _build_plain_text(data: dict) -> str:
    timestamp = datetime.now().strftime("%b %d, %Y · %I:%M %p")
    ct = (data.get("contract_type") or "CALL").upper()
    lines = [
        f"🔥 Lil Tony Alert — {timestamp}",
        "",
        f"{data['ticker']}  |  {ct}",
        "",
        f"Strike:  {data.get('strike', '--')}",
        f"Exp:     {data.get('expiry', '--')}",
        f"Entry:   {data['entry']}",
        f"Stop:    {data['stop']}",
        f"Target:  {data['target']}",
        f"Score:   {data.get('score', '--')}/100",
    ]
    if data.get("risk_score"):
        _scales = {"LOW": "▰▰▱▱▱", "MEDIUM": "▰▰▰▱▱", "HIGH": "▰▰▰▰▰"}
        scale = _scales.get(data["risk_score"], "▱▱▱▱▱")
        lines.append(f"Risk: {scale} {data['risk_score']}  |  IV: {data.get('risk_iv_label','?')}  |  Premium: {data.get('risk_premium_label','?')}")
    lines += ["", "⚡ MAX TRADE: $100"]
    return "\n".join(lines)


def send_alert(data: dict) -> bool:
    ticker  = data["ticker"]
    ct      = (data.get("contract_type") or "CALL").upper()
    subject = f"🔥 {ticker}  |  {ct} — Lil Tony Alert"
    html_body  = _fill_template(data)
    plain_body = _build_plain_text(data)

    payload = {
        "from":    ALERT_FROM,
        "to":      [ALERT_TO],
        "subject": subject,
        "html":    html_body,
        "text":    plain_body,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type":  "application/json",
    }

    try:
        r = requests.post("https://api.resend.com/emails", json=payload, headers=headers)
        if r.status_code == 200:
            print(f"[Lil Tony] ✅ Alert sent → {ticker} | {ct}")
            return True
        else:
            print(f"[Lil Tony] ❌ Resend error {r.status_code}: {r.text}")
            return False
    except Exception as e:
        print(f"[Lil Tony] ❌ Send failed: {e}")
        return False


if __name__ == "__main__":
    send_alert({
        "ticker":        "NVDA",
        "strategy":      "Momentum Breakout",
        "signal":        "BUY",
        "contract_type": "CALL",
        "contract":      "$900C",
        "expiry":        "exp 05/23",
        "entry":         "$0.65",
        "target":        "$3.80",
        "pct_gain":      "+484%",
        "stop":          "$0.25",
        "confidence":    "HIGH",
        "broker_link":   "https://robinhood.com/options/NVDA",
    })
