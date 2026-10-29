"""Handing a PDF to a person in Teams. A bot cannot just attach a file in a chat. In a one-to-one chat Teams uses a
"file consent" card: the bot offers the file, the person clicks Allow, Teams then gives the bot a one-time upload
address in the person's own OneDrive, and the bot sends the file there. Channels and group chats have no such
card (files there need SharePoint and admin-approved Graph permissions), so the bot says so instead.

The card carries only a random token. The file, the person and the chat are looked up from our own record of what
we offered, so nothing in a click can point the bot at another file. An answer to a card is only honored for the
person it was offered to, in the chat it was offered in, within 30 minutes."""

import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from microsoft_teams.api import Attachment, MessageActivityInput
from microsoft_teams.api.models import FileConsentCard, FileInfoCard, FileUploadInfo

log = logging.getLogger("sonar.teams")

CONSENT_TYPE = "application/vnd.microsoft.teams.card.file.consent"
INFO_TYPE = "application/vnd.microsoft.teams.card.file.info"
OFFER_MINUTES = 30
MAX_OFFERS = 50  # files waiting for a click; the oldest is dropped first
UPLOAD_HOST_SUFFIXES = (".sharepoint.com", ".sharepoint.us", ".sharepoint.de", ".sharepoint.cn")  # where Teams stores a person's files
UPLOAD_TIMEOUT_S = 60

NOT_IN_CHANNELS = (
    "I made the PDF, but Teams only lets me send files in a one-to-one chat, not in a channel or group chat. "
    "Message me directly and ask again, and I will send it there."
)
DECLINED = "No problem, I won't send it. Ask again any time."
UPLOAD_FAILED = "Sorry, I couldn't send the PDF just now. Please ask me again."

Uploader = Callable[[str, bytes], Awaitable[None]]


@dataclass
class Offer:
    path: Path
    user: str
    conversation: str
    expires: float


def allowed_upload_url(url: str | None) -> bool:
    """Teams' upload addresses are https links into the person's SharePoint/OneDrive storage."""
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname.lower().endswith(UPLOAD_HOST_SUFFIXES)


async def upload_to_teams(url: str, data: bytes) -> None:
    """Send the file to the one-time address Teams gave us. No credentials of ours go with it."""
    headers = {"Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}"}
    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_S, follow_redirects=False) as client:
        response = await client.put(url, content=data, headers=headers)
        response.raise_for_status()


class FileDelivery:
    def __init__(self, uploader: Uploader = upload_to_teams) -> None:
        self.uploader = uploader
        self.offers: dict[str, Offer] = {}

    def offer(self, path: Path, user: str, conversation: str) -> MessageActivityInput:
        """The message that offers a file; remembers it until the person answers."""
        now = time.monotonic()
        self.offers = {t: o for t, o in self.offers.items() if o.expires > now}
        while len(self.offers) >= MAX_OFFERS:
            self.offers.pop(next(iter(self.offers)))
        token = secrets.token_urlsafe(16)
        self.offers[token] = Offer(path, user, conversation, now + OFFER_MINUTES * 60)
        card = FileConsentCard(
            description="Sonar PDF report", size_in_bytes=path.stat().st_size,
            accept_context={"token": token}, decline_context={"token": token},
        )
        attachment = Attachment(content_type=CONSENT_TYPE, name=path.name, content=card)
        return MessageActivityInput(text="Here is your PDF. Allow it and I will send it to you.").add_attachments(attachment)

    def take(self, context: object, user: str, conversation: str) -> Offer | None:
        """The offer a card answer belongs to, once. None when the token is unknown, expired or someone else's."""
        token = context.get("token") if isinstance(context, dict) else None
        if not isinstance(token, str):
            return None
        offer = self.offers.get(token)
        if offer is None or offer.expires <= time.monotonic():
            self.offers.pop(token, None)
            return None
        if offer.user != user or offer.conversation != conversation:
            log.warning("step=teams_file outcome=wrong_person")
            return None
        del self.offers[token]
        return offer

    async def send(self, offer: Offer, info: FileUploadInfo | None) -> MessageActivityInput | None:
        """Upload the offered file to the address Teams gave; the message that shows the file in the chat, or None."""
        if info is None or not allowed_upload_url(info.upload_url):
            log.warning("step=teams_file outcome=bad_upload_address host=%s", urlparse(info.upload_url or "").hostname if info else None)
            return None
        try:
            await self.uploader(info.upload_url, offer.path.read_bytes())
        except (httpx.HTTPError, OSError):
            log.exception("step=teams_file outcome=upload_failed")
            return None
        card = FileInfoCard(unique_id=info.unique_id, file_type=info.file_type)
        attachment = Attachment(content_type=INFO_TYPE, content_url=info.content_url, name=info.name, content=card)
        return MessageActivityInput(text="").add_attachments(attachment)
