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


def test_local_ipv4_candidates_include_non_default_lan_and_filter_link_local(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        discovery,
        "preferred_local_ipv4",
        lambda: "10.3.93.125",
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
            "Old Ethernet": [
                SimpleNamespace(
                    family=discovery.socket.AF_INET,
                    address="172.16.0.9",
                )
            ],
        },
    )

    assert discovery.local_ipv4_candidates() == (
        "10.3.93.125",
        "192.168.31.178",
        "192.168.0.110",
    )
