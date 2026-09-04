from __future__ import annotations

from pathlib import Path

import pytest

from go2.config import ConfigurationError, load_config


BASE_YAML = """
connection:
  mode: local_sta
  ip: 192.168.8.181
  connect_timeout: 12
  require_aes_key: true
safety:
  max_vx: 0.25
  max_vy: 0.20
  max_yaw: 0.5
  watchdog_timeout: 0.4
logging:
  level: INFO
"""


def write_config(tmp_path: Path, text: str = BASE_YAML) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_environment_overrides_yaml_ip_and_secret_is_hidden(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path),
        environ={
            "UNITREE_REGION": "cn",
            "UNITREE_ROBOT_IP": "10.0.0.42",
            "UNITREE_AES_128_KEY": "a" * 32,
            "UNITREE_SERIAL_NUMBER": "TEST-SN",
        },
        load_env_file=False,
    )

    assert config.connection.ip == "10.0.0.42"
    assert config.connection.aes_128_key == "a" * 32
    assert config.connection.preflight_errors() == []
    assert "a" * 32 not in repr(config.connection)
    assert len(config.robots) == 1
    assert config.robots[0].connection is config.connection


def test_numbered_robot_profiles_are_isolated_and_extensible(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path),
        environ={
            "UNITREE_ROBOT_IP": "10.0.0.41",
            "UNITREE_AES_128_KEY": "a" * 32,
            "UNITREE_SERIAL_NUMBER": "DOG-1",
            "UNITREE_ROBOT_2_LABEL": "左侧狗",
            "UNITREE_ROBOT_2_IP": "10.0.0.42",
            "UNITREE_ROBOT_2_SERIAL_NUMBER": "DOG-2",
            "UNITREE_ROBOT_2_AES_128_KEY": "b" * 32,
            "UNITREE_ROBOT_3_SERIAL_NUMBER": "DOG-3",
            "UNITREE_ROBOT_3_AES_128_KEY": "d" * 32,
            "UNITREE_ROBOT_4_IP": "10.0.0.44",
            "UNITREE_ROBOT_4_SERIAL_NUMBER": "DOG-4",
            "UNITREE_ROBOT_4_AES_128_KEY": "c" * 32,
            "UNITREE_ROBOT_5_IP": "10.0.0.45",
            "UNITREE_ROBOT_5_SERIAL_NUMBER": "DOG-5",
            "UNITREE_ROBOT_5_AES_128_KEY": "e" * 32,
            "UNITREE_ROBOT_6_IP": "10.0.0.46",
            "UNITREE_ROBOT_6_SERIAL_NUMBER": "DOG-6",
            "UNITREE_ROBOT_6_AES_128_KEY": "f" * 32,
        },
        load_env_file=False,
    )
    assert [profile.id for profile in config.robots] == [
        "dog_1",
        "dog_2",
        "dog_3",
        "dog_4",
        "dog_5",
        "dog_6",
    ]
    assert config.robots[1].label == "左侧狗"
    assert config.robots[1].connection.aes_128_key == "b" * 32
    assert config.robots[2].connection.target_ip is None
    assert config.robots[2].connection.preflight_errors() == []
    assert "b" * 32 not in repr(config.robots)


def test_duplicate_fleet_identity_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="distinct IP"):
        load_config(
            write_config(tmp_path),
            environ={
                "UNITREE_ROBOT_IP": "10.0.0.41",
                "UNITREE_AES_128_KEY": "a" * 32,
                "UNITREE_ROBOT_2_IP": "10.0.0.41",
                "UNITREE_ROBOT_2_AES_128_KEY": "b" * 32,
            },
            load_env_file=False,
        )


def test_local_ap_uses_fixed_robot_ip(tmp_path: Path) -> None:
    text = BASE_YAML.replace("mode: local_sta", "mode: local_ap")
    config = load_config(
        write_config(tmp_path, text),
        environ={"UNITREE_AES_128_KEY": "1" * 32},
        load_env_file=False,
    )
    assert config.connection.target_ip == "192.168.12.1"


def test_missing_key_is_reported_as_preflight_error(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path), environ={}, load_env_file=False
    )
    assert any("UNITREE_AES_128_KEY" in item for item in config.connection.preflight_errors())


@pytest.mark.parametrize("key", ["short", "z" * 32, "1" * 31, "1" * 33])
def test_invalid_aes_key_is_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(ConfigurationError, match="32 hexadecimal"):
        load_config(
            write_config(tmp_path),
            environ={"UNITREE_AES_128_KEY": key},
            load_env_file=False,
        )


def test_unknown_option_is_rejected(tmp_path: Path) -> None:
    text = BASE_YAML.replace("  connect_timeout: 12", "  connect_timeout: 12\n  typo: true")
    with pytest.raises(ConfigurationError, match="Unknown connection option"):
        load_config(write_config(tmp_path, text), environ={}, load_env_file=False)
