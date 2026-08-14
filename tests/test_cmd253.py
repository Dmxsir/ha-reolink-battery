"""Offline cmd253 channel-0 compatibility test; no camera is contacted."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
