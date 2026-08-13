"""LAN selection helpers independent of Home Assistant internals."""

from __future__ import annotations

import ipaddress
from typing import Any


def lan_network_choices(adapters: list[dict[str, Any]]) -> dict[str, str]:
    """Return safe physical-LAN candidates from HA network adapters."""
    choices: dict[str, str] = {}
    for adapter in adapters:
        if not adapter.get("enabled", True):
            continue
        name = str(adapter.get("name") or "network")
        for ip_info in adapter.get("ipv4", []):
            if not isinstance(ip_info, dict):
                continue
            address = ip_info.get("address")
            prefix = ip_info.get("network_prefix")
            try:
                interface = ipaddress.ip_interface(f"{address}/{prefix}")
            except ValueError:
                continue
            if (
                not isinstance(interface, ipaddress.IPv4Interface)
                or interface.ip.is_loopback
                or interface.ip.is_link_local
                or not interface.ip.is_private
                or interface.network.prefixlen > 30
            ):
                continue
            value = str(interface)
            choices[value] = f"{name} — {value}"
    return choices
