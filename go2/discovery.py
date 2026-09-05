"""Interface-bound, read-only GO2 multicast discovery."""

from __future__ import annotations

import ipaddress
import json
import socket
import struct
import time

import psutil


GO2_MULTICAST_GROUP = "231.1.1.1"
QUERY_PORT = 10131
REPLY_PORT = 10134
QUERY_NAME = "unitree_dapengche"
_VIRTUAL_INTERFACE_MARKERS = (
    "docker",
    "hamachi",
    "hyper-v",
    "loopback",
    "oray",
    "radmin",
    "tap",
    "tailscale",
    "tun",
    "virtual",
    "vmware",
    "vpn",
    "vethernet",
    "wsl",
    "zerotier",
)


def preferred_local_ipv4() -> str:
    """Return the IPv4 selected by the OS for ordinary routed traffic."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect chooses a route without sending a packet.
        sock.connect(("223.5.5.5", 53))
        return str(sock.getsockname()[0])
    finally:
        sock.close()


def _looks_virtual_interface(interface_name: str) -> bool:
    normalized = interface_name.casefold()
    return any(marker in normalized for marker in _VIRTUAL_INTERFACE_MARKERS)


def _usable_lan_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
    )


def local_ipv4_candidates() -> tuple[str, ...]:
    """Return physical private-LAN IPv4 interfaces in route preference order."""

    physical_addresses: list[str] = []
    try:
        interface_stats = psutil.net_if_stats()
        for interface_name, interface_addresses in psutil.net_if_addrs().items():
            stats = interface_stats.get(interface_name)
            if (
                (stats is not None and not stats.isup)
                or _looks_virtual_interface(interface_name)
            ):
                continue
            for item in interface_addresses:
                if (
                    item.family == socket.AF_INET
                    and _usable_lan_ipv4(item.address)
                    and item.address not in physical_addresses
                ):
                    physical_addresses.append(item.address)
    except (OSError, RuntimeError):
        # Route and hostname resolution remain a dependency-free fallback.
        pass

    try:
        preferred = preferred_local_ipv4()
    except OSError:
        preferred = None
    if preferred and _usable_lan_ipv4(preferred):
        preferred_network = ipaddress.ip_network(f"{preferred}/24", strict=False)
        physical_addresses = [
            value
            for value in physical_addresses
            if ipaddress.ip_address(value) in preferred_network
        ]
    if preferred in physical_addresses:
        physical_addresses.remove(preferred)
        physical_addresses.insert(0, preferred)
    if physical_addresses:
        return tuple(physical_addresses)

    fallback: list[str] = []
    if preferred and _usable_lan_ipv4(preferred):
        fallback.append(preferred)
    try:
        hostname_addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        hostname_addresses = []
    fallback.extend(
        value
        for value in hostname_addresses
        if _usable_lan_ipv4(value) and value not in fallback
    )
    return tuple(fallback)


def _route_interface_for(remote_ip: str, fallback: str) -> str:
    route_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route_socket.connect((remote_ip, QUERY_PORT))
        return str(route_socket.getsockname()[0])
    except OSError:
        return fallback
    finally:
        route_socket.close()


def discover_robot_ip(
    serial_number: str,
    *,
    local_ip: str | None = None,
    timeout: float = 3.0,
) -> tuple[str | None, str]:
    """Find one GO2 by SN while forcing multicast onto the LAN interface."""

    interface_ips = (local_ip,) if local_ip else local_ipv4_candidates()
    if not interface_ips:
        return None, "no usable IPv4 interface"
    queries = (
        json.dumps({"name": QUERY_NAME}).encode("utf-8"),
        json.dumps({"name": QUERY_NAME, "sn": serial_number}).encode("utf-8"),
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", REPLY_PORT))
        group_bytes = socket.inet_aton(GO2_MULTICAST_GROUP)
        active_interfaces: list[tuple[str, bytes]] = []
        for interface_ip in interface_ips:
            interface_bytes = socket.inet_aton(interface_ip)
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    struct.pack("=4s4s", group_bytes, interface_bytes),
                )
            except OSError:
                continue
            active_interfaces.append((interface_ip, interface_bytes))
        if not active_interfaces:
            return None, ",".join(interface_ips)

        for attempt in range(3):
            for _, interface_bytes in active_interfaces:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    interface_bytes,
                )
                for query in queries:
                    sock.sendto(query, (GO2_MULTICAST_GROUP, QUERY_PORT))
            if attempt < 2:
                time.sleep(0.2)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, ",".join(item[0] for item in active_interfaces)
            sock.settimeout(remaining)
            try:
                data, address = sock.recvfrom(2048)
            except socket.timeout:
                return None, ",".join(item[0] for item in active_interfaces)
            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if message.get("sn") == serial_number:
                robot_ip = str(message.get("ip") or address[0])
                return robot_ip, _route_interface_for(
                    robot_ip,
                    active_interfaces[0][0],
                )
    finally:
        for interface_ip in interface_ips:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_DROP_MEMBERSHIP,
                    struct.pack(
                        "=4s4s",
                        socket.inet_aton(GO2_MULTICAST_GROUP),
                        socket.inet_aton(interface_ip),
                    ),
                )
            except OSError:
                pass
        sock.close()
