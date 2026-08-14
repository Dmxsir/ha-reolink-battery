"""Pure parsers for cached battery-camera status research."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime

DECIMAL_MEGABYTE = 1_000_000


@dataclass(frozen=True, slots=True)
class BatteryState:
    """Normalized values from Baichuan BatteryInfo."""

    percent: int
    charging: bool | None
    charge_status: str
    power_source: str
    adapter_status: str
    low_power: bool | None
    temperature: int | None


@dataclass(frozen=True, slots=True)
class StorageState:
    """Normalized SD-card state from Baichuan HddInfo."""

    present: bool
    status: str
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Non-identifying device metadata."""

    model: str | None = None
    firmware: str | None = None
    hardware: str | None = None


@dataclass(frozen=True, slots=True)
class LocalState:
    """One short authenticated-session snapshot, retained while sleeping."""

    refreshed_at: datetime
    device_info: DeviceInfo
    battery: BatteryState | None
    storage: StorageState | None
    wifi_rssi_dbm: int | None


@dataclass(frozen=True, slots=True)
class CloudState:
    """Cloud-only values that never require contacting the camera."""

    model: str | None = None
    status: str | None = None
    last_event_at: datetime | None = None
    last_event_type: str | None = None
    last_event_ai_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Cloud and cached local state with independent update lifetimes."""

    cloud: CloudState
    local: LocalState | None = None

    def with_cloud(self, cloud: CloudState) -> DeviceState:
        return replace(self, cloud=cloud)

    def with_local(self, local: LocalState) -> DeviceState:
        return replace(self, local=local)


def _merge_local(previous: LocalState, current: LocalState) -> LocalState:
    """Keep last-known optional values when a short refresh omits them."""
    old_info = previous.device_info
    new_info = current.device_info
    return LocalState(
        refreshed_at=current.refreshed_at,
        device_info=DeviceInfo(
            model=new_info.model or old_info.model,
            firmware=new_info.firmware or old_info.firmware,
            hardware=new_info.hardware or old_info.hardware,
        ),
        battery=current.battery or previous.battery,
        storage=current.storage or previous.storage,
        wifi_rssi_dbm=(
            current.wifi_rssi_dbm
            if current.wifi_rssi_dbm is not None
            else previous.wifi_rssi_dbm
        ),
    )


class DeviceStatusCache:
    """Small observable cache; it never opens or polls a camera connection."""

    def __init__(self, state: DeviceState) -> None:
        self.state = state
        self._listeners: set[Callable[[], None]] = set()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def update_cloud(self, cloud: CloudState) -> None:
        previous = self.state.cloud
        self.state = self.state.with_cloud(
            CloudState(
                model=cloud.model or previous.model,
                status=cloud.status or previous.status,
                last_event_at=cloud.last_event_at or previous.last_event_at,
                last_event_type=cloud.last_event_type or previous.last_event_type,
                last_event_ai_types=(
                    cloud.last_event_ai_types or previous.last_event_ai_types
                ),
            )
        )
        self._notify()

    def update_local(self, local: LocalState) -> None:
        if self.state.local is not None:
            local = _merge_local(self.state.local, local)
        self.state = self.state.with_local(local)
        self._notify()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


def local_state_as_dict(state: LocalState) -> dict[str, object]:
    """Serialize the trusted cache snapshot for a Home Assistant config entry."""
    data = asdict(state)
    data["refreshed_at"] = state.refreshed_at.isoformat()
    return data


def local_state_from_dict(data: object) -> LocalState | None:
    """Restore a cache snapshot, rejecting incomplete or malformed data."""
    if not isinstance(data, dict):
        return None
    try:
        refreshed_at = datetime.fromisoformat(str(data["refreshed_at"]))
        if refreshed_at.tzinfo is None:
            return None
        device_info = DeviceInfo(**data["device_info"])
        battery_data = data.get("battery")
        storage_data = data.get("storage")
        battery = BatteryState(**battery_data) if battery_data is not None else None
        storage = StorageState(**storage_data) if storage_data is not None else None
        raw_wifi = data.get("wifi_rssi_dbm")
        wifi = parse_wifi_rssi(raw_wifi)
        if raw_wifi is not None and wifi is None:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return LocalState(refreshed_at, device_info, battery, storage, wifi)


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    return None


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def parse_battery_info(data: Mapping[str, object] | None) -> BatteryState | None:
    """Normalize the fields parsed by reolink_aio's cmd253 handler."""
    if data is None:
        return None
    percent = _integer(data.get("batteryPercent"))
    if percent is None or not 0 <= percent <= 100:
        return None

    raw_charge = data.get("chargeStatus")
    charge_key = (
        str(raw_charge).strip().casefold() if raw_charge is not None else ""
    )
    charge_status = {
        "0": "discharging",
        "none": "discharging",
        "discharging": "discharging",
        "1": "charging",
        "charging": "charging",
        "2": "complete",
        "complete": "complete",
        "completed": "complete",
        "chargecomplete": "complete",
    }.get(charge_key, "unknown")
    charging = {
        "discharging": False,
        "charging": True,
        "complete": False,
    }.get(charge_status)

    adapter = _optional_text(data.get("adapterStatus")) or "unknown"
    adapter_key = adapter.casefold()
    power_status = str(data.get("powerSupplyStatus") or "").casefold()
    if adapter_key == "solarpanels":
        power_source = "solar"
    elif adapter_key in {"adapter", "acadapter", "dcadapter"} or power_status in {"normal", "lowchargeonly"}:
        power_source = "external"
    elif adapter_key == "none" and charging is False:
        power_source = "battery"
    else:
        power_source = "unknown"

    low_power_value = _integer(data.get("lowPower"))
    return BatteryState(
        percent=percent,
        charging=charging,
        charge_status=charge_status,
        power_source=power_source,
        adapter_status=adapter,
        low_power=None if low_power_value is None else bool(low_power_value),
        temperature=_integer(data.get("temperature")),
    )


