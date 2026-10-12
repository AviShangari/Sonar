"""Sends the plain-text report by email over SMTP (stdlib only). Delivery is separate from the report itself."""

import smtplib
import ssl
from email.message import EmailMessage

from sonar.config import Settings
from sonar.models import Report
from sonar.report.render import render_email_text
from sonar.secrets import smtp_password

SMTP_TIMEOUT_SECONDS = 30


def build_message(report: Report, settings: Settings) -> EmailMessage:
    count = len(report.matches)
    subject = f"Sonar: {count} {'match' if count == 1 else 'matches'} (run {report.run_id})"
    if report.status != "ok":
        subject += f" [{report.status}]"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.email_from
    message["To"] = settings.email_to
    message.set_content(render_email_text(report, settings.email_snippets))
    return message


def send_report(report: Report, settings: Settings) -> None:
    """Send the report. Raises on any failure; the caller decides that a failed email must not fail the run."""
    if not (settings.smtp_host and settings.email_from and settings.email_to):
        raise RuntimeError("email is on but smtp_host, email_from or email_to is missing in config/settings.yaml")
    message = build_message(report, settings)
    password = smtp_password()
    login = settings.smtp_username or settings.email_from
    context = ssl.create_default_context()
    if settings.smtp_port == 465:  # TLS from the first byte
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context) as server:
            server.login(login, password)
            server.send_message(message)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.starttls(context=context)  # never log in over an unencrypted connection
        server.login(login, password)
        server.send_message(message)
