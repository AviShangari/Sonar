"""The ONLY place secrets are read. They come from environment variables or the gitignored .env file at the repo root."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
SMTP_PASSWORD_VAR = "SONAR_SMTP_PASSWORD"


def smtp_password() -> str:
    """The password (or app password) for the sending mailbox. A real environment variable wins over .env."""
    load_dotenv(ENV_FILE, override=False)
    value = os.environ.get(SMTP_PASSWORD_VAR)
    if not value:
        raise RuntimeError(f"{SMTP_PASSWORD_VAR} is not set; put it in .env or set it in the environment before sending email.")
    return value
