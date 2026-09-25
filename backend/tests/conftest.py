"""Shared test setup."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _device_key_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal device key (``viseca.demo.device_key_path``, ``passport.cli``) is written
    under the test's tmp dir, never ``~/.config/oneguard`` of whoever runs the tests."""
    monkeypatch.setenv("ONEGUARD_DEVICE_KEY", str(tmp_path / "device.pem"))
