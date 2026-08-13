"""Deterministic Milestone 3A tests; no cloud or camera is contacted."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package


def load_module(name: str):
    full_name = f"{PACKAGE}.{name}"
    spec = importlib.util.spec_from_file_location(full_name, COMPONENT / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


const = load_module("const")
cloud = load_module("cloud")
events = load_module("events")

MESSAGE_CENTER_STM_V1_FIXTURE = {
    "time": "1786640000000",
    "data": (
        "XTN221HA1PR/wxM21plE6h2zyyH9LgPi72cbzJksBeopKb5zUao5pLbb0V14QowT"
        "opR5VBYaJpwuVx3XfsKv4hnDKzzXGoLkfdcFxM0npJkaut0MdbcU7bIBUsHmohwp6f"
        "m2hQqm6f5iXcvvnME3KI2G7tzd4oOY4hcDM8UVEff4uutzPoyoIVnbgVl+gBIO0Cdt"
        "Yr1ZOU3YSz2JD+Xzj/MgZZ/F2cvDZVxInVIOjwK0dNPWLtValSfQkpc9ZC5OUaj/z"
        "ZCAeYTkjKc1"
    ),
}


class DecoderTests(unittest.TestCase):
    def test_live_equivalent_stm_v1_fixture(self):
        decoded = cloud.decode_message_center_payload(
            MESSAGE_CENTER_STM_V1_FIXTURE, "user-1"
        )
        self.assertTrue(decoded.wrapped)
        self.assertEqual(decoded.payload["items"][0]["id"], "event-wrapper")
        self.assertEqual(decoded.payload["nextToken"], "page-2")

    def test_plaintext_and_empty_responses(self):
        plaintext = cloud.decode_message_center_payload({"items": []}, "user-1")
        self.assertFalse(plaintext.wrapped)
        self.assertEqual(plaintext.payload, {"items": []})
        enveloped = cloud.decode_message_center_payload(
            {"data": {"items": []}}, "user-1"
        )
        self.assertEqual(enveloped.payload, {"items": []})

    def test_malformed_stm_is_rejected(self):
        with self.assertRaises(cloud.CloudEventDecodeError) as caught:
            cloud.decode_message_center_payload(
                {"stm": 1, "time": "1786640000000", "data": "not-base64"},
                "user-1",
            )
        self.assertEqual(caught.exception.reason, "invalid_stm_v1")
        self.assertTrue(caught.exception.wrapped)

    def test_user_profile_id_extraction(self):
        self.assertEqual(cloud.user_id_from_payload({"id": "user-1"}), "user-1")
        self.assertEqual(
            cloud.user_id_from_payload({"data": {"user": {"id": "user-2"}}}),
            "user-2",
        )


class EventTests(unittest.TestCase):
    def test_scalar_list_missing_optional_and_timezone_normalization(self):
        parsed = events.parse_cloud_events(
            {
                "items": [
                    {
                        "id": "scalar",
                        "uid": "camera-1_00",
                        "alarmAt": "2026-08-13T12:00:00",
                        "raw": {"alarm": {"type": "AI", "aiType": "person"}},
                    },
                    {
                        "id": "list",
                        "uid": "camera-1",
                        "alarmAt": 1786622400,
                        "raw": {
                            "alarm": {
                                "type": "AI",
                                "aiType": ["person", "vehicle"],
                            }
                        },
                    },
                    {
                        "id": "missing-optional",
                        "uid": "camera-1",
                        "alarmAt": "2026-08-13T12:00:00Z",
                        "alarmType": "MD",
                    },
                ]
            },
            "camera-1",
        )
        self.assertEqual([event.event_id for event in parsed], ["scalar", "list", "missing-optional"])
        self.assertEqual(parsed[0].uid, "camera-1")
        self.assertEqual(parsed[0].ai_types, ("person",))
        self.assertEqual(parsed[1].ai_types, ("person", "vehicle"))
        self.assertTrue(all(event.alarm_time.tzinfo is not None for event in parsed))
        self.assertEqual(parsed[0].alarm_time.tzinfo, UTC)

    def test_duplicate_event_is_inserted_once(self):
        event = events.CloudEvent(
            "event-1",
            "camera-1",
            datetime(2026, 8, 13, 12, tzinfo=UTC),
            "MD",
        )
        queue = events.EventQueue()
        self.assertTrue(queue.enqueue(event))
        self.assertFalse(queue.enqueue(event))
        self.assertEqual(queue.pending, (event,))


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self.status = 200
        self.headers = {"Content-Type": "application/json"}
        raw = json.dumps(payload).encode()
        self.content = types.SimpleNamespace(read=lambda _: _async_value(raw))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


async def _async_value(value):
    return value


class _FakeSession:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.request_data = None

    def request(self, method, url, **kwargs):
        self.request_data = (method, url, kwargs)
        return _FakeResponse(self.payload)


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_user_profile_request(self):
        session = _FakeSession({"id": "user-1"})
        user_id = await cloud.ReolinkCloudClient(session).async_query_user_id(
            "access-token"
        )
        method, url, kwargs = session.request_data
        self.assertEqual(user_id, "user-1")
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/v1.0/users/@me/profile/"))
        self.assertIsNone(kwargs["json"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer access-token")

    async def test_bounded_official_request_shape_and_page_telemetry(self):
        session = _FakeSession(MESSAGE_CENTER_STM_V1_FIXTURE)
        page = await cloud.ReolinkCloudClient(session).async_query_events(
            "access-token",
            "user-1",
            "camera-1",
            datetime(2026, 8, 13, 12, tzinfo=UTC),
            datetime(2026, 8, 13, 12, 2, tzinfo=UTC),
        )
        method, url, kwargs = session.request_data
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/v2/message-center/devices/msgs/query"))
        self.assertEqual(
            kwargs["json"],
            {
                "timeRanges": [
                    {"startAt": 1786622400000, "endAt": 1786622520000}
                ],
                "uids": ["camera-1_00"],
                "alarmTypes": [],
            },
        )
        self.assertEqual(page.http_status, 200)
        self.assertTrue(page.wrapped)
        self.assertEqual(page.item_count, 1)
        self.assertTrue(page.next_token_present)


if __name__ == "__main__":
    unittest.main()
