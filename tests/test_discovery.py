from __future__ import annotations

from unittest.mock import Mock
from types import SimpleNamespace

import go2.discovery as discovery


def test_preferred_local_ipv4_uses_routed_socket(monkeypatch) -> None:
    fake_socket = Mock()
    fake_socket.getsockname.return_value = ("192.168.31.178", 12345)
    monkeypatch.setattr(discovery.socket, "socket", lambda *args: fake_socket)

    assert discovery.preferred_local_ipv4() == "192.168.31.178"
    fake_socket.connect.assert_called_once_with(("223.5.5.5", 53))
    fake_socket.close.assert_called_once()


def test_local_ipv4_candidates_keep_physical_lans_and_filter_virtual_adapters(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        discovery,
        "preferred_local_ipv4",
        lambda: "192.168.0.110",
    )
    monkeypatch.setattr(discovery.socket, "gethostname", lambda: "workstation")
    monkeypatch.setattr(
        discovery.socket,
        "gethostbyname_ex",
        lambda _: (
            "workstation",
            [],
            [
                "10.3.93.125",
                "192.168.31.178",
                "169.254.25.41",
                "127.0.0.1",
            ],
        ),
    )
    monkeypatch.setattr(
        discovery.psutil,
        "net_if_stats",
        lambda: {
            "WLAN": SimpleNamespace(isup=True),
            "Ethernet": SimpleNamespace(isup=True),
            "Tailscale": SimpleNamespace(isup=True),
            "Radmin VPN": SimpleNamespace(isup=True),
            "VirtualBox Host-Only": SimpleNamespace(isup=True),
            "Old Ethernet": SimpleNamespace(isup=False),
        },
    )
    monkeypatch.setattr(
        discovery.psutil,
        "net_if_addrs",
        lambda: {
            "WLAN": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="192.168.0.110",
                )
            ],
            "Ethernet": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="192.168.31.178",
                )
            ],
            "Tailscale": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="100.64.0.2",
                )
            ],
            "Radmin VPN": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="26.3.4.5",
                )
            ],
            "VirtualBox Host-Only": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="192.168.56.1",
                )
            ],
            "Old Ethernet": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="172.16.0.9",
                )
            ],
        },
    )

    assert discovery.local_ipv4_candidates() == ("192.168.0.110",)


def test_local_ipv4_candidates_fall_back_when_interface_inventory_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        discovery,
        "preferred_local_ipv4",
        lambda: "192.168.8.20",
    )
    monkeypatch.setattr(
        discovery.psutil,
        "net_if_stats",
        lambda: (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setattr(discovery.socket, "gethostname", lambda: "workstation")
    monkeypatch.setattr(
        discovery.socket,
        "gethostbyname_ex",
        lambda _: (
            "workstation",
            [],
            ["192.168.8.20", "169.254.1.2", "127.0.0.1"],
        ),
    )

    assert discovery.local_ipv4_candidates() == ("192.168.8.20",)
