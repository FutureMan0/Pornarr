from __future__ import annotations

from typing import Any, cast

from pornarr_api.routers.events import _event_frame, _stream
from pornarr_shared.events import EVENT_STREAM, GLOBAL_CHANNEL, publish_event, user_channel


class Redis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append(("xadd", (stream, fields)))
        return "1-0"

    async def publish(self, channel: str, event_id: str) -> None:
        self.calls.append(("publish", (channel, event_id)))


async def test_publisher_persists_before_notifying_the_target_user() -> None:
    redis = Redis()

    event_id = await publish_event(redis, "download.progress", {"percent": 10}, user_id="user-1")

    assert event_id == "1-0"
    assert redis.calls[0][0] == "xadd"
    assert redis.calls[0][1][0] == EVENT_STREAM
    assert redis.calls[1] == ("publish", (user_channel("user-1"), "1-0"))


async def test_global_events_use_the_global_channel() -> None:
    redis = Redis()

    await publish_event(redis, "system.notice", {"message": "ready"})

    assert redis.calls[1] == ("publish", (GLOBAL_CHANNEL, "1-0"))


def test_event_frame_hides_another_users_event() -> None:
    fields = {"type": "download.progress", "data": "{}", "user_id": "user-2", "viewer_id": "user-1"}

    assert _event_frame("1-0", fields) is None


class PubSub:
    async def subscribe(self, *_: str) -> None:
        return None

    async def unsubscribe(self, *_: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class ResumeRedis:
    async def xread(self, _: dict[str, str]) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        return [
            (
                EVENT_STREAM,
                [
                    ("1-0", {"type": "ready", "data": "{}", "user_id": "user-1"}),
                    ("2-0", {"type": "private", "data": "{}", "user_id": "user-2"}),
                ],
            )
        ]

    def pubsub(self) -> PubSub:
        return PubSub()


class FakeRequest:
    class App:
        state = type("State", (), {"redis": ResumeRedis()})()

    app = App()

    async def is_disconnected(self) -> bool:
        return False


async def test_resume_replays_only_events_visible_to_the_user() -> None:
    stream = _stream(cast(Any, FakeRequest()), "user-1", "0-0")

    assert await anext(stream) == "id: 1-0\nevent: ready\ndata: {}\n\n"
    await stream.aclose()


class LivePubSub(PubSub):
    def __init__(self) -> None:
        self.messages = [{"data": "3-0"}]

    async def get_message(self, **_: object) -> dict[str, str] | None:
        return self.messages.pop() if self.messages else None


class LiveRedis(ResumeRedis):
    def pubsub(self) -> LivePubSub:
        return LivePubSub()

    async def xrange(self, *_: object, **__: object) -> list[tuple[str, dict[str, str]]]:
        return [("3-0", {"type": "live", "data": "{}", "user_id": "user-1"})]


class LiveRequest(FakeRequest):
    class App:
        state = type("State", (), {"redis": LiveRedis()})()

    app = App()

    async def is_disconnected(self) -> bool:
        return False


async def test_live_publication_is_loaded_from_the_stream_before_delivery() -> None:
    stream = _stream(cast(Any, LiveRequest()), "user-1", None)

    assert await anext(stream) == "id: 3-0\nevent: live\ndata: {}\n\n"
    await stream.aclose()
