"""Email dispatch with pluggable provider: SendGrid, generic SMTP (e.g. Gmail), or dev stub.

Provider is chosen by ``settings.email_provider``:
  * ``"sendgrid"`` — uses the SendGrid HTTP API (requires SENDGRID_API_KEY)
  * ``"smtp"``     — uses stdlib smtplib (requires SMTP_HOST, SMTP_USER, SMTP_PASSWORD)
  * ``""``          — dev stub: logs the message and returns (used in local dev / tests)

For backwards compatibility, if ``email_provider`` is empty but ``sendgrid_api_key``
is set, the SendGrid path is still chosen automatically.

Failures are logged but never raised — auth flows must not 500 when the email
provider flaps.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings
from app.logging_config import log


# ---------- provider selection ----------

def _resolved_provider() -> str:
    provider = (settings.email_provider or "").strip().lower()
    if provider:
        return provider
    # back-compat: legacy deploys that only set SENDGRID_API_KEY
    if settings.sendgrid_api_key:
        return "sendgrid"
    return ""


# ---------- provider implementations ----------

def _send_via_sendgrid(to_email: str, subject: str, html: str) -> None:
    if not settings.sendgrid_api_key:
        log.warning("email_sendgrid_misconfigured", to=to_email)
        return
    # Lazy import so SMTP-only deployments don't need the sendgrid package installed.
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Content, Email, Mail, To
    except ImportError:
        log.error("email_send_failed", provider="sendgrid", to=to_email, error="sendgrid package not installed")
        return
    message = Mail(
        from_email=Email(settings.email_from_address, settings.email_from_name),
        to_emails=To(to_email),
        subject=subject,
    )
    message.add_content(Content("text/html", html))
    try:
        SendGridAPIClient(settings.sendgrid_api_key).send(message)
    except Exception as e:  # pragma: no cover — upstream failures
        log.error("email_send_failed", provider="sendgrid", to=to_email, error=str(e))


def _send_via_smtp(to_email: str, subject: str, html: str) -> None:
    missing = [
        name
        for name, value in (
            ("SMTP_HOST", settings.smtp_host),
            ("SMTP_USER", settings.smtp_user),
            ("SMTP_PASSWORD", settings.smtp_password),
        )
        if not value
    ]
    if missing:
        log.warning("email_smtp_misconfigured", to=to_email, missing=missing)
        return

    msg = EmailMessage()
    msg["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    # Provide a plain-text fallback for clients that won't render HTML.
    msg.set_content("This message requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")

    host = settings.smtp_host
    port = settings.smtp_port
    timeout = settings.smtp_timeout_seconds

    try:
        if port == 465:
            # Implicit TLS (SMTPS) — used by Gmail on :465 and most legacy servers.
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as client:
                client.login(settings.smtp_user, settings.smtp_password)
                client.send_message(msg)
        else:
            # STARTTLS on :587 (Gmail's recommended path) or plain on :25.
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.ehlo()
                if settings.smtp_use_tls:
                    ctx = ssl.create_default_context()
                    client.starttls(context=ctx)
                    client.ehlo()
                client.login(settings.smtp_user, settings.smtp_password)
                client.send_message(msg)
    except Exception as e:  # pragma: no cover — upstream failures
        log.error("email_send_failed", provider="smtp", host=host, port=port, to=to_email, error=str(e))


# ---------- public API ----------

def _send(to_email: str, subject: str, html: str) -> None:
    provider = _resolved_provider()
    if provider == "sendgrid":
        _send_via_sendgrid(to_email, subject, html)
    elif provider == "smtp":
        _send_via_smtp(to_email, subject, html)
    else:
        # Dev / CI stub — just log so tests + local runs can verify the call happened.
        log.info("email_stub", to=to_email, subject=subject, body_preview=html[:200])


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
