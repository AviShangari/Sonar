"""Handing a PDF to a person in Teams: the file offer in a one-to-one chat, the click on it, and what is refused.
Teams and the upload are recording fakes; no network, no cost."""

import asyncio
import json

import httpx
import pytest
from microsoft_teams.api import FileConsentInvokeActivity, MessageActivityInput

from sonar.teams import delivery as teams_delivery
from sonar.teams.bot import SonarBot, build_app
from sonar.teams.delivery import (
    CONSENT_TYPE, DECLINED, INFO_TYPE, MAX_OFFERS, NOT_IN_CHANNELS, UPLOAD_FAILED, FileDelivery, allowed_upload_url,
)
from test_teams import BOT_ID, TENANT, FakeAssistant, FakeContext, deliver, make_bot, message, settings

UPLOAD_URL = "https://contoso-my.sharepoint.com/personal/someone/_api/v2.0/drives/b!x/items/y/uploadSession?guid=1"


class FakeUploader:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.error = error

    async def __call__(self, url: str, data: bytes) -> None:
        self.calls.append((url, data))
        if self.error:
            raise self.error


@pytest.fixture()
def pdf(tmp_path):
    path = tmp_path / "sonar-run-4.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return path


def consent_answer(token, action="accept", user="user-1", tenant=TENANT, conversation="conv-1", upload_url=UPLOAD_URL, context=None):
    return FileConsentInvokeActivity.model_validate({
        "type": "invoke", "name": "fileConsent/invoke", "id": "i1", "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/teams/", "from": {"id": "29:u", "aadObjectId": user},
        "recipient": {"id": BOT_ID, "name": "Sonar"},
        "conversation": {"id": conversation, "tenantId": tenant, "conversationType": "personal"},
        "value": {
            "action": action, "context": context if context is not None else {"token": token},
            "uploadInfo": {"name": "sonar-run-4.pdf", "uploadUrl": upload_url, "contentUrl": "https://contoso-my.sharepoint.com/f.pdf",
                           "uniqueId": "{ABC}", "fileType": "pdf"},
        },
    })


def offer_and_token(bot: SonarBot, pdf, user="user-1", conversation="conv-1") -> str:
    bot.delivery.offer(pdf, user, conversation)
    return next(reversed(bot.delivery.offers))


def answer_card(bot: SonarBot, activity) -> FakeContext:
    async def go() -> FakeContext:
        ctx = FakeContext(activity)
        await bot.on_file_consent(ctx)
        return ctx

    return asyncio.run(go())


def files_bot(pdf, uploader: FakeUploader | None = None) -> tuple[SonarBot, FakeUploader]:
    uploader = uploader or FakeUploader()
    fake = FakeAssistant("Your PDF is ready.")
    fake.files = [pdf]
    return SonarBot(settings(), TENANT, lambda: fake, FileDelivery(uploader)), uploader


def test_a_pdf_is_offered_as_a_file_in_a_one_to_one_chat(pdf):
    bot, _ = files_bot(pdf)
    ctx = deliver(bot, message("make me a pdf"))
    assert ctx.texts == ["Your PDF is ready."]
    offer = next(m for m in ctx.sent if isinstance(m, MessageActivityInput) and m.attachments)
    attachment = offer.attachments[0]
    assert attachment.content_type == CONSENT_TYPE and attachment.name == pdf.name
    assert attachment.content.size_in_bytes == pdf.stat().st_size
    token = attachment.content.accept_context["token"]
    assert bot.delivery.offers[token].path == pdf and bot.delivery.offers[token].user == "user-1"
    assert str(pdf) not in json.dumps(offer.model_dump(by_alias=True, exclude_none=True))  # the card holds no path


@pytest.mark.parametrize("kind", ["channel", "groupChat"])
def test_in_channels_and_group_chats_the_bot_explains_instead_of_offering(pdf, kind):
    bot, _ = files_bot(pdf)
    ctx = deliver(bot, message("<at>Sonar</at> make me a pdf", kind=kind, mention=True))
    assert ctx.texts == ["Your PDF is ready.", NOT_IN_CHANNELS] and bot.delivery.offers == {}


def test_allow_uploads_the_file_and_shows_it_in_the_chat(pdf):
    bot, uploader = files_bot(pdf)
    ctx = answer_card(bot, consent_answer(offer_and_token(bot, pdf)))
    assert uploader.calls == [(UPLOAD_URL, b"%PDF-1.4 fake")]
    shown = ctx.sent[0].attachments[0]
    assert shown.content_type == INFO_TYPE and shown.content_url == "https://contoso-my.sharepoint.com/f.pdf"
    assert shown.name == "sonar-run-4.pdf" and shown.content.unique_id == "{ABC}" and shown.content.file_type == "pdf"


