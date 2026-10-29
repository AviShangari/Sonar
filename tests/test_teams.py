"""The Teams bot: who it answers, what it answers with, and that the SDK's security checks stay on.
The assistant is a stand-in and Teams is a recording fake; no network, no cost."""

import asyncio
import io
import json
import struct
import zipfile
import zlib

import pytest
from microsoft_teams.api import MessageActivity, TypingActivityInput

from sonar.agent_runner import AgentError
from sonar.assistant.agent import SYSTEM_PROMPT, TEAMS_FORMAT, TERMINAL_FORMAT, Answer
from sonar.config import TeamsSettings, load_teams_settings
from sonar.teams import bot as teams_bot
from sonar.teams import package
from sonar.teams.bot import BUSY, FAILED, HELP, MAX_ANSWER_CHARS, NOT_ALLOWED, Conversations, SonarBot, build_app

TENANT = "11111111-2222-3333-4444-555555555555"
BOT_ID = "28:sonar-bot"


def settings(**overrides) -> TeamsSettings:
    values = dict(
        short_description="Short.", full_description="Full.", developer_name="ITnet", website_url="https://example.com",
        privacy_url="https://example.com/p", terms_url="https://example.com/t",
    )
    return TeamsSettings(**(values | overrides))


def message(text: str, kind: str = "personal", tenant: str = TENANT, user: str = "user-1", mention: bool = False,
            conversation: str = "conv-1") -> MessageActivity:
    entities = [{"type": "mention", "text": "<at>Sonar</at>", "mentioned": {"id": BOT_ID, "name": "Sonar"}}] if mention else []
    return MessageActivity.model_validate({
        "type": "message", "id": "m1", "channelId": "msteams", "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "text": text, "from": {"id": "29:u", "aadObjectId": user}, "recipient": {"id": BOT_ID, "name": "Sonar"},
        "conversation": {"id": conversation, "tenantId": tenant, "conversationType": kind}, "entities": entities,
    })


class FakeContext:
    """Stands in for the SDK's ActivityContext: records what the bot sends."""

    def __init__(self, activity: MessageActivity) -> None:
        self.activity = activity
        self.sent: list = []

    async def send(self, message):
        self.sent.append(message)

    @property
    def texts(self) -> list[str]:
        return [m for m in self.sent if isinstance(m, str)]


class FakeAssistant:
    def __init__(self, answer: str | Exception = "Two matches.") -> None:
        self.answer = answer
        self.files: list = []  # PDFs the assistant "made" for the next answer
        self.questions: list[str] = []
        self.release = asyncio.Event()
        self.release.set()

    async def ask_full(self, question: str) -> Answer:
        self.questions.append(question)
        await self.release.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return Answer(self.answer, list(self.files))

    def reset(self) -> None:
        pass


def deliver(bot: SonarBot, activity: MessageActivity) -> FakeContext:
    """One incoming message, and the background answer it starts, run to the end."""
    async def go() -> FakeContext:
        ctx = FakeContext(activity)
        await bot.on_message(ctx)
        await asyncio.gather(*bot.tasks)
        return ctx
    return asyncio.run(go())


def make_bot(assistant: FakeAssistant, **overrides) -> SonarBot:
    return SonarBot(settings(**overrides), TENANT.upper(), lambda: assistant)


# ---- who it answers -----------------------------------------------------------------------------------

def test_answers_a_one_to_one_question():
    assistant = FakeAssistant()
    ctx = deliver(make_bot(assistant), message("any new matches?"))
    assert assistant.questions == ["any new matches?"]
    assert isinstance(ctx.sent[0], TypingActivityInput)
    assert ctx.texts == ["Two matches."]


def test_another_company_is_ignored_silently():
    assistant = FakeAssistant()
    ctx = deliver(make_bot(assistant), message("any new matches?", tenant="99999999-0000-0000-0000-000000000000"))
    assert assistant.questions == [] and ctx.sent == []


def test_a_missing_tenant_is_ignored():
    assistant = FakeAssistant()
    ctx = deliver(make_bot(assistant), message("any new matches?", tenant=""))
    assert assistant.questions == [] and ctx.sent == []


