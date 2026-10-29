"""Sonar in Microsoft Teams. People chat with Sonar one to one, or @mention it in a channel or group chat,
and the terminal assistant (sonar.assistant) answers, with the same six read-only tools.

The Teams SDK (microsoft-teams-apps) receives Teams' messages on /api/messages and checks that each one
really comes from Microsoft for our bot (the App ID and secret from .env). On top of that, this module:
  - ignores messages from any other company (tenant), silently;
  - answers only people on `allowed_users` in config/teams.yaml, when that list is not empty;
  - in channels and group chats, answers only when Sonar is @mentioned;
  - keeps one conversation history per chat or channel thread, and one question at a time in each;
  - answers in the background: Teams waits only about 15 seconds for a reply, the assistant often takes longer;
  - hands over a PDF the assistant made: offered as a file in a one-to-one chat (delivery.py), explained in channels.
The question text is never logged."""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import uvicorn
from microsoft_teams.api import Action, FileConsentInvokeActivity, MessageActivity, TypingActivityInput
from microsoft_teams.api.activities.utils import StripMentionsTextOptions, strip_mentions_text
from microsoft_teams.apps import ActivityContext, App
from microsoft_teams.apps.http import FastAPIAdapter

from sonar.agent_runner import AgentError
from sonar.assistant.agent import Answer
from sonar.assistant.safe import for_terminal
from sonar.config import TeamsSettings
from sonar.teams.delivery import DECLINED, NOT_IN_CHANNELS, UPLOAD_FAILED, FileDelivery

log = logging.getLogger("sonar.teams")

MAX_ANSWER_CHARS = 20_000  # Teams refuses a message above about 28 KB
HELP = (
    "Ask me about Sonar's tender matches: what's new, closing dates, why a posting matched, or the last scan. "
    "I can only read Sonar's stored reports.\n\n"
    "Type **reset** to start a new conversation."
)
RESET_WORDS = {"reset", "/reset", "start over", "new conversation"}
HELP_WORDS = {"", "help", "/help", "?"}
NOT_ALLOWED = "Sorry, you're not on Sonar's list of users. Ask the person who runs Sonar to add you."
BUSY = "I'm still working on your last question. Please wait for that answer first."
FAILED = "Sorry, I couldn't answer that right now. Please try again in a moment."


class Asker(Protocol):
    """What the bot needs from the assistant (sonar.assistant.agent.Assistant)."""

    async def ask_full(self, question: str) -> Answer: ...

    def reset(self) -> None: ...


def tenant_of(activity: Any) -> str:
    """The company (Microsoft 365 tenant) the message or card answer was sent from."""
    tenant = activity.conversation.tenant_id
    if not tenant and activity.channel_data and activity.channel_data.tenant:
        tenant = activity.channel_data.tenant.id
    return (tenant or "").lower()


def question_from(activity: MessageActivity) -> str:
    """The message text without the "@Sonar" mention (other people's mentions stay)."""
    return strip_mentions_text(activity, StripMentionsTextOptions(account_id=activity.recipient.id)) or ""


def needs_mention(activity: MessageActivity) -> bool:
    """Channels and group chats: Sonar answers only when @mentioned. A one-to-one chat needs no mention."""
    return activity.conversation.conversation_type not in (None, "personal")


def for_teams(answer: str) -> str:
    """The model's answer as posted: control characters removed (it may quote posting text), length capped."""
    text = for_terminal(answer).strip() or "I don't have an answer for that."
    return text if len(text) <= MAX_ANSWER_CHARS else text[:MAX_ANSWER_CHARS] + "\n\n…(answer cut: too long for Teams)"


class Conversations:
    """One assistant (its own short history) per chat or channel thread, newest kept, oldest dropped."""

    def __init__(self, make: Callable[[], Asker], limit: int) -> None:
        self.make = make
        self.limit = limit
        self.assistants: OrderedDict[str, Asker] = OrderedDict()

    def get(self, key: str) -> Asker:
        if key not in self.assistants:
            self.assistants[key] = self.make()
        self.assistants.move_to_end(key)
        while len(self.assistants) > self.limit:
            self.assistants.popitem(last=False)
        return self.assistants[key]

    def reset(self, key: str) -> None:
        self.assistants.pop(key, None)


