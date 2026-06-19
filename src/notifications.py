"""Optional SMTP notification; discovery never requires email configuration."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Mapping


def send_optional_email(subject: str, body: str, env: Mapping[str, str] | None = None) -> str:
    values = env or os.environ
    required = ("SMTP_HOST", "SMTP_FROM", "SMTP_TO")
    if not all(values.get(key) for key in required):
        return "SMTP not configured; local summary generated without email."
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = values["SMTP_FROM"]
    message["To"] = values["SMTP_TO"]
    message.set_content(body)
    host = values["SMTP_HOST"]
    port = int(values.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=20) as client:
        if values.get("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}:
            client.starttls()
        if values.get("SMTP_USERNAME") and values.get("SMTP_PASSWORD"):
            client.login(values["SMTP_USERNAME"], values["SMTP_PASSWORD"])
        client.send_message(message)
    return "Discovery summary email sent."

