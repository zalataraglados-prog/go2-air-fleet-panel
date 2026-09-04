from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.fetch_key_to_env import (
    configured_serials,
    read_credentials,
    select_device,
    write_env,
)


@dataclass
class FakeDevice:
    sn: str
    key: str
    series: str = "Go2"
    model: str = "Air"


def test_read_unlabeled_credentials(tmp_path: Path) -> None:
    path = tmp_path / "credentials.txt"
    path.write_text("user@example.com\nraw-password\n", encoding="utf-8")
    assert read_credentials(path) == ("user@example.com", "raw-password")


def test_read_labeled_credentials(tmp_path: Path) -> None:
    path = tmp_path / "credentials.txt"
    path.write_text("邮箱：user@example.com\nkey:raw-password\n", encoding="utf-8")
    assert read_credentials(path) == ("user@example.com", "raw-password")


def test_select_device_excludes_already_assigned_robot() -> None:
    first = FakeDevice("serial-one", "a" * 32)
    second = FakeDevice("serial-two", "b" * 32)

    assert select_device(
        [first, second],
        excluded_serials=frozenset({"serial-one"}),
    ) is second


def test_select_device_rejects_ambiguous_unassigned_robots() -> None:
    devices = [
        FakeDevice("serial-one", "a" * 32),
        FakeDevice("serial-two", "b" * 32),
    ]

    with pytest.raises(RuntimeError, match='"status": "AMBIGUOUS"'):
        select_device(devices)


def test_select_device_can_choose_stable_candidate_index() -> None:
    later = FakeDevice("serial-two", "b" * 32)
    earlier = FakeDevice("serial-one", "a" * 32)

    assert select_device(
        [later, earlier],
        candidate_index=1,
    ) is earlier
    assert select_device(
        [later, earlier],
        candidate_index=2,
    ) is later
    with pytest.raises(RuntimeError, match="CANDIDATE_INDEX_OUT_OF_RANGE"):
        select_device([later, earlier], candidate_index=3)


def test_write_second_slot_preserves_primary_and_ip(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "UNITREE_REGION=cn\n"
        "UNITREE_ROBOT_IP=192.0.2.1\n"
        "UNITREE_AES_128_KEY=" + "a" * 32 + "\n"
        "UNITREE_SERIAL_NUMBER=serial-one\n"
        "UNITREE_ROBOT_2_LABEL=机器狗 2\n"
        "UNITREE_ROBOT_2_IP=192.0.2.2\n"
        "UNITREE_ROBOT_2_SERIAL_NUMBER=\n"
        "UNITREE_ROBOT_2_AES_128_KEY=\n",
        encoding="utf-8",
    )

    write_env(path, FakeDevice("serial-two", "B" * 32), "cn", slot=2)

    content = path.read_text(encoding="utf-8")
    assert "UNITREE_ROBOT_IP=192.0.2.1" in content
    assert f"UNITREE_AES_128_KEY={'a' * 32}" in content
    assert "UNITREE_ROBOT_2_IP=192.0.2.2" in content
    assert "UNITREE_ROBOT_2_SERIAL_NUMBER=serial-two" in content
    assert f"UNITREE_ROBOT_2_AES_128_KEY={'b' * 32}" in content
    assert configured_serials(path, excluding_slot=2) == frozenset({"serial-one"})


def test_write_new_slot_can_include_discovered_ip(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("UNITREE_REGION=cn\n", encoding="utf-8")

    write_env(
        path,
        FakeDevice("serial-three", "C" * 32),
        "cn",
        slot=3,
        target_ip="192.0.2.3",
    )

    content = path.read_text(encoding="utf-8")
    assert "UNITREE_ROBOT_3_IP=192.0.2.3" in content
    assert "UNITREE_ROBOT_3_SERIAL_NUMBER=serial-three" in content
    assert f"UNITREE_ROBOT_3_AES_128_KEY={'c' * 32}" in content