def test_someone_not_on_the_list_is_refused_without_a_model_call():
    assistant = FakeAssistant()
    ctx = deliver(make_bot(assistant, allowed_users=["ALLOWED-1"]), message("any new matches?", user="someone-else"))
    assert assistant.questions == []
    assert ctx.texts == [NOT_ALLOWED]


def test_someone_on_the_list_is_answered():
    assistant = FakeAssistant()
    deliver(make_bot(assistant, allowed_users=["ALLOWED-1"]), message("any new matches?", user="allowed-1"))
    assert assistant.questions == ["any new matches?"]


@pytest.mark.parametrize("kind", ["channel", "groupChat"])
def test_channels_and_group_chats_need_an_at_mention(kind):
    assistant = FakeAssistant()
    ctx = deliver(make_bot(assistant), message("any new matches?", kind=kind))
    assert assistant.questions == [] and ctx.sent == []
    deliver(make_bot(assistant), message("<at>Sonar</at> any new matches?", kind=kind, mention=True))
    assert assistant.questions == ["any new matches?"]


def test_only_sonars_own_mention_is_removed():
    activity = message("<at>Sonar</at> what did <at>Dana</at> ask?", kind="channel", mention=True)
    activity.entities.append(activity.entities[0].model_copy(update={"text": "<at>Dana</at>"}))
    activity.entities[1].mentioned = activity.entities[1].mentioned.model_copy(update={"id": "29:dana", "name": "Dana"})
    assert teams_bot.question_from(activity) == "what did <at>Dana</at> ask?"


# ---- commands, one question at a time ------------------------------------------------------------------

@pytest.mark.parametrize("text", ["help", "HELP", ""])
def test_help(text):
    assistant = FakeAssistant()
    ctx = deliver(make_bot(assistant), message(text))
    assert ctx.texts == [HELP] and assistant.questions == []


def test_reset_starts_a_new_history():
    made: list[FakeAssistant] = []

    def make() -> FakeAssistant:
        made.append(FakeAssistant())
        return made[-1]

    bot = SonarBot(settings(), TENANT, make)
    deliver(bot, message("first"))
    ctx = deliver(bot, message("reset"))
    deliver(bot, message("second"))
    assert ctx.texts == ["Started a new conversation."]
    assert [a.questions for a in made] == [["first"], ["second"]]


def test_a_second_question_waits_for_the_first():
    assistant = FakeAssistant()
    bot = make_bot(assistant)

    async def go() -> tuple[FakeContext, FakeContext]:
        assistant.release.clear()  # the first answer is still being worked on
        first, second = FakeContext(message("first")), FakeContext(message("second"))
        await bot.on_message(first)
        await asyncio.sleep(0)
        await bot.on_message(second)
        assistant.release.set()
        await asyncio.gather(*bot.tasks)
        return first, second

    first, second = asyncio.run(go())
    assert assistant.questions == ["first"]
    assert second.texts == [BUSY] and first.texts == ["Two matches."]
    assert bot.busy == set()


def test_other_conversations_are_not_blocked():
    bot = make_bot(FakeAssistant())
    deliver(bot, message("first", conversation="a"))
    assert deliver(bot, message("second", conversation="b")).texts == ["Two matches."]


def test_conversation_histories_are_capped():
    conversations = Conversations(FakeAssistant, limit=2)
    first = conversations.get("a")
    conversations.get("b")
    conversations.get("c")
    assert list(conversations.assistants) == ["b", "c"]
    assert conversations.get("a") is not first


# ---- what it answers with ------------------------------------------------------------------------------

def test_a_failure_is_an_apology_without_details():
    assistant = FakeAssistant(AgentError("OpenRouter said 402: key sk-or-secret has no credit"))
    bot = make_bot(assistant)
    ctx = deliver(bot, message("any new matches?"))
    assert ctx.texts == [FAILED]
    assert bot.busy == set()


def test_an_unexpected_error_is_an_apology_too():
    ctx = deliver(make_bot(FakeAssistant(KeyError("boom"))), message("any new matches?"))
    assert ctx.texts == [FAILED]


