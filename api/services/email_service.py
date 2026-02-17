"""Verification email for API key signup."""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from api.config import get_settings


def send_verification_email(email: str, token: str) -> None:
    """Send API key verification email."""
    settings = get_settings()

    verify_url = f"{settings.public_url}/developer/verify?token={token}"

    html = f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
  <h2 style="color: #333; margin-bottom: 8px;">UN Digital Library API</h2>
  <p style="color: #666; margin-bottom: 24px;">Verify your email to get your API key.</p>
  <a href="{verify_url}" style="display: inline-block; background: #009edb; color: white; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: 600;">
    Verify &amp; Get API Key
  </a>
  <p style="color: #999; font-size: 13px; margin-top: 24px;">
    This link expires in 1 hour. If you didn't request an API key, ignore this email.
  </p>
</div>
"""
    text = f"Verify your email to get your UN Digital Library API key:\n\n{verify_url}\n\nThis link expires in 1 hour."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Verify your email — UN Digital Library API"
    msg["From"] = settings.smtp_from
    msg["To"] = email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        if settings.smtp_user and settings.smtp_pass:
            server.login(settings.smtp_user, settings.smtp_pass)
        server.sendmail(settings.smtp_from, [email], msg.as_string())
