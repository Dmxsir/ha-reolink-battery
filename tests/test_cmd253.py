"""Offline cmd253 channel-0 compatibility test; no camera is contacted."""

from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import sys
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from reolink_aio.baichuan.baichuan import Baichuan
from reolink_aio.enums import ConnectionEnum
from reolink_aio.exceptions import ReolinkError

COMPONENT = Path(__file__).parents[1] / "custom_components" / "reolink_battery"
PACKAGE = "_reolink_battery_cmd253_test"
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


camera = load_module("camera")
device_status = sys.modules[f"{PACKAGE}.device_status"]

CMD253_FIXTURE = """<body><BatteryInfo>
<channelId>0</channelId>
<adapterStatus>solarPanels</adapterStatus>
<batteryPercent>82</batteryPercent>
<batteryVersion>1</batteryVersion>
<chargeStatus>charging</chargeStatus>
<current>120</current>
<lowPowerFlag>0</lowPowerFlag>
<temperature>28</temperature>
<voltage>3900</voltage>
</BatteryInfo></body>"""


class Cmd253Tests(unittest.TestCase):
    def test_channel_zero_is_rejected_then_accepted_by_narrow_inventory_shim(self):
        async def exercise():
            api = types.SimpleNamespace(
                _channels=[],
                _stream_channels=[],
                _num_channels=0,
                _battery={},
            )
            parser = Baichuan(
                "camera.invalid",
                "admin",
                "unused",
                api,
                connection_type=ConnectionEnum.udp,
            )
            parser._parse_xml(253, CMD253_FIXTURE)
            self.assertEqual(api._battery, {})

            camera.prepare_standalone_channel_zero(api)
            parser._parse_xml(253, CMD253_FIXTURE)
            return api

        api = asyncio.run(exercise())
        self.assertEqual(api._channels, [0])
        self.assertEqual(api._stream_channels, [0])
        self.assertEqual(api._num_channels, 1)
        self.assertEqual(api._battery[0]["batteryPercent"], 82)
        self.assertEqual(api._battery[0]["chargeStatus"], 1)
        self.assertEqual(api._battery[0]["adapterStatus"], "solarPanels")

    def test_failed_optional_reads_do_not_create_a_refresh_timestamp(self):
        async def unavailable(*_args):
            raise ReolinkError("offline")

        baichuan = types.SimpleNamespace(
            get_info=unavailable,
            get_battery_info=unavailable,
            GetHddInfo=unavailable,
            get_wifi_signal=unavailable,
        )
        host = types.SimpleNamespace(
            _channels=[],
            _stream_channels=[],
            _num_channels=0,
            _battery={},
            baichuan=baichuan,
            hdd_info=None,
            wifi_signal=lambda: None,
        )
        self.assertIsNone(asyncio.run(camera.async_collect_local_state(host)))

    def test_partial_query_failure_merges_new_and_last_known_fields(self):
        class Baichuan:
            async def get_battery_info(self, _channel):
                host._battery[0] = {
                    "batteryPercent": 77,
                    "chargeStatus": 0,
                }

            async def GetHddInfo(self):
                raise ReolinkError("storage unavailable")

            async def get_wifi_signal(self):
                host._wifi = -48

            async def get_info(self):
                raise ReolinkError("device info unavailable")

        host = types.SimpleNamespace(
            _channels=[],
            _stream_channels=[],
            _num_channels=0,
            _battery={},
            _wifi=None,
            baichuan=Baichuan(),
            hdd_info=None,
            wifi_signal=lambda: host._wifi,
        )
        previous = device_status.LocalState(
            datetime(2026, 8, 13, 8, 0, tzinfo=UTC),
            device_status.DeviceInfo("Argus 2E", "firmware", "hardware"),
            device_status.BatteryState(90, True, "charging", "unknown", "unknown", None, 30),
            device_status.StorageState(True, "normal", 100, 60, 40),
            -30,
        )
        current = asyncio.run(camera.async_collect_local_state(host))
        cache = device_status.DeviceStatusCache(
            device_status.DeviceState(device_status.CloudState(), previous)
        )
        cache.update_local(current)
        merged = cache.state.local
        self.assertEqual(merged.battery.percent, 77)
        self.assertEqual(merged.wifi_rssi_dbm, -48)
        self.assertEqual(merged.storage, previous.storage)
        self.assertEqual(merged.device_info, previous.device_info)
        self.assertGreater(merged.refreshed_at, previous.refreshed_at)

    def test_short_status_session_always_closes_and_has_no_media_operation(self):
        calls = []

        class Lease:
            host = "192.0.2.174"
            source_ip = "192.0.2.61"

            def close(self):
                calls.append("lease_close")

        class Baichuan:
            _first_login = True
            _push_callback = None
            _close_callback = None

            async def login(self):
                calls.append("login")

            async def get_battery_info(self, _channel):
                calls.append("battery")
                host._battery[0] = {"batteryPercent": 82}

            async def GetHddInfo(self):
                calls.append("storage")
                host.hdd_info = []

            async def get_wifi_signal(self):
                calls.append("wifi")
                host._wifi = -44

            async def get_info(self):
                calls.append("device_info")
                return {"type": "Argus 2E"}

        class Host:
            def __init__(self, **_kwargs):
                self.baichuan = Baichuan()
                self._channels = []
                self._stream_channels = []
                self._num_channels = 0
                self._battery = {}
                self.hdd_info = None
                self._wifi = None

            def wifi_signal(self):
                return self._wifi

            async def logout(self):
                calls.append("logout")

        class Connection:
            connection_open = True

            def __init__(self, *_args, **_kwargs):
                pass

            async def connect(self):
                calls.append("connect")

            async def close(self):
                calls.append("connection_close")

        host = None

        def make_host(**kwargs):
            nonlocal host
            host = Host(**kwargs)
            return host

        with (
            patch.object(camera, "Host", make_host),
            patch.object(camera, "BoundBaichuanUdpConnection", Connection),
            patch.object(camera, "linux_ipv4_interface", return_value=("eth0", 2)),
            patch.object(camera, "resolve_uid_lan", return_value=Lease()),
            patch.object(camera, "validate_local_lan_route"),
        ):
            result = asyncio.run(
                camera.async_validate_legacy_device(
                    "camera1",
                    "admin",
                    "secret",
                    ipaddress.ip_interface("192.0.2.61/24"),
                )
            )

        self.assertIsNotNone(result.local_state)
        self.assertEqual(
            calls,
            [
                "connect",
                "lease_close",
                "login",
                "battery",
                "storage",
                "wifi",
                "device_info",
                "logout",
                "connection_close",
            ],
        )
        self.assertFalse(
            {"rtsp", "preview", "download", "snapshot"}.intersection(calls)
        )

    def test_auth_failure_still_closes_session(self):
        calls = []

        class Lease:
            host = "192.0.2.174"
            source_ip = "192.0.2.61"

            def close(self):
                calls.append("lease_close")

        class Baichuan:
            _first_login = True
            _push_callback = None
            _close_callback = None

            async def login(self):
                calls.append("login")
                raise ReolinkError("rejected")

        class Host:
            def __init__(self, **_kwargs):
                self.baichuan = Baichuan()

            async def logout(self):
                calls.append("logout")

        class Connection:
            connection_open = True

            def __init__(self, *_args, **_kwargs):
                pass

            async def connect(self):
                calls.append("connect")

            async def close(self):
                calls.append("connection_close")

        with (
            patch.object(camera, "Host", Host),
            patch.object(camera, "BoundBaichuanUdpConnection", Connection),
            patch.object(camera, "linux_ipv4_interface", return_value=("eth0", 2)),
            patch.object(camera, "resolve_uid_lan", return_value=Lease()),
            patch.object(camera, "validate_local_lan_route"),
            self.assertRaises(camera.CameraStageError) as raised,
        ):
            asyncio.run(
                camera.async_validate_legacy_device(
                    "camera1",
                    "admin",
                    "secret",
                        ipaddress.ip_interface("192.0.2.61/24"),
                )
            )

        self.assertEqual(raised.exception.stage, "AUTH_ERROR")
        self.assertEqual(
            calls,
            ["connect", "lease_close", "login", "logout", "connection_close"],
        )


if __name__ == "__main__":
    unittest.main()