def test_control_characters_are_removed_and_long_answers_cut():
    ctx = deliver(make_bot(FakeAssistant("Title \x1b[31mred\x07\n- item")), message("q"))
    assert ctx.texts == ["Title  [31mred \n- item"]
    long = deliver(make_bot(FakeAssistant("x" * (MAX_ANSWER_CHARS + 50))), message("q")).texts[0]
    assert len(long) < MAX_ANSWER_CHARS + 100 and long.endswith("too long for Teams)")


def test_the_teams_prompt_differs_only_in_layout():
    assert "{format_rule}" in SYSTEM_PROMPT
    assert "Teams" in TEAMS_FORMAT and "terminal" in TERMINAL_FORMAT


# ---- the SDK's own checks ------------------------------------------------------------------------------

def test_requests_are_always_authenticated(monkeypatch):
    monkeypatch.setenv("DANGEROUSLY_ALLOW_UNAUTHENTICATED_REQUESTS", "true")
    app = build_app(settings(), "app-id", "secret", TENANT, make_bot(FakeAssistant()))
    assert app.options.dangerously_allow_unauthenticated_requests is False
    assert app.credentials.client_id == "app-id" and app.credentials.tenant_id == TENANT


def test_unsigned_requests_are_refused():
    app = build_app(settings(), "app-id", "secret", TENANT, make_bot(FakeAssistant()))
    asyncio.run(app.initialize())
    body = message("hi").model_dump(by_alias=True, exclude_none=True)
    for headers in ({}, {"authorization": "Bearer not-a-real-token"}):
        response = asyncio.run(app.server.handle_request({"body": body, "headers": headers}))
        assert response["status"] == 401


def test_listens_where_settings_say():
    app = build_app(settings(port=4000), "app-id", "secret", TENANT, make_bot(FakeAssistant()))
    config = app.server.adapter._server.config
    assert (config.host, config.port) == ("127.0.0.1", 4000)


def test_repo_settings_load_and_listen_locally():
    teams = load_teams_settings()
    assert teams.host == "127.0.0.1"


# ---- app package --------------------------------------------------------------------------------------

def png_size_and_pixels(data: bytes) -> tuple[int, list[tuple[int, ...]]]:
    width, height = struct.unpack(">II", data[16:24])
    assert width == height
    raw = zlib.decompress(data[data.index(b"IDAT") + 4: data.index(b"IEND") - 8])
    stride = 1 + width * 4
    pixels = [tuple(raw[y * stride + 1 + x * 4: y * stride + 5 + x * 4]) for y in range(height) for x in range(width)]
    return width, pixels


def test_package_holds_manifest_and_icons(tmp_path):
    path = package.build(settings(package_dir=str(tmp_path)), "app-guid")
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == ["color.png", "manifest.json", "outline.png"]
        manifest = json.loads(zf.read("manifest.json"))
        color, outline = zf.read("color.png"), zf.read("outline.png")
    assert manifest["id"] == manifest["bots"][0]["botId"] == "app-guid"
    assert manifest["bots"][0]["supportsFiles"] is True  # needed for the one-to-one file offer
    assert manifest["manifestVersion"] == "1.25" and manifest["supportsChannelFeatures"] == "tier1"
    assert png_size_and_pixels(color)[0] == 192
    size, pixels = png_size_and_pixels(outline)
    assert size == 32 and set(pixels) <= {(255, 255, 255, 255), (0, 0, 0, 0)}  # Teams: white and transparent only


def test_package_rejects_text_teams_would_refuse():
    with pytest.raises(ValueError):
        package.manifest(settings(short_description="x" * 81), "app-guid")


def test_package_from_repo_settings_is_valid_json(tmp_path):
    teams = load_teams_settings().model_copy(update={"package_dir": str(tmp_path)})
    with zipfile.ZipFile(io.BytesIO(package.build(teams, "app-guid").read_bytes())) as zf:
        assert json.loads(zf.read("manifest.json"))["name"]["short"] == "Sonar"