def test_decline_sends_nothing(pdf):
    bot, uploader = files_bot(pdf)
    ctx = answer_card(bot, consent_answer(offer_and_token(bot, pdf), action="decline"))
    assert ctx.texts == [DECLINED] and uploader.calls == []


def test_an_offer_can_be_used_once(pdf):
    bot, uploader = files_bot(pdf)
    token = offer_and_token(bot, pdf)
    answer_card(bot, consent_answer(token))
    again = answer_card(bot, consent_answer(token))
    assert len(uploader.calls) == 1 and again.sent == []


def test_someone_elses_click_is_ignored_and_the_offer_stays(pdf):
    bot, uploader = files_bot(pdf)
    token = offer_and_token(bot, pdf)
    for activity in (consent_answer(token, user="someone-else"), consent_answer(token, conversation="other-chat")):
        assert answer_card(bot, activity).sent == []
    assert uploader.calls == [] and token in bot.delivery.offers
    answer_card(bot, consent_answer(token))
    assert len(uploader.calls) == 1


def test_another_company_is_ignored(pdf):
    bot, uploader = files_bot(pdf)
    token = offer_and_token(bot, pdf)
    assert answer_card(bot, consent_answer(token, tenant="99999999-0000-0000-0000-000000000000")).sent == []
    assert uploader.calls == [] and token in bot.delivery.offers


@pytest.mark.parametrize("context", ["text", {"token": 5}, {"token": "never-offered"}, {}, {"path": "C:/secret.txt"}])
def test_a_card_answer_we_never_offered_does_nothing(pdf, context):
    bot, uploader = files_bot(pdf)
    offer_and_token(bot, pdf)
    assert answer_card(bot, consent_answer("x", context=context)).sent == [] and uploader.calls == []


def test_an_old_offer_expires(pdf):
    bot, uploader = files_bot(pdf)
    token = offer_and_token(bot, pdf)
    bot.delivery.offers[token].expires = 0
    assert answer_card(bot, consent_answer(token)).sent == [] and uploader.calls == [] and token not in bot.delivery.offers


def test_only_so_many_offers_are_kept(pdf):
    delivery = FileDelivery(FakeUploader())
    for n in range(MAX_OFFERS + 5):
        delivery.offer(pdf, f"user-{n}", "conv")
    assert len(delivery.offers) == MAX_OFFERS


@pytest.mark.parametrize("url", [
    "https://evil.example/upload", "http://contoso.sharepoint.com/x", "https://sharepoint.com.evil.example/x",
    "https://localhost/x", "file:///C:/secret", "https://127.0.0.1/x", "", None,
])
def test_the_file_goes_only_to_the_persons_sharepoint_storage(pdf, url):
    bot, uploader = files_bot(pdf)
    ctx = answer_card(bot, consent_answer(offer_and_token(bot, pdf), upload_url=url))
    assert uploader.calls == [] and ctx.texts == [UPLOAD_FAILED]
    assert not allowed_upload_url(url)


def test_real_sharepoint_addresses_are_accepted():
    assert allowed_upload_url(UPLOAD_URL) and allowed_upload_url("https://contoso.sharepoint.us/x")


def test_a_failed_upload_is_an_apology(pdf):
    bot, _ = files_bot(pdf, FakeUploader(httpx.ConnectError("boom")))
    assert answer_card(bot, consent_answer(offer_and_token(bot, pdf))).texts == [UPLOAD_FAILED]


def test_the_upload_sends_the_bytes_with_a_range_and_none_of_our_credentials(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    real = httpx.AsyncClient
    monkeypatch.setattr(teams_delivery.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    asyncio.run(teams_delivery.upload_to_teams(UPLOAD_URL, b"0123456789"))
    request = seen[0]
    assert request.method == "PUT" and str(request.url) == UPLOAD_URL and request.content == b"0123456789"
    assert request.headers["content-range"] == "bytes 0-9/10" and "authorization" not in request.headers


def test_the_app_listens_for_card_answers():
    app = build_app(settings(), "app-id", "secret", TENANT, make_bot(FakeAssistant()))
    assert app.router.select_handlers(consent_answer("t"))
