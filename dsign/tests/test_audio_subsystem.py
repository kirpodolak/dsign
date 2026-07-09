"""Integration-style unit tests for the audio subsystem (backlog T-AUD).

Covers ALSA PCM reopen, MPV audio route rebind, and mute/volume propagation
without requiring real hardware or amixer on the CI runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union
from unittest.mock import MagicMock

import pytest

from dsign.services.playlist_management import PlaylistManager
from dsign.services.settings_service import SettingsService
from test_mpv_manager_send_command import DummySession, StubMPVManager


def _settings_service(tmp_path: Path, null_logger) -> SettingsService:
    settings_file = tmp_path / "settings.json"
    upload = tmp_path / "upload"
    upload.mkdir(parents=True, exist_ok=True)
    svc = SettingsService(str(settings_file), str(upload), logger=null_logger)
    svc.save_settings(dict(SettingsService.DEFAULT_SETTINGS))
    return svc


def _playlist_manager(null_logger, tmp_path, *, mpv=None, settings=None) -> PlaylistManager:
    mpv = mpv or MagicMock()
    logo = MagicMock()
    pm = PlaylistManager(null_logger, None, str(tmp_path), MagicMock(), mpv, logo)
    if settings is not None:
        pm.set_settings_service(settings)
    return pm


def test_expand_audio_route_explicit_device(tmp_path, null_logger):
    svc = _settings_service(tmp_path, null_logger)
    settings = svc.load_settings()
    settings["mpv"] = {"audio-device": "alsa/hdmi:CARD=PCH,DEV=1"}
    svc.save_settings(settings)

    out = svc.expand_audio_route("hdmi", settings=settings)

    assert out == {"ao": "alsa", "audio-device": "alsa/hdmi:CARD=PCH,DEV=1"}


def test_expand_audio_route_hdmi_uses_pch_when_available(tmp_path, null_logger, monkeypatch):
    svc = _settings_service(tmp_path, null_logger)
    monkeypatch.setattr(
        svc,
        "_pch_hdmi_mpv_device",
        lambda **kwargs: "alsa/hdmi:CARD=PCH,DEV=2",
    )

    out = svc.expand_audio_route("hdmi")

    assert out == {"ao": "alsa", "audio-device": "alsa/hdmi:CARD=PCH,DEV=2"}


def test_expand_audio_route_hdmi_fallback_vc4hdmi(tmp_path, null_logger, monkeypatch):
    svc = _settings_service(tmp_path, null_logger)
    monkeypatch.setattr(svc, "_pch_hdmi_mpv_device", lambda **kwargs: None)

    out = svc.expand_audio_route("hdmi")

    assert out == {"ao": "alsa", "audio-device": "alsa/hdmi:CARD=vc4hdmi0,DEV=0"}


def test_build_mpv_audio_updates_unmutes_and_resolves_route(tmp_path, null_logger, monkeypatch):
    svc = _settings_service(tmp_path, null_logger)
    settings = svc.load_settings()
    settings["mpv"] = {"audio-route": "hdmi"}
    svc.save_settings(settings)

    calls: list[str | None] = []

    def _fake_unmute(*, dev=None):
        calls.append(dev)

    monkeypatch.setattr(svc, "unmute_pch_digital_outputs", _fake_unmute)
    monkeypatch.setattr(
        svc,
        "expand_audio_route",
        lambda route, settings=None: {
            "ao": "alsa",
            "audio-device": "alsa/hdmi:CARD=PCH,DEV=0",
        },
    )

    updates = svc.build_mpv_audio_updates(settings=settings)

    assert updates["ao"] == "alsa"
    assert updates["audio-device"] == "alsa/hdmi:CARD=PCH,DEV=0"
    assert calls == ["0"]


def test_set_master_audio_persists_volume_and_mute(tmp_path, null_logger):
    svc = _settings_service(tmp_path, null_logger)

    state = svc.set_master_audio(volume_percent=42, muted=True)

    assert state == {"volume": 42, "mute": True}
    loaded = svc.load_settings()
    assert loaded["volume"] == 42
    assert loaded["mute"] is True


def test_alsa_dev_index_from_mpv_device():
    assert SettingsService._alsa_dev_index_from_mpv_device("alsa/hdmi:CARD=PCH,DEV=3") == "3"
    assert SettingsService._alsa_dev_index_from_mpv_device("auto") is None
    assert SettingsService._alsa_dev_index_from_mpv_device("") is None


def test_rebind_audio_output_no_cycle_sets_device_only(null_logger):
    session = DummySession([{"error": "success", "data": None}])
    mgr = StubMPVManager(logger=null_logger, session=session)

    ok = mgr.rebind_audio_output("alsa", "alsa/hdmi:CARD=PCH,DEV=0", cycle_ao=False)

    assert ok is True
    assert len(session.calls) == 1
    assert session.calls[0]["payload"]["command"] == [
        "set_property",
        "audio-device",
        "alsa/hdmi:CARD=PCH,DEV=0",
    ]


def test_rebind_audio_output_cycles_ao_when_idle(null_logger):
    behaviours: List[Union[Dict[str, Any], Any]] = [
        {"error": "success", "data": None},  # update_settings ao
        {"error": "success", "data": True},  # idle-active
        {"error": "success", "data": None},  # ao ""
        {"error": "success", "data": None},  # ao alsa
        {"error": "success", "data": None},  # audio-device
    ]
    session = DummySession(behaviours)
    mgr = StubMPVManager(logger=null_logger, session=session)
    mgr.get_property_light = MagicMock(return_value=True)  # type: ignore[method-assign]

    ok = mgr.rebind_audio_output("alsa", "alsa/hdmi:CARD=PCH,DEV=1", cycle_ao=True)

    assert ok is True
    commands = [c["payload"]["command"] for c in session.calls]
    assert ["set_property", "ao", ""] in commands
    assert ["set_property", "ao", "alsa"] in commands
    assert ["set_property", "audio-device", "alsa/hdmi:CARD=PCH,DEV=1"] in commands


def test_force_alsa_ao_open_cycles_ao_and_device(null_logger):
    session = DummySession(
        [
            {"error": "success", "data": None},
            {"error": "success", "data": None},
            {"error": "success", "data": None},
        ]
    )
    mgr = StubMPVManager(logger=null_logger, session=session)

    ok = mgr.force_alsa_ao_open("alsa", "alsa/hdmi:CARD=PCH,DEV=0")

    assert ok is True
    commands = [c["payload"]["command"] for c in session.calls]
    assert commands[0] == ["set_property", "ao", ""]
    assert commands[1] == ["set_property", "ao", "alsa"]
    assert commands[2] == ["set_property", "audio-device", "alsa/hdmi:CARD=PCH,DEV=0"]


def test_effective_playback_muted_combines_global_profile_item(tmp_path, null_logger):
    svc = _settings_service(tmp_path, null_logger)
    pm = _playlist_manager(null_logger, tmp_path, settings=svc)

    assert pm._effective_playback_muted(item_muted=False, profile_muted=False) is False

    svc.set_master_audio(muted=True)
    assert pm._effective_playback_muted(item_muted=False, profile_muted=False) is True

    svc.set_master_audio(muted=False)
    assert pm._effective_playback_muted(item_muted=True, profile_muted=False) is True
    assert pm._effective_playback_muted(item_muted=False, profile_muted=True) is True


def test_sync_settings_audio_route_calls_rebind(tmp_path, null_logger, monkeypatch):
    svc = _settings_service(tmp_path, null_logger)
    settings = svc.load_settings()
    settings["mpv"] = {"audio-route": "hdmi"}
    svc.save_settings(settings)

    monkeypatch.setattr(
        svc,
        "build_mpv_audio_updates",
        lambda **kwargs: {"ao": "alsa", "audio-device": "alsa/hdmi:CARD=PCH,DEV=0"},
    )

    mpv = MagicMock()
    mpv.rebind_audio_output.return_value = True
    pm = _playlist_manager(null_logger, tmp_path, mpv=mpv, settings=svc)

    assert pm._sync_settings_audio_route_to_mpv(cycle_ao=False) is True
    mpv.rebind_audio_output.assert_called_once_with(
        "alsa",
        "alsa/hdmi:CARD=PCH,DEV=0",
        cycle_ao=False,
    )


def test_ensure_mpv_alsa_pcm_open_when_codec_active_pcm_closed(tmp_path, null_logger, monkeypatch):
    svc = _settings_service(tmp_path, null_logger)
    monkeypatch.setattr(
        svc,
        "build_mpv_audio_updates",
        lambda **kwargs: {"ao": "alsa", "audio-device": "alsa/hdmi:CARD=PCH,DEV=0"},
    )

    mpv = MagicMock()
    mpv.get_property_light.side_effect = lambda prop, timeout=1.0: {
        "audio-codec-name": "aac",
        "time-pos": 1.5,
    }.get(prop)
    mpv.force_alsa_ao_open.return_value = True

    pm = _playlist_manager(null_logger, tmp_path, mpv=mpv, settings=svc)
    monkeypatch.setattr(pm, "_alsa_pcm_has_running_playback", lambda: False)
    monkeypatch.setattr(pm, "_kick_alsa_hardware_after_demuxer", lambda: None)
    monkeypatch.setattr(pm, "_sync_settings_volume_to_mpv", lambda: None)

    pm._ensure_mpv_alsa_pcm_open()

    mpv.force_alsa_ao_open.assert_called_once_with("alsa", "alsa/hdmi:CARD=PCH,DEV=0")


def test_reapply_effective_mute_to_mpv(tmp_path, null_logger):
    svc = _settings_service(tmp_path, null_logger)
    svc.set_master_audio(muted=True)

    mpv = MagicMock()
    pm = _playlist_manager(null_logger, tmp_path, mpv=mpv, settings=svc)
    pm._set_playback_mute_context(item_muted=False, profile_muted=False)

    pm.reapply_effective_mute_to_mpv()

    mpv._send_command.assert_called_once()
    payload = mpv._send_command.call_args[0][0]
    assert payload["command"] == ["set_property", "mute", "yes"]
