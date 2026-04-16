"""Email dispatch via SendGrid. In dev (no API key), prints links to stdout."""
from __future__ import annotations

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Content, Email, Mail, To

from app.config import settings
from app.logging_config import log


def _send(to_email: str, subject: str, html: str) -> None:
    if not settings.sendgrid_api_key:
        log.info("email_stub", to=to_email, subject=subject, body_preview=html[:200])
        return
    message = Mail(
        from_email=Email(settings.email_from_address, settings.email_from_name),
        to_emails=To(to_email),
        subject=subject,
    )
    message.add_content(Content("text/html", html))
    try:
        client = SendGridAPIClient(settings.sendgrid_api_key)
        client.send(message)
    except Exception as e:  # pragma: no cover
        log.error("email_send_failed", to=to_email, error=str(e))


def send_verification_email(to_email: str, token: str) -> None:
    link = f"{settings.frontend_url}/verify-email?token={token}"
    html = f"""
      <h2>Welcome to TradeCore</h2>
      <p>Click the link below to verify your email address:</p>
      <p><a href="{link}">Verify my email</a></p>
      <p>This link expires in 24 hours.</p>
    """
    _send(to_email, "Verify your TradeCore email", html)


def send_password_reset_email(to_email: str, token: str) -> None:
    link = f"{settings.frontend_url}/reset-password?token={token}"
    html = f"""
      <h2>Reset your TradeCore password</h2>
      <p>Click the link below to choose a new password:</p>
      <p><a href="{link}">Reset password</a></p>
      <p>This link expires in 1 hour. If you didn't request this, ignore the email.</p>
    """
    _send(to_email, "Reset your TradeCore password", html)