def parse_storage_info(
    disks: Sequence[Mapping[str, object]] | None,
) -> StorageState | None:
    """Normalize reolink_aio HddInfo values; capacity/size are decimal MB."""
    if disks is None:
        return None
    sd_cards = [
        disk
        for disk in disks
        if str(disk.get("storageType", 2)).casefold()
        in {"2", "sd", "sdcard", "sd_card"}
    ]
    if not sd_cards:
        return StorageState(present=False, status="absent")

    disk = sd_cards[0]
    formatted = _integer(disk.get("format")) == 1
    mounted = _integer(disk.get("mount")) == 1
    status = "normal" if formatted and mounted else "error"
    total_mb = _integer(disk.get("capacity"))
    free_mb = _integer(disk.get("size"))
    if (
        total_mb is None
        or free_mb is None
        or total_mb < 0
        or free_mb < 0
        or free_mb > total_mb
    ):
        return StorageState(present=True, status="error")

    total = total_mb * DECIMAL_MEGABYTE
    free = free_mb * DECIMAL_MEGABYTE
    return StorageState(
        present=True,
        status=status,
        total_bytes=total,
        used_bytes=total - free,
        free_bytes=free,
    )


def parse_device_info(data: Mapping[str, object] | None) -> DeviceInfo:
    """Keep only model and version fields, excluding serial/item identifiers."""
    data = data or {}
    return DeviceInfo(
        model=_optional_text(data.get("type") or data.get("model")),
        firmware=_optional_text(data.get("firmwareVersion")),
        hardware=_optional_text(data.get("hardwareVersion")),
    )


def parse_wifi_rssi(value: object) -> int | None:
    """Accept only the documented Baichuan dBm range, not UI quality levels."""
    signal = _integer(value)
    return signal if signal is not None and -127 <= signal <= 0 else None


def parse_local_state(
    *,
    refreshed_at: datetime,
    device_info: Mapping[str, object] | None,
    battery: Mapping[str, object] | None,
    storage: Sequence[Mapping[str, object]] | None,
    wifi_signal: object,
) -> LocalState:
    """Build one immutable local snapshot from an already-open session."""
    return LocalState(
        refreshed_at=refreshed_at,
        device_info=parse_device_info(device_info),
        battery=parse_battery_info(battery),
        storage=parse_storage_info(storage),
        wifi_rssi_dbm=parse_wifi_rssi(wifi_signal),
    )
