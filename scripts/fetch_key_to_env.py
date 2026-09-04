#!/usr/bin/env python3
"""Fetch one GO2 AES key into an isolated fleet slot without printing it."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from curl_cffi import CurlOpt, requests as cffi_requests  # noqa: E402
from unitree_webrtc_connect import UnitreeCloud, UnitreeCloudError  # noqa: E402
from go2.discovery import discover_robot_ip  # noqa: E402


KEY_PATTERN = re.compile(r"[0-9a-fA-F]{32}")
EMAIL_LABELS = {"email", "account", "邮箱", "账号"}
PASSWORD_LABELS = {"password", "pass", "pwd", "key", "密码", "密钥"}


def classify_cloud_message(message: object) -> str:
    text = str(message or "").casefold()
    groups = (
        ("verification_required", ("captcha", "verify", "verification", "验证码", "验证")),
        ("rate_limited", ("frequent", "too many", "rate", "频繁", "稍后")),
        ("region_mismatch", ("region", "area", "地区", "区域")),
        (
            "credential_rejected",
            ("password", "passwd", "email", "account", "login", "密码", "邮箱", "账号", "登录"),
        ),
    )
    for category, markers in groups:
        if any(marker in text for marker in markers):
            return category
    return "empty_message" if not text else "cloud_rejected"


def sanitize_cloud_message(message: object, email: str, password: str) -> str:
    text = str(message or "")
    for secret in (email, password):
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"[^\s@]+@[^\s@]+", "<email>", text)
    text = re.sub(r"\b[0-9a-fA-F]{24,}\b", "<token>", text)
    return text


def read_credentials(path: Path) -> tuple[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read credentials file: {path}") from exc
    if len(lines) != 2:
        raise ValueError(
            "Credentials file must contain exactly two lines: email, password"
        )

    def value_from_line(line: str, labels: set[str]) -> str:
        for delimiter in ("=", ":", "："):
            if delimiter not in line:
                continue
            label, value = line.split(delimiter, 1)
            if label.strip().casefold() in labels:
                return value.strip()
        return line.strip()

    email = value_from_line(lines[0], EMAIL_LABELS)
    password = value_from_line(lines[1], PASSWORD_LABELS)
    if not email or not password:
        raise ValueError("Email and password values must not be empty")
    return email, password


def select_device(
    devices: list[object],
    *,
    excluded_serials: frozenset[str] = frozenset(),
    candidate_index: int | None = None,
) -> object:
    keyed = [
        device
        for device in devices
        if KEY_PATTERN.fullmatch(str(getattr(device, "key", "") or ""))
        and str(getattr(device, "sn", "") or "").strip()
        and str(getattr(device, "sn", "") or "").strip() not in excluded_serials
    ]
    go2_keyed = [
        device
        for device in keyed
        if "go2"
        in f"{getattr(device, 'series', '')} {getattr(device, 'model', '')}".lower()
    ]
    candidates = sorted(
        go2_keyed or keyed,
        key=lambda device: str(getattr(device, "sn", "") or ""),
    )
    if candidate_index is not None:
        if not 1 <= candidate_index <= len(candidates):
            raise RuntimeError(
                json.dumps(
                    {
                        "status": "CANDIDATE_INDEX_OUT_OF_RANGE",
                        "candidate_count": len(candidates),
                    }
                )
            )
        return candidates[candidate_index - 1]
    if len(candidates) != 1:
        raise RuntimeError(
            json.dumps(
                {
                    "status": "AMBIGUOUS",
                    "device_count": len(devices),
                    "keyed_device_count": len(keyed),
                    "go2_keyed_device_count": len(go2_keyed),
                    "excluded_device_count": len(excluded_serials),
                }
            )
        )
    return candidates[0]


def _env_name(slot: int, field: str) -> str:
    return f"UNITREE_{field}" if slot == 1 else f"UNITREE_ROBOT_{slot}_{field}"


def _read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def configured_serials(path: Path, *, excluding_slot: int) -> frozenset[str]:
    values = _read_env_values(path)
    serials = {
        values.get(_env_name(slot, "SERIAL_NUMBER"), "").strip()
        for slot in range(1, 17)
        if slot != excluding_slot
    }
    return frozenset(serial for serial in serials if serial)


def _upsert_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    result: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        name = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if name in remaining and not stripped.startswith("#"):
            result.append(f"{name}={remaining.pop(name)}")
        else:
            result.append(raw_line)
    if remaining and result and result[-1]:
        result.append("")
    result.extend(f"{name}={value}" for name, value in remaining.items())
    path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def write_env(
    path: Path,
    device: object,
    region: str,
    *,
    slot: int = 1,
    target_ip: str | None = None,
) -> None:
    key = str(getattr(device, "key", "")).lower()
    serial = str(getattr(device, "sn", ""))
    updates = {
        "UNITREE_REGION": region,
        _env_name(slot, "AES_128_KEY"): key,
        _env_name(slot, "SERIAL_NUMBER"): serial,
    }
    if target_ip is not None:
        updates[
            "UNITREE_ROBOT_IP" if slot == 1 else _env_name(slot, "IP")
        ] = str(ipaddress.ip_address(target_ip))
    _upsert_env_values(path, updates)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch one GO2 key into a fleet slot without printing credentials, "
            "key, or serial number."
        )
    )
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--region", choices=("cn", "global"), default="cn")
    parser.add_argument(
        "--slot",
        type=int,
        choices=range(1, 17),
        default=1,
        help="Fleet slot to update (1-16); other configured serials are excluded",
    )
    parser.add_argument(
        "--resolve-ip",
        help="Process-local HTTPS address override when system DNS is broken",
    )
    parser.add_argument(
        "--candidate-index",
        type=int,
        choices=range(1, 17),
        help=(
            "Select one remaining device by its stable serial-number sort order; "
            "required only when more than one unassigned device remains"
        ),
    )
    parser.add_argument(
        "--discover-ip",
        action="store_true",
        help="Resolve the selected robot's LAN IP by read-only multicast discovery",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    email = ""
    password = ""
    try:
        email, password = read_credentials(args.credentials_file.resolve())
        cloud = UnitreeCloud(region=args.region, device_type="Go2")
        if args.resolve_ip:
            resolved_ip = str(ipaddress.ip_address(args.resolve_ip))
            hostname = (
                "robot-api.unitree.com"
                if args.region == "cn"
                else "global-robot-api.unitree.com"
            )
            cloud._session = cffi_requests.Session(
                impersonate="chrome120",
                curl_options={
                    CurlOpt.RESOLVE: [f"{hostname}:443:{resolved_ip}"]
                },
            )
        cloud.login_email(email, password)
        devices = cloud.list_devices()
        output_path = args.output.resolve()
        device = select_device(
            devices,
            excluded_serials=configured_serials(
                output_path,
                excluding_slot=args.slot,
            ),
            candidate_index=args.candidate_index,
        )
        target_ip = None
        if args.discover_ip:
            target_ip, _ = discover_robot_ip(str(getattr(device, "sn", "")))
            if target_ip is None:
                raise RuntimeError(
                    json.dumps(
                        {
                            "status": "ROBOT_NOT_FOUND",
                            "env_written": False,
                        }
                    )
                )
        write_env(
            output_path,
            device,
            args.region,
            slot=args.slot,
            target_ip=target_ip,
        )
        print(
            json.dumps(
                {
                    "status": "SUCCESS",
                    "selected_slot": args.slot,
                    "device_count": len(devices),
                    "selected_family": getattr(device, "series", "")
                    or getattr(device, "model", "")
                    or "unknown",
                    "online": getattr(device, "online", None),
                    "key_length": len(str(getattr(device, "key", ""))),
                    "ip_discovered": target_ip is not None,
                    "env_written": True,
                }
            )
        )
        return 0
    except UnitreeCloudError as exc:
        print(
            json.dumps(
                {
                    "status": "CLOUD_ERROR",
                    "action": exc.action,
                    "code": exc.code,
                    "reason_category": classify_cloud_message(exc.msg),
                    "message_length": len(str(exc.msg or "")),
                    "safe_message": sanitize_cloud_message(
                        exc.msg, email, password
                    ),
                }
            )
        )
        return 1
    except cffi_requests.RequestsError as exc:
        print(
            json.dumps(
                {
                    "status": "NETWORK_ERROR",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    except (ValueError, RuntimeError) as exc:
        message = str(exc)
        print(message if message.startswith("{") else json.dumps({"status": "ERROR"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