class SonarBot:
    def __init__(
        self, settings: TeamsSettings, tenant_id: str, make_assistant: Callable[[], Asker],
        delivery: FileDelivery | None = None,
    ) -> None:
        self.tenant_id = tenant_id.lower()
        self.delivery = delivery or FileDelivery()
        self.allowed_users = {u.lower() for u in settings.allowed_users}
        self.conversations = Conversations(make_assistant, settings.max_conversations)
        self.busy: set[str] = set()  # conversations with a question being answered
        self.tasks: set[asyncio.Task[None]] = set()  # running answers (kept so they are not garbage-collected)

    async def on_message(self, ctx: ActivityContext[MessageActivity]) -> None:
        activity = ctx.activity
        user = activity.from_.aad_object_id or ""
        kind = activity.conversation.conversation_type or "personal"
        if tenant_of(activity) != self.tenant_id:
            log.warning("step=teams_message outcome=other_company kind=%s", kind)
            return
        if self.allowed_users and user.lower() not in self.allowed_users:
            log.warning("step=teams_message outcome=not_allowed user=%s kind=%s", user or "unknown", kind)
            await ctx.send(NOT_ALLOWED)
            return
        if needs_mention(activity) and not activity.is_recipient_mentioned():
            return
        question, key = question_from(activity), activity.conversation.id
        if question.lower() in HELP_WORDS:
            await ctx.send(HELP)
            return
        if question.lower() in RESET_WORDS:
            self.conversations.reset(key)
            await ctx.send("Started a new conversation.")
            return
        if key in self.busy:
            await ctx.send(BUSY)
            return
        self.busy.add(key)
        task = asyncio.create_task(self.answer(ctx, key, question, user, kind))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def answer(self, ctx: ActivityContext[Any], key: str, question: str, user: str, kind: str) -> None:
        began, outcome = time.monotonic(), "answered"
        files: list[Path] = []
        try:
            try:
                await ctx.send(TypingActivityInput())  # "Sonar is typing…"; not worth failing over
            except Exception:
                log.debug("typing indicator failed", exc_info=True)
            try:
                result = await self.conversations.get(key).ask_full(question)
                reply, files = for_teams(result.text), result.files
            except AgentError as exc:
                outcome, reply = "failed", FAILED
                log.warning("step=teams_answer outcome=agent_error detail=%s", exc)
            except Exception:
                outcome, reply = "failed", FAILED
                log.exception("step=teams_answer outcome=error")
            try:
                await ctx.send(reply)
                if files and kind == "personal":
                    for path in files:
                        await ctx.send(self.delivery.offer(path, user, key))
                elif files:
                    await ctx.send(NOT_IN_CHANNELS)
            except Exception:
                outcome = "not_sent"
                log.exception("step=teams_answer outcome=send_failed")
        finally:
            self.busy.discard(key)
            log.info(
                "step=teams_answer outcome=%s user=%s kind=%s files=%d seconds=%.1f", outcome, user or "unknown", kind,
                len(files), time.monotonic() - began,
            )

    async def on_file_consent(self, ctx: ActivityContext[FileConsentInvokeActivity]) -> None:
        """The person's click on a "send me the file" card: Allow uploads the PDF, Decline says no problem."""
        activity = ctx.activity
        user, key = activity.from_.aad_object_id or "", activity.conversation.id
        if tenant_of(activity) != self.tenant_id:
            log.warning("step=teams_file outcome=other_company")
            return
        answer = activity.value
        offer = self.delivery.take(answer.context, user, key)
        if offer is None:
            log.warning("step=teams_file outcome=unknown_offer user=%s", user or "unknown")
            return
        if answer.action != Action.ACCEPT:
            await ctx.send(DECLINED)
            return
        message = await self.delivery.send(offer, answer.upload_info)
        await ctx.send(message or UPLOAD_FAILED)
        log.info("step=teams_file outcome=%s user=%s", "sent" if message else "failed", user or "unknown")


def build_app(settings: TeamsSettings, app_id: str, app_password: str, tenant_id: str, bot: SonarBot) -> App:
    """The Teams app: checks every incoming request (never the SDK's 'unauthenticated' development mode, whatever
    the environment says) and listens only on settings.host."""
    adapter = FastAPIAdapter(
        server_factory=lambda api: uvicorn.Server(
            uvicorn.Config(api, host=settings.host, port=settings.port, log_level="warning")
        )
    )
    app = App(
        client_id=app_id,
        client_secret=app_password,
        tenant_id=tenant_id,
        dangerously_allow_unauthenticated_requests=False,
        telemetry={"agent365": False},
        http_server_adapter=adapter,
    )
    app.on_message(bot.on_message)
    app.on_file_consent(bot.on_file_consent)
    return app
