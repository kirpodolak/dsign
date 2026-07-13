"""Playlist play() implementation extracted from PlaylistManager (H-REF PR4)."""

from __future__ import annotations

import os
import traceback
from threading import Thread
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .playback_constants import PlaybackConstants

if TYPE_CHECKING:
    from .playlist_management import PlaylistManager


class PlaybackPlayRunner:
    """Resolves playlist items, picks playback mode, starts engine thread."""

    def __init__(self, pm: "PlaylistManager") -> None:
        self._pm = pm

    def run(
        self,
        playlist_id: int,
        *,
        start_index: int = 0,
        preserve_stall_tracking: bool = False,
        single_pass: bool = False,
        source: str = "manual",
        rule_id: Optional[int] = None,
    ) -> bool:
        from ..models import PlaybackStatus, Playlist, PlaylistProfileAssignment, PlaybackProfile

        try:
            # Stop any previous manual playback loop
            self._pm._stop_play_thread(preserve_stall_tracking=preserve_stall_tracking)
            self._pm._cancel_content_cache_prefetches()
            self._pm._prune_media_backoff()
            # Mark playback starting before DB/profile IPC so Wi-Fi-on-display skips.
            self._pm._set_playback_active_marker(True)
            self._pm._audio_route_applied_for_play = False

            # Get playlist and validate
            playlist = self._pm.db_session.query(Playlist).get(playlist_id)
            if not playlist:
                raise ValueError(f"Playlist {playlist_id} not found")

            # Get assigned profile if exists
            profile_settings = {}
            assignment = self._pm.db_session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=playlist_id
            ).first()

            if assignment and assignment.profile_id:
                profile = self._pm.db_session.query(PlaybackProfile).get(assignment.profile_id)
                if profile:
                    # `settings` is stored as JSON in DB, so it is already a dict.
                    profile_settings = profile.settings or {}

            profile_muted = bool(profile_settings.get("mute", False))

            # Apply profile settings first
            if profile_settings:
                if not self._pm._mpv_manager.update_settings(profile_settings):
                    self._pm.logger.warning("Failed to apply some profile settings")

            # panscan>0 даёт «зум под размер экрана» и на части DRM/сборок ведёт к обрезке даже при 16:9.
            # По умолчанию вписываем кадр без обрезки; при необходимости убрать полосы — задать panscan в профиле MPV.
            if "panscan" not in profile_settings:
                self._pm._mpv_manager._send_command(
                    {"command": ["set_property", "panscan", 0.0]},
                    timeout=2.0,
                )

            # Manual playback loop is the most reliable way to enforce per-item durations on mpv builds
            # where ffconcat timing is inconsistent for images and mixed media.
            items = []
            missing = []
            # Enforce stable playback order (PlaylistFiles.order in DB).
            files = sorted((playlist.files or []), key=lambda x: int(getattr(x, "order", 0) or 0))
            for pf in files:
                resolved = self._pm._resolve_playlist_item_path(getattr(pf, "file_name", None))
                if not resolved or not resolved.get("path"):
                    missing.append(str(getattr(pf, "file_name", "")))
                    continue

                is_video = bool(resolved.get("is_video"))
                is_audio = bool(resolved.get("is_audio"))
                file_name = str(getattr(pf, "file_name", "") or "")
                items.append(
                    {
                        "key": resolved.get("key") or file_name,
                        "label": self._pm._media_label_for_file_name(file_name),
                        "path": resolved["path"],
                        "duration": int(getattr(pf, "duration", 0) or 0),
                        "is_video": is_video,
                        "is_audio": is_audio,
                        "muted": bool(getattr(pf, "muted", False))
                        if (is_video or is_audio)
                        else False,
                        "http_headers": resolved.get("http_headers") or {},
                        "page_url": resolved.get("page_url"),
                        "provider": resolved.get("provider"),
                    }
                )

            if not items:
                raise ValueError(
                    f"Playlist {playlist_id} has no existing media files"
                    + (f". Missing: {', '.join(missing[:10])}" if missing else "")
                    + (" ..." if len(missing) > 10 else "")
                )

            start_index = int(start_index or 0)
            if start_index < 0 or start_index >= len(items):
                start_index = 0

            playback_mode = self._pm._playlist_playback_mode(items)
            if not single_pass and playback_mode in ("local_single", "local_playlist"):
                return self._pm._play_local_video_engine(
                    playlist_id=playlist_id,
                    items=items,
                    start_index=start_index,
                    profile_muted=profile_muted,
                    profile_settings=profile_settings,
                    playlist=playlist,
                    mode=playback_mode,
                    source=source,
                    rule_id=rule_id,
                )

            self._pm._active_playlist_id = playlist_id
            self._pm._active_playback_mode = "manual"
            try:
                self._pm._mpv_manager.set_playback_session_active(True)
            except Exception:
                pass
            self._pm._set_loop_position(start_index, len(items))
            first = items[start_index]
            self._pm._set_current_media_label(self._pm._item_media_label(first))
            try:
                self._pm._logo_manager.ensure_mpv_video_output()
            except Exception:
                pass

            first_path = str(first.get("path") or "")
            first_is_network = first_path.startswith(("http://", "https://", "ytdl://"))
            if first_is_network:
                try:
                    self._pm._mpv_manager.set_playback_stream_opening(True)
                except Exception:
                    pass
            try:
                # Show first item immediately for responsiveness
                try:
                    # Do NOT loop the file at MPV level; the app controls looping.
                    self._pm._mpv_manager._send_command(
                        {"command": ["set_property", "loop-file", "no"]},
                        timeout=2.0,
                    )
                except Exception:
                    pass
                _, first_mpv_opts = self._pm._apply_mpv_http_headers(first, stream_url=first_path)
                self._pm._apply_mpv_ytdl_options(first, stream_url=first_path)
                first_load_cmd = self._pm._mpv_loadfile_command(
                    first_path,
                    "replace",
                    per_file_opts=first_mpv_opts,
                )
                first_load_timeout = (
                    self._pm._ytdl_loadfile_ipc_timeout_sec()
                    if first_path.startswith("ytdl://")
                    else (45.0 if first_is_network else 10.0)
                )
                first_media_key = str(first.get("key") or first_path)
                # Network/ytdl opens in two phases (ytdl_hook resolve, then lavf reapply). Unpausing
                # here made playback start before lavf headers merged — visible "double start".
                if first_is_network:
                    try:
                        self._pm._mpv_manager._send_command(
                            {"command": ["set_property", "pause", "yes"]},
                            timeout=3.0,
                        )
                    except Exception:
                        pass
                    first_load_resp = self._pm._issue_loadfile(
                        first_load_cmd,
                        media_key=first_media_key,
                        timeout=first_load_timeout,
                        max_attempts=1,
                    )
                    self._pm._preloaded_load_cmd = first_load_cmd
                else:
                    first_load_resp = None
                    loaded_local = False
                    for try_offset in range(len(items)):
                        try_index = (start_index + try_offset) % len(items)
                        candidate = items[try_index]
                        cand_path = str(candidate.get("path") or "")
                        if self._pm._is_network_stream_path(cand_path):
                            continue
                        _, cand_mpv_opts = self._pm._apply_mpv_http_headers(
                            candidate, stream_url=cand_path
                        )
                        cand_key = str(candidate.get("key") or cand_path)
                        cand_is_audio = bool(candidate.get("is_audio"))
                        if cand_is_audio:
                            self._pm._prepare_mpv_audio_before_loadfile()
                            try:
                                audio_opts = self._pm._logo_manager.prepare_audio_playback()
                            except Exception:
                                audio_opts = {"vid": "no", "keep-open": "no"}
                            merged_opts = dict(cand_mpv_opts or {})
                            merged_opts.update(audio_opts)
                            loaded_candidate = self._pm._safe_loadfile(
                                cand_path,
                                media_key=cand_key,
                                is_video=False,
                                is_audio=True,
                                per_file_opts=merged_opts,
                                timeout=10.0,
                                wait_vo=False,
                            )
                        else:
                            self._pm._prepare_mpv_audio_before_loadfile()
                            loaded_candidate = self._pm._safe_loadfile(
                                cand_path,
                                media_key=cand_key,
                                is_video=bool(candidate.get("is_video")),
                                per_file_opts=cand_mpv_opts,
                                timeout=10.0,
                            )
                        if loaded_candidate:
                            if try_index != start_index:
                                start_index = try_index
                                first = candidate
                                first_path = cand_path
                                first_media_key = cand_key
                                self._pm._set_current_media_label(self._pm._item_media_label(first))
                                self._pm._set_loop_position(start_index, len(items))
                            loaded_local = True
                            first_load_resp = {"error": "success"}
                            break
                    if not loaded_local:
                        raise RuntimeError("No playable local media at playlist start")
                    first_muted = self._pm._effective_playback_muted(
                        item_muted=bool(first.get("muted", False)),
                        profile_muted=profile_muted,
                    )
                    self._pm._apply_post_loadfile_playback_props(
                        muted=first_muted,
                        item_muted=bool(first.get("muted", False)),
                        profile_muted=profile_muted,
                    )
            except Exception:
                if first_is_network:
                    try:
                        self._pm._mpv_manager.set_playback_stream_opening(False)
                    except Exception:
                        pass
                raise

            self._pm._preloaded_stream_ready = False
            if first_is_network and bool(first.get("is_video")):
                self._pm.logger.info(
                    "Playback play: network loadfile issued (unpause deferred until stream ready)",
                    extra={
                        "playlist_id": playlist_id,
                        "media_key": str(first.get("key") or first_path),
                        "load_ok": bool(first_load_resp and first_load_resp.get("error") == "success"),
                        "path_preview": first_path[:120],
                        "deferred_unpause": True,
                    },
                )

            # Start background loop to enforce durations and EOF waits.
            # play() loadfile'd items[start_index]; loop walks from there, skips reload on first offset once.
            self._pm._play_thread = Thread(
                target=self._pm._run_manual_slideshow_loop,
                args=(playlist_id, items, start_index),
                kwargs={
                    "first_item_preloaded": True,
                    "profile_muted": profile_muted,
                    "single_pass": single_pass,
                },
                daemon=True,
            )
            self._pm._play_thread.start()

            # Persist only after the slideshow thread is running (avoids DB=playing + dead thread).
            self._pm._persist_playback_status(
                playlist_id=playlist_id,
                status="playing",
                source=source,
                rule_id=rule_id,
            )

            # Notify clients
            try:
                if self._pm.socketio:
                    self._pm.socketio.emit(
                        'playback_update',
                        {
                            'status': 'playing',
                            'playlist_id': playlist.id,
                            'current_media': self._pm._get_current_media_label(),
                            'playlist': {'id': playlist.id, 'name': playlist.name},
                            'settings': profile_settings,
                        },
                    )
            except Exception:
                # Best-effort: playback must continue even if sockets are unavailable.
                pass

            return True

        except Exception as e:
            self._pm.logger.error(
                "Playback error",
                extra={
                    'error': str(e),
                    'type': type(e).__name__,
                    'stack_trace': traceback.format_exc()
                }
            )
            try:
                self._pm.db_session.rollback()
            except Exception:
                pass

            # Best-effort: persist non-playing state so UI doesn't show green when we fell back to idle
            try:
                self._pm._persist_playback_status(
                    playlist_id=None,
                    status="idle",
                    source="idle",
                    clear_rule=True,
                )
            except Exception:
                try:
                    self._pm.db_session.rollback()
                except Exception:
                    pass

            self._pm._set_playback_active_marker(False)
            try:
                self._pm._mpv_manager.set_playback_session_active(False)
            except Exception:
                pass

            # Fall back to idle logo
            try:
                self._pm._logo_manager.ensure_mpv_video_output()
            except Exception:
                pass
            self._pm._logo_manager.display_idle_logo()
            raise RuntimeError(f"Failed to start playback: {str(e)}")
