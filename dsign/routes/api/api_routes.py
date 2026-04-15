from threading import Lock
import os
import shutil
import subprocess
import traceback
import time
from pathlib import Path 
from flask import jsonify, request, send_from_directory, abort, current_app, send_file
from flask_login import login_required, current_user, login_user, logout_user
from flask_wtf.csrf import validate_csrf
from werkzeug.utils import secure_filename
from dsign.models import PlaybackProfile, PlaylistProfileAssignment, Playlist, User, db
from dsign.config.mpv_settings_schema import MPV_SETTINGS_SCHEMA
from PIL import Image
from dsign.config.config import THUMBNAIL_FOLDER, THUMBNAIL_URL
import re
# Import service classes directly from their modules (dsign.services no longer re-exports them).

thumbnail_lock = Lock()

def init_api_routes(api_bp, services):
    settings_service = services.get('settings_service')
    playback_service = services.get('playback_service')
    playlist_service = services.get('playlist_service')
    file_service = services.get('file_service')
    thumbnail_service = services.get('thumbnail_service')
    socketio = services.get('socketio')
    UPLOAD_LOGO_NAME = "idle_logo.jpg"
    MAX_LOGO_SIZE = 5 * 1024 * 1024  # 5MB
    # Throttle expensive MPV screenshot capture requests (software fallback is CPU-heavy on Pi 3B+).
    screenshot_capture_lock = Lock()
    last_screenshot_capture_ts: float = 0.0
    screenshot_min_interval_sec: float = 10.0

    # ======================
    # System status / audio (dashboard)
    # ======================
    def _read_cpu_temp_c() -> float | None:
        # Raspberry Pi / Linux common path
        for p in (
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/devices/virtual/thermal/thermal_zone0/temp",
        ):
            try:
                if os.path.exists(p):
                    raw = Path(p).read_text(encoding="utf-8").strip()
                    v = float(raw)
                    return round(v / 1000.0, 1) if v > 1000 else round(v, 1)
            except Exception:
                continue
        return None

    def _read_cpu_load_percent() -> float | None:
        try:
            load1, _, _ = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            return round(min(100.0, (load1 / cpu_count) * 100.0), 1)
        except Exception:
            return None

    _procstat_last: dict = {"total": None, "idle": None, "ts": None}

    def _read_cpu_percent_procstat() -> float | None:
        """
        "Real" CPU% from /proc/stat delta.
        Returns percent usage since last call (needs 2 samples).
        """
        try:
            p = "/proc/stat"
            if not os.path.exists(p):
                return None
            first = Path(p).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            # cpu  user nice system idle iowait irq softirq steal guest guest_nice
            parts = first.split()
            if len(parts) < 5 or parts[0] != "cpu":
                return None
            nums = [int(x) for x in parts[1:]]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)

            last_total = _procstat_last["total"]
            last_idle = _procstat_last["idle"]
            _procstat_last["total"] = total
            _procstat_last["idle"] = idle
            _procstat_last["ts"] = time.time()

            if last_total is None or last_idle is None:
                return None
            dt_total = total - last_total
            dt_idle = idle - last_idle
            if dt_total <= 0:
                return None
            usage = (dt_total - dt_idle) / dt_total * 100.0
            return round(max(0.0, min(100.0, usage)), 1)
        except Exception:
            return None

    def _disk_usage(path: str) -> dict | None:
        try:
            usage = shutil.disk_usage(path)
            used = usage.used
            total = usage.total
            free = usage.free
            pct = round((used / total) * 100.0, 1) if total else None
            return {"path": path, "total": total, "used": used, "free": free, "used_percent": pct}
        except Exception:
            return None

    def _amixer_available() -> bool:
        try:
            subprocess.run(["amixer", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return True
        except Exception:
            return False

    def _amixer_pick_control() -> tuple[int, str] | None:
        """
        Pick a reasonable ALSA simple control for volume/mute.
        Different images/cards expose different names (e.g. Master, PCM, Headphone).
        """
        if not _amixer_available():
            return None
        try:
            # Try default card first, then a few numeric cards.
            preferred = ["Master", "PCM", "Headphone", "Speaker", "Line Out", "Line", "Digital", "HDMI"]
            candidates: list[tuple[int, list[str]]] = []

            def read_names(args: list[str]) -> list[str]:
                out = subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)
                return re.findall(r"Simple mixer control '([^']+)'", out)

            # Default card (no -c) is sometimes HDMI and can have no simple controls.
            try:
                names0 = read_names(["amixer", "scontrols"])
                if names0:
                    candidates.append((-1, names0))
            except Exception:
                pass

            for card in range(0, 6):
                try:
                    names = read_names(["amixer", "-c", str(card), "scontrols"])
                    if names:
                        candidates.append((card, names))
                except Exception:
                    continue

            if not candidates:
                return None

            # Prefer cards with preferred control names.
            for p in preferred:
                for card, names in candidates:
                    if p in names:
                        return (card if card >= 0 else 0, p) if card == -1 else (card, p)

            # Fall back to first control on first candidate card.
            card, names = candidates[0]
            return (card if card >= 0 else 0, names[0]) if names else None
        except Exception:
            return None

    def _audio_get() -> dict:
        """
        Best-effort global audio status via ALSA amixer.
        Returns: { available, volume_percent, muted }
        """
        if not _amixer_available():
            return {"available": False, "volume_percent": None, "muted": None}
        try:
            picked = _amixer_pick_control()
            if not picked:
                return {"available": False, "volume_percent": None, "muted": None}
            card, ctl = picked
            out = subprocess.check_output(["amixer", "-c", str(card), "sget", ctl], text=True, stderr=subprocess.STDOUT)
            # Parse first "[NN%]" and last "[on]/[off]"
            m_vol = re.search(r"\[(\d{1,3})%\]", out)
            m_mute = re.findall(r"\[(on|off)\]", out)
            vol = int(m_vol.group(1)) if m_vol else None
            muted = (m_mute[-1] == "off") if m_mute else None
            return {"available": True, "volume_percent": vol, "muted": muted}
        except Exception:
            return {"available": True, "volume_percent": None, "muted": None}

    def _audio_set(volume_percent: int | None = None, muted: bool | None = None) -> dict:
        if not _amixer_available():
            return {"available": False, "volume_percent": None, "muted": None}
        picked = _amixer_pick_control()
        if not picked:
            return {"available": False, "volume_percent": None, "muted": None}
        card, ctl = picked
        if volume_percent is not None:
            volume_percent = int(max(0, min(100, volume_percent)))
            subprocess.run(["amixer", "-c", str(card), "sset", ctl, f"{volume_percent}%"], check=False)
        if muted is not None:
            subprocess.run(["amixer", "-c", str(card), "sset", ctl, "mute" if muted else "unmute"], check=False)
        return _audio_get()

    @api_bp.route('/system/status', methods=['GET'])
    @login_required
    def get_system_status():
        try:
            upload_folder = current_app.config.get("UPLOAD_FOLDER", "/var/lib/dsign/media")
            return jsonify(
                {
                    "success": True,
                    "status": {
                        "storage": {
                            "root": _disk_usage("/"),
                            "media": _disk_usage(upload_folder),
                        },
                        "cpu": {
                            "temp_c": _read_cpu_temp_c(),
                            # Prefer procstat CPU% (delta) when available; fall back to loadavg-based estimate.
                            "usage_percent": _read_cpu_percent_procstat(),
                            "load_percent": _read_cpu_load_percent(),
                        },
                        "audio": _audio_get(),
                    },
                }
            )
        except Exception as e:
            current_app.logger.error(f"Error getting system status: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/system/audio', methods=['POST'])
    @login_required
    def set_system_audio():
        try:
            data = request.get_json(silent=True) or {}
            vol = data.get("volume_percent")
            muted = data.get("muted")
            vol_i = int(vol) if vol is not None else None
            muted_b = bool(muted) if muted is not None else None
            state = _audio_set(volume_percent=vol_i, muted=muted_b)
            return jsonify({"success": True, "audio": state})
        except Exception as e:
            current_app.logger.error(f"Error setting system audio: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/media/idle_logo_rotation', methods=['GET'])
    @login_required
    def get_idle_logo_rotation():
        try:
            cur = settings_service.load_settings() if settings_service else {}
            display = cur.get("display") if isinstance(cur.get("display"), dict) else {}
            rotate = int(display.get("idle_logo_rotate", 0) or 0)
            if rotate not in (0, 90, 180, 270):
                rotate = 0
            return jsonify({"success": True, "rotate": rotate})
        except Exception as e:
            current_app.logger.error(f"Error getting idle logo rotation: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/media/idle_logo_rotation', methods=['POST'])
    @login_required
    def set_idle_logo_rotation():
        try:
            data = request.get_json(silent=True) or {}
            rotate = int(data.get("rotate", 0) or 0)
            if rotate not in (0, 90, 180, 270):
                return jsonify({"success": False, "error": "Invalid rotation"}), 400

            if settings_service:
                cur = settings_service.load_settings()
                display = cur.get("display") if isinstance(cur.get("display"), dict) else {}
                display["idle_logo_rotate"] = rotate
                cur["display"] = display
                settings_service.save_settings(cur)

            # Apply immediately (best-effort) by reloading idle logo and setting rotation.
            try:
                playback_service.restart_idle_logo(rotate=rotate)
            except Exception:
                pass

            return jsonify({"success": True, "rotate": rotate})
        except Exception as e:
            current_app.logger.error(f"Error setting idle logo rotation: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    # ======================
    # MPV Settings (/api/settings)
    # ======================
    @api_bp.route('/settings/schema', methods=['GET'])
    @login_required
    def get_settings_schema():
        try:
            return jsonify({
                'success': True,
                'schema': MPV_SETTINGS_SCHEMA
            })
        except Exception as e:
            current_app.logger.error(f"Error getting settings schema: {str(e)}")
            return jsonify({
                'success': False,
                'error': 'Failed to load settings schema'
            }), 500

    @api_bp.route('/settings/current', methods=['GET'])
    @login_required
    def get_current_settings():
        try:
            settings = settings_service.get_current_settings()
            profile = None
            
            # Get current profile if available
            if settings.get('profile_id'):
                profile = db.session.query(PlaybackProfile).get(settings['profile_id'])
            
            return jsonify({
                'success': True,
                'settings': settings,
                'profile': {
                    'id': profile.id if profile else None,
                    'name': profile.name if profile else None,
                    'type': profile.profile_type if profile else None
                }
            })
        except Exception as e:
            current_app.logger.error(f"Error getting current settings: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/settings/update', methods=['POST'])
    @login_required
    def update_settings():
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': 'No data provided'
                }), 400

            # Validate and update settings
            success = settings_service.update_mpv_settings(
                data,
                profile_type=data.get('profile_type'),
                playlist_id=data.get('playlist_id')
            )

            if success:
                return jsonify({'success': True})
            else:
                return jsonify({
                    'success': False,
                    'error': 'Failed to update settings'
                }), 500

        except Exception as e:
            current_app.logger.error(f"Error updating settings: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/settings/mpv/global', methods=['POST'])
    @login_required
    def save_global_mpv():
        """
        Persist advanced MPV options in settings.json (mpv) and apply to the running player.
        """
        try:
            data = request.get_json(silent=True) or {}
            mpv_mgr = getattr(playback_service, '_mpv_manager', None) if playback_service else None
            ok = settings_service.save_global_mpv_and_apply(data, mpv_mgr)
            if ok:
                return jsonify({'success': True})
            return jsonify({'success': False, 'error': 'Failed to save MPV settings'}), 500
        except Exception as e:
            current_app.logger.error(f"Error saving global MPV settings: {str(e)}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @api_bp.route('/settings/display/apply', methods=['POST'])
    @login_required
    def apply_display_mode():
        """
        Apply HDMI output preset by calling /usr/local/bin/dsign-display-apply (sudo) and rebooting.
        Requires sudoers rule allowing the helper script (and optionally reboot).
        """
        try:
            if not (getattr(current_user, "is_admin", False) or current_user.username == "admin"):
                return jsonify({"success": False, "error": "Unauthorized"}), 403

            data = request.get_json(silent=True) or {}
            preset = data.get("preset", "auto")
            reboot = bool(data.get("reboot", True))
            if preset not in {"auto", "1080p60", "4k30"}:
                return jsonify({"success": False, "error": "Invalid preset"}), 400

            # Persist selection in settings.json (so UI shows it even before reboot)
            settings_service.set_display_mode_preset(preset)

            helper = "/usr/local/bin/dsign-display-apply"
            cmd = ["sudo", helper, preset]
            if not reboot:
                cmd.append("--no-reboot")

            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except FileNotFoundError:
                return jsonify({
                    "success": False,
                    "error": f"Helper script not found: {helper}"
                }), 500
            except subprocess.CalledProcessError as e:
                # sudo missing permissions / helper error
                msg = (e.stderr or e.stdout or "").strip() or f"Command failed: {e.returncode}"
                return jsonify({
                    "success": False,
                    "error": msg
                }), 403

            current_app.logger.info(
                "Display mode preset apply requested",
                extra={"operation": "DisplayModeApply", "preset": preset, "reboot": reboot}
            )

            return jsonify({"success": True, "preset": preset, "reboot": reboot})
        except Exception as e:
            current_app.logger.error(f"Error applying display mode: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/settings/preview/auto', methods=['POST'])
    @login_required
    def set_auto_preview_timer():
        """
        Configure screenshot.timer interval:
        - Off
        - 5 / 10 / 15 minutes
        Applies via helper script (sudo) and stores preference in settings.json.
        """
        try:
            if not (getattr(current_user, "is_admin", False) or current_user.username == "admin"):
                return jsonify({"success": False, "error": "Unauthorized"}), 403

            data = request.get_json(silent=True) or {}
            interval_sec = int(data.get("interval_sec", 0) or 0)
            if interval_sec not in {0, 300, 600, 900}:
                return jsonify({"success": False, "error": "Invalid interval"}), 400

            # Persist to settings.json so UI reflects desired state.
            settings_service.set_preview_auto_interval_sec(interval_sec)

            helper = "/usr/local/bin/dsign-preview-timer"
            cmd = ["sudo", helper, "off"] if interval_sec == 0 else ["sudo", helper, "set", str(interval_sec)]
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            except FileNotFoundError:
                return jsonify({"success": False, "error": f"Helper script not found: {helper}"}), 500
            except subprocess.CalledProcessError as e:
                msg = (e.stderr or e.stdout or "").strip() or f"Command failed: {e.returncode}"
                return jsonify({"success": False, "error": msg}), 403

            return jsonify({"success": True, "interval_sec": interval_sec})
        except Exception as e:
            current_app.logger.error(f"Error updating preview timer: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/settings/transcode/apply', methods=['POST'])
    @login_required
    def apply_transcode_settings():
        """Enable/disable upload-time transcoding and persist settings.json."""
        try:
            if not (getattr(current_user, "is_admin", False) or current_user.username == "admin"):
                return jsonify({"success": False, "error": "Unauthorized"}), 403

            data = request.get_json(silent=True) or {}
            enabled = bool(data.get("enabled", False))
            resolution = str(data.get("resolution", "1920x1080"))
            fps = int(data.get("fps", 25))
            settings_service.set_transcode_settings(enabled=enabled, resolution=resolution, fps=fps)
            return jsonify({"success": True, "enabled": enabled, "resolution": resolution, "fps": fps})
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except Exception as e:
            current_app.logger.error(f"Error applying transcode settings: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    # ======================
    # Playback Profiles (/api/profiles)
    # ======================
    @api_bp.route('/profiles', methods=['GET'])
    @login_required
    def get_profiles():
        try:
            profiles = db.session.query(PlaybackProfile).all()
            return jsonify({
                'success': True,
                'profiles': [{
                    'id': p.id,
                    'name': p.name,
                    'type': p.profile_type,
                    'settings': p.settings
                } for p in profiles]
            })
        except Exception as e:
            current_app.logger.error(f"Error getting profiles: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles', methods=['POST'])
    @login_required
    def create_profile():
        try:
            data = request.get_json()
            if not data or 'name' not in data or 'type' not in data:
                return jsonify({
                    'success': False,
                    'error': 'Missing required fields'
                }), 400

            # Validate profile type
            if data['type'] not in ['idle', 'playlist']:
                return jsonify({
                    'success': False,
                    'error': 'Invalid profile type'
                }), 400

            # Create new profile
            profile = PlaybackProfile(
                name=data['name'],
                profile_type=data['type'],
                settings=data.get('settings', {})
            )
            db.session.add(profile)
            db.session.commit()

            return jsonify({
                'success': True,
                'profile_id': profile.id
            }), 201

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/<int:profile_id>', methods=['DELETE'])
    @login_required
    def delete_profile(profile_id):
        try:
            profile = db.session.query(PlaybackProfile).get(profile_id)
            if not profile:
                return jsonify({
                    'success': False,
                    'error': 'Profile not found'
                }), 404

            # Remove any assignments first
            db.session.query(PlaylistProfileAssignment).filter_by(
                profile_id=profile_id
            ).delete()

            db.session.delete(profile)
            db.session.commit()

            return jsonify({'success': True})

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error deleting profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/assign', methods=['POST'])
    @login_required
    def assign_profile():
        try:
            data = request.get_json()
            if not data or 'playlist_id' not in data:
                return jsonify({
                    'success': False,
                    'error': 'Missing playlist_id'
                }), 400

            # Remove existing assignment if exists
            db.session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=data['playlist_id']
            ).delete()

            # Add new assignment if profile_id provided
            if data.get('profile_id'):
                # Verify profile exists
                profile = db.session.query(PlaybackProfile).get(data['profile_id'])
                if not profile or profile.profile_type != 'playlist':
                    return jsonify({
                        'success': False,
                        'error': 'Invalid playlist profile'
                    }), 400

                assignment = PlaylistProfileAssignment(
                    playlist_id=data['playlist_id'],
                    profile_id=data['profile_id']
                )
                db.session.add(assignment)

            db.session.commit()

            # Notify clients of the change
            if socketio:
                socketio.emit('profile_assignment_changed', {
                    'playlist_id': data['playlist_id'],
                    'profile_id': data.get('profile_id')
                })

            return jsonify({'success': True})

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error assigning profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/apply/<int:profile_id>', methods=['POST'])
    @login_required
    def apply_profile(profile_id):
        try:
            profile = db.session.query(PlaybackProfile).get(profile_id)
            if not profile:
                return jsonify({
                    'success': False,
                    'error': 'Profile not found'
                }), 404

            # Apply profile settings
            settings_service.apply_profile(profile.settings)

            # Update current profile reference
            settings_service.update_current_profile(profile_id)

            return jsonify({
                'success': True,
                'profile_id': profile_id
            })

        except Exception as e:
            current_app.logger.error(f"Error applying profile: {str(e)}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @api_bp.route('/profiles/assignments', methods=['GET'])
    @login_required
    def get_profile_assignments():
        try:
            assignments = db.session.query(PlaylistProfileAssignment).all()
            return jsonify({
                "success": True,
                "assignments": {a.playlist_id: a.profile_id for a in assignments}
            })
        except Exception as e:
            current_app.logger.error(f"Error getting assignments: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Failed to load assignments"
            }), 500

    # ======================
    # Playlist overrides (simplified UI over existing profiles/assignments)
    # ======================
    @api_bp.route('/playlists/overrides', methods=['GET'])
    @login_required
    def get_playlist_overrides():
        try:
            from dsign.models import Playlist, PlaylistProfileAssignment, PlaybackProfile

            playlists = db.session.query(Playlist).all()
            assignments = {
                a.playlist_id: a.profile_id
                for a in db.session.query(PlaylistProfileAssignment).all()
                if a.playlist_id and a.profile_id
            }
            profiles = {
                p.id: p
                for p in db.session.query(PlaybackProfile).filter_by(profile_type="playlist").all()
            }

            rows = []
            for pl in playlists:
                pid = assignments.get(pl.id)
                prof = profiles.get(pid) if pid else None
                settings = (prof.settings or {}) if prof else {}
                rows.append(
                    {
                        "playlist_id": pl.id,
                        "playlist_name": pl.name,
                        "has_overrides": bool(prof),
                        "overrides": {
                            "video_rotate": settings.get("video-rotate", 0),
                            "panscan": settings.get("panscan", 0.0),
                            "mute": bool(settings.get("mute", False)),
                            "dwidth": settings.get("dwidth"),
                            "dheight": settings.get("dheight"),
                        },
                    }
                )

            return jsonify({"success": True, "playlists": rows})
        except Exception as e:
            current_app.logger.error(f"Error getting playlist overrides: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/playlists/overrides', methods=['POST'])
    @login_required
    def set_playlist_overrides():
        """
        Create/update a hidden playlist profile and assignment for a playlist.
        This keeps DB model stable while simplifying UI.
        """
        try:
            from dsign.models import PlaylistProfileAssignment, PlaybackProfile

            data = request.get_json(silent=True) or {}
            playlist_id = int(data.get("playlist_id"))
            enabled = bool(data.get("enabled", False))

            # If overrides disabled -> remove assignment (keep profile record for now)
            assignment = db.session.query(PlaylistProfileAssignment).filter_by(playlist_id=playlist_id).first()
            if not enabled:
                if assignment:
                    db.session.delete(assignment)
                    db.session.commit()
                return jsonify({"success": True, "disabled": True})

            rotate = int(data.get("video_rotate", 0))
            if rotate not in (0, 90, 180, 270):
                return jsonify({"success": False, "error": "Invalid rotation"}), 400

            panscan = float(data.get("panscan", 0.0))
            if panscan < 0.0 or panscan > 1.0:
                return jsonify({"success": False, "error": "Invalid panscan"}), 400

            mute = bool(data.get("mute", False))
            dwidth = data.get("dwidth")
            dheight = data.get("dheight")
            if dwidth is not None:
                dwidth = int(dwidth)
                if dwidth <= 0 or dwidth > 7680:
                    return jsonify({"success": False, "error": "Invalid dwidth"}), 400
            if dheight is not None:
                dheight = int(dheight)
                if dheight <= 0 or dheight > 4320:
                    return jsonify({"success": False, "error": "Invalid dheight"}), 400
            if (dwidth is None) != (dheight is None):
                return jsonify({"success": False, "error": "dwidth/dheight must be set together"}), 400

            # Use a deterministic hidden profile name so we can find/update it.
            prof_name = f"_dsign_playlist_{playlist_id}"
            profile = db.session.query(PlaybackProfile).filter_by(profile_type="playlist", name=prof_name).first()
            if not profile:
                profile = PlaybackProfile(name=prof_name, profile_type="playlist", settings={})
                db.session.add(profile)
                db.session.flush()

            profile.settings = {
                "video-rotate": rotate,
                "panscan": panscan,
                "mute": mute,
                **({"dwidth": dwidth, "dheight": dheight} if dwidth is not None and dheight is not None else {}),
            }
            db.session.add(profile)

            if not assignment:
                assignment = PlaylistProfileAssignment(playlist_id=playlist_id, profile_id=profile.id)
            assignment.profile_id = profile.id
            db.session.add(assignment)
            db.session.commit()

            return jsonify({"success": True, "profile_id": profile.id})

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error setting playlist overrides: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    # ======================
    # Playback Control (/api/playback)
    # ======================
    @api_bp.route('/playback/play', methods=['POST'])
    @login_required
    def playback_play():
        try:
            data = request.get_json()
            if not data or 'playlist_id' not in data:
                return jsonify({
                    "success": False,
                    "error": "Missing playlist_id"
                }), 400

            # Get additional settings from request if provided
            additional_settings = data.get('settings', {})
        
            # Start playback through service
            result = playback_service.play(
                playlist_id=data['playlist_id'],
                settings=additional_settings
            )
        
            return jsonify({
                "success": True,
                "playlist_id": data['playlist_id'],
                "details": result
            })
        except Exception as e:
            current_app.logger.error(f"Error starting playback: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playback/stop', methods=['POST'])
    @login_required
    def playback_stop():
        try:
            result = playback_service.stop()
            return jsonify({
                "success": True,
                "details": result
            })
        except Exception as e:
            current_app.logger.error(f"Error stopping playback: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playback/status', methods=['GET'])
    @login_required
    def playback_status():
        try:
            status = playback_service.get_status()
            status_dict = status if isinstance(status, dict) else vars(status)
            return jsonify({
                "success": True,
                "status": status_dict
            })
        except Exception as e:
            current_app.logger.error(f"Error getting playback status: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    # ======================
    # Playlist Management (/api/playlists)
    # ======================
    @api_bp.route('/playlists', methods=['GET'])
    @login_required
    def get_playlists():
        try:
            playlists = db.session.query(Playlist).all()
            assignments = {a.playlist_id: a.profile_id 
                          for a in db.session.query(PlaylistProfileAssignment).all()}
        
            return jsonify({
                "success": True,
                "playlists": [{
                    **p.to_dict(),
                    "profile_id": assignments.get(p.id)
                } for p in playlists]
            })
        except Exception as e:
            current_app.logger.error(f"Error getting playlists: {str(e)}")
            return jsonify({
                "success": False,
                 "error": "Failed to load playlists"
            }), 500

    @api_bp.route('/playlists', methods=['POST'])
    @login_required
    def create_playlist():
        try:
            data = request.get_json()
            if not data or 'name' not in data:
                return jsonify({
                    "success": False,
                    "error": "Missing required fields"
                }), 400

            result = playlist_service.create_playlist(data)
            if socketio:
                socketio.emit('playlist_created', {
                    'playlist_id': result['playlist_id'],
                    'name': data['name']
                })
            return jsonify({
                "success": True,
                "playlist_id": result["playlist_id"]
            }), 201
        except Exception as e:
            current_app.logger.error(f"Error creating playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['GET'])
    @login_required
    def get_playlist(playlist_id):
        try:
            playlist = playlist_service.get_playlist(playlist_id)
            if not playlist:
                return jsonify({
                    "success": False,
                    "error": "Playlist not found"
                }), 404
            
            return jsonify({
                "success": True,
                "playlist": playlist
            })
        except Exception as e:
            current_app.logger.error(f"Error getting playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['PUT'])
    @login_required
    def update_playlist(playlist_id):
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    "success": False,
                    "error": "No data provided"
                }), 400

            result = playlist_service.update_playlist(playlist_id, data)
            
            # Логирование успешного обновления
            if result.get('success'):
                current_app.logger.info(f"Playlist {playlist_id} metadata updated successfully")
                if 'name' in data:
                    current_app.logger.debug(f"M3U file regenerated for playlist {playlist_id}")
            
            return jsonify(result)
        except Exception as e:
            current_app.logger.error(f"Error updating playlist {playlist_id}: {str(e)}", exc_info=True)
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>', methods=['DELETE'])
    @login_required
    def delete_playlist(playlist_id):
        try:
            # Удаляем привязки к профилям
            db.session.query(PlaylistProfileAssignment).filter_by(
                playlist_id=playlist_id
            ).delete()

            result = playlist_service.delete_playlist(playlist_id)

            if socketio:
                socketio.emit('playlist_deleted', {'playlist_id': playlist_id})
                
            return jsonify(result)
        except Exception as e:
            current_app.logger.error(f"Error deleting playlist {playlist_id}: {str(e)}", exc_info=True)
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/active', methods=['GET'])
    @login_required
    def active_playlist():
        try:
            active_playlist = playlist_service.get_active_playlist()
            return jsonify({
                "success": True,
                "active": bool(active_playlist),
                "playlist": active_playlist
            })
        except Exception as e:
            current_app.logger.error(f"Error getting active playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/playlists/<int:playlist_id>/reorder', methods=['POST'])
    @login_required
    def reorder_playlist_items(playlist_id):
        try:
            data = request.get_json()
            if not data or 'item_id' not in data or 'position' not in data:
                return jsonify({
                    "success": False,
                    "error": "Invalid data"
                }), 400

            success = playlist_service.reorder_single_item(
                playlist_id,
                data['item_id'],
                data['position']
            )
            return jsonify({"success": success})
        except Exception as e:
            current_app.logger.error(f"Error reordering playlist: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
    
    @api_bp.route('/playlists/<int:playlist_id>/files', methods=['POST'])
    @login_required
    def update_playlist_files(playlist_id):
        try:
            data = request.get_json()
            if not data or 'files' not in data:
                return jsonify({"success": False, "error": "Missing files data"}), 400

            current_app.logger.debug(f"Updating files for playlist {playlist_id} with {len(data.get('files', []))} files")

            result = playlist_service.update_playlist_files(playlist_id, data.get('files', []))
            
            if not result.get('success'):
                current_app.logger.error(f"Playlist files update failed: {result.get('error')}")
                return jsonify(result), 400

            current_app.logger.info(f"Playlist {playlist_id} files updated successfully")
            current_app.logger.debug(f"M3U file regenerated for playlist {playlist_id}")
            
            return jsonify(result)
        
        except Exception as e:
            current_app.logger.error(f"API error updating playlist files {playlist_id}: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": "Internal server error"}), 500
    
    # ======================
    # Media File Handling (/api/media)
    # ======================
    @api_bp.route('/media/files', methods=['GET'])
    @login_required
    def get_media_files():
        try:
            playlist_id = request.args.get('playlist_id')
            files = file_service.get_media_files_with_playlist_info(
                playlist_id=playlist_id,
                db_session=db.session
            )
        
            return jsonify({
                "success": True,
                "files": files,
                "count": len(files)
            })
        except Exception as e:
            current_app.logger.error(f"Error getting media files: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500


    @api_bp.route('/media/files', methods=['POST'])
    @login_required
    def delete_media_files():
        try:
            data = request.get_json()
            if not data or 'files' not in data or not isinstance(data['files'], list):
                return jsonify({
                    "success": False,
                    "error": "Invalid request format. Expected {'files': [...]}"
                }), 400

            files_to_delete = [secure_filename(f) for f in data['files'] if f]
            if not files_to_delete:
                return jsonify({
                    "success": False,
                    "error": "No valid filenames provided"
                }), 400

            result = file_service.delete_files(files_to_delete)
            return jsonify({
                "success": True,
                "deleted": result
            })
        except Exception as e:
            current_app.logger.error(f"Error deleting media files: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/media/upload', methods=['POST'])
    @login_required
    def upload_media():
        try:
            if 'files' not in request.files:
                return jsonify({
                    "success": False,
                    "error": "No files provided"
                }), 400

            # Use persisted settings.json toggle (default OFF) for transcoding.
            try:
                cur = settings_service.load_settings() if settings_service else {}
                display = cur.get("display") if isinstance(cur.get("display"), dict) else {}
                transcode_cfg = {
                    "enabled": bool(display.get("auto_transcode_videos", False)),
                    "resolution": str(display.get("transcode_target_resolution", "1920x1080")),
                    "fps": int(display.get("transcode_target_fps", 25) or 25),
                }
            except Exception:
                transcode_cfg = {"enabled": False, "resolution": "1920x1080", "fps": 25}

            files = request.files.getlist('files')
            # Backward-compatible call: older deployments may not accept `transcode=` yet.
            try:
                import inspect
                params = inspect.signature(getattr(file_service, "handle_upload")).parameters
                if "transcode" in params:
                    saved_files = file_service.handle_upload(files, transcode=transcode_cfg)
                else:
                    saved_files = file_service.handle_upload(files)
            except Exception:
                # Fall back to legacy call shape
                saved_files = file_service.handle_upload(files)
            # Return initial per-file transcode status so the UI can show "Queued…" immediately
            # without requiring a page refresh.
            try:
                # Only include non-empty statuses (videos that are queued/running/failed/completed).
                transcode_status = {}
                for fn in (saved_files or []):
                    st = file_service.get_transcode_status(filename=fn)
                    if st:
                        transcode_status[fn] = st
            except Exception:
                transcode_status = {}
            return jsonify({
                "success": True,
                "files": saved_files,
                "transcode_status": transcode_status,
                "transcode_enabled": bool(transcode_cfg.get("enabled")),
            })
        except Exception as e:
            current_app.logger.error(f"Error uploading media: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/media/transcode/status', methods=['GET'])
    @login_required
    def get_transcode_status():
        """Return background transcode progress (percent + ETA)."""
        try:
            filename = request.args.get("filename")
            status = file_service.get_transcode_status(filename=filename)
            return jsonify({"success": True, "status": status})
        except Exception as e:
            current_app.logger.error(f"Error getting transcode status: {str(e)}")
            return jsonify({"success": False, "error": str(e)}), 500

    @api_bp.route('/media/upload_logo', methods=['POST'])
    @login_required
    def upload_logo():
        try:
            if 'logo' not in request.files:
                return jsonify({"success": False, "error": "No file provided"}), 400

            file = request.files['logo']
            if not file.filename:
                return jsonify({"success": False, "error": "Empty filename"}), 400

            # Проверка размера и формата файла
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
        
            if file_size > current_app.config['MAX_LOGO_SIZE']:
                return jsonify({
                    "success": False,
                    "error": f"File too large (max {current_app.config['MAX_LOGO_SIZE']//1024//1024}MB)"
                }), 400

            try:
                img = Image.open(file.stream)
                img.verify()
                file.stream.seek(0)
                if img.format.lower() not in ('jpeg', 'jpg', 'png'):
                    raise ValueError("Unsupported format")
            except Exception:
                return jsonify({
                    "success": False,
                    "error": "Invalid image file (only JPEG/PNG allowed)"
                }), 400

            # Сохранение файла
            filename = secure_filename(current_app.config['IDLE_LOGO'])
            upload_folder = current_app.config['UPLOAD_FOLDER']
            file_path = os.path.join(upload_folder, filename)
        
            # Создаем backup
            backup_path = None
            if os.path.exists(file_path):
                backup_path = f"{file_path}.bak"
                os.rename(file_path, backup_path)

            try:
                file.save(file_path)
                os.chmod(file_path, 0o644)

                # Обновляем логотип в плеере
                if not playback_service.restart_idle_logo(upload_folder, filename):
                    raise RuntimeError("Failed to update player")

                # Успешное завершение
                if backup_path and os.path.exists(backup_path):
                    os.unlink(backup_path)

                return jsonify({
                    "success": True,
                    "message": "Logo updated successfully",
                    "timestamp": int(time.time())
                })

            except Exception as e:
                # Восстановление из backup
                if backup_path and os.path.exists(backup_path):
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    os.rename(backup_path, file_path)
                    playback_service.restart_idle_logo(upload_folder, filename)

                current_app.logger.error(f"Logo upload failed: {str(e)}")
                return jsonify({
                    "success": False,
                    "error": str(e),
                    "recovered": backup_path is not None
                }), 500

        except Exception as e:
            current_app.logger.error(f"Unexpected error: {str(e)}")
            return jsonify({
                "success": False,
                "error": "Internal server error"
            }), 500

    @api_bp.route('/media/logo_status', methods=['GET'])
    @login_required
    def get_logo_status():
        try:
            logo_path = playback_service.get_current_logo_path()
            return jsonify({
                "success": True,
                "path": str(logo_path),
                "is_default": "placeholder.jpg" in str(logo_path),
                "exists": True,
                "filename": os.path.basename(logo_path)
            })
        except FileNotFoundError:
            return jsonify({
                "success": False,
                "error": "No logo files available",
                "exists": False
            }), 404
        except Exception as e:
            current_app.logger.error(f"Error getting logo status: {str(e)}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500

    @api_bp.route('/media/<path:filename>', methods=['GET'])
    @login_required
    def serve_media(filename):
        try:
            upload_folder = current_app.config.get('UPLOAD_FOLDER', '/var/lib/dsign/media')
            file_path = os.path.join(upload_folder, filename)
        
            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                abort(404)
            
            return send_from_directory(
                upload_folder,
                filename,
                mimetype=None,
                as_attachment=False,
                conditional=True
            )
        except Exception as e:
            current_app.logger.error(f"Error serving media file: {str(e)}")
            abort(404)
                
    @api_bp.route('/media/mpv_screenshot', methods=['GET'])
    @login_required
    def get_mpv_screenshot():
        try:
            screenshot_path = os.path.join(current_app.config['STATIC_FOLDER'], 'images', 'on_air_screen.jpg')
            default_path = os.path.join(current_app.config['STATIC_FOLDER'], 'images', 'default-preview.jpg')
        
            # Hot path: this endpoint is polled by the UI.
            # Avoid expensive PIL open/verify here; do validation in the capture endpoint instead.
            if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 1024:
                return send_file(
                    screenshot_path,
                    mimetype='image/jpeg',
                    conditional=True,
                    max_age=0
                )
        
            return send_file(
                default_path,
                mimetype='image/jpeg',
                conditional=True,
                max_age=0
            )
        
        except Exception as e:
            current_app.logger.error(f"Screenshot error: {str(e)}")
            abort(500)
            
    @api_bp.route('/media/mpv_screenshot/capture', methods=['POST'])
    @login_required
    def force_screenshot_update():
        """Запуск systemd-сервиса для обновления скриншота"""
        try:
            # If Auto preview is Off, only allow explicit manual capture requests.
            # This prevents any background/implicit polling from starting screenshot.service.
            try:
                current_settings = settings_service.get_current_settings() if settings_service else {}
                display = current_settings.get("display") if isinstance(current_settings.get("display"), dict) else {}
                auto_interval = int(display.get("preview_auto_interval_sec") or 0)
            except Exception:
                auto_interval = 0

            intent = (request.headers.get("X-DSIGN-Preview-Intent") or "").strip().lower()
            is_manual = intent == "manual"
            if auto_interval <= 0 and not is_manual:
                return jsonify(
                    {
                        "success": True,
                        "skipped": True,
                        "reason": "auto_preview_off",
                        "retry_in_sec": 0,
                    }
                )

            # Audit: this endpoint is the only place that starts screenshot.service.
            # If the service triggers unexpectedly, this log helps identify the caller.
            try:
                current_app.logger.info(
                    "MPV screenshot capture requested",
                    extra={
                        "operation": "mpv_screenshot_capture",
                        "user": getattr(current_user, "username", None),
                        "remote_addr": request.headers.get("X-Forwarded-For") or request.remote_addr,
                        "user_agent": request.headers.get("User-Agent", ""),
                        "referer": request.headers.get("Referer", ""),
                    },
                )
            except Exception:
                # Never block capture due to logging issues
                pass

            # Check if CSRF token is present in form data or headers
            csrf_token = request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
            if not csrf_token:
                current_app.logger.error("CSRF token missing")
                abort(403, description="CSRF token missing")
        
            try:
                validate_csrf(csrf_token)
            except Exception as e:
                current_app.logger.error(f"CSRF validation failed: {str(e)}")
                abort(403, description="Invalid CSRF token")

            nonlocal last_screenshot_capture_ts

            # Fast-path throttle: if we captured recently, don't start a new systemd job.
            now = time.time()
            with screenshot_capture_lock:
                if (now - last_screenshot_capture_ts) < screenshot_min_interval_sec:
                    wait_sec = round(screenshot_min_interval_sec - (now - last_screenshot_capture_ts), 2)
                    return jsonify({"success": True, "skipped": True, "retry_in_sec": wait_sec})
                # Reserve the slot; even if the systemd start fails, we keep a short cooldown to avoid storms.
                last_screenshot_capture_ts = now

            # Import subprocess here to avoid UnboundLocalError
            import subprocess
        
            # Start service
            result = subprocess.run(
                ['sudo', '/bin/systemctl', 'start', 'screenshot.service'],
                check=True,
                timeout=10,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
    
            current_app.logger.info(f"Screenshot service started: {result.stdout}")

            # Best-effort validation (one-time cost, not on every GET poll).
            # systemctl "start" may return before the file is fully written, so we don't fail the request
            # if validation cannot be performed yet.
            try:
                screenshot_path = os.path.join(current_app.config['STATIC_FOLDER'], 'images', 'on_air_screen.jpg')
                if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 1024:
                    with Image.open(screenshot_path) as img:
                        img.verify()
            except Exception as ve:
                current_app.logger.warning(f"Screenshot validation skipped/failed: {str(ve)}")

            return jsonify({"success": True, "skipped": False})

        except subprocess.TimeoutExpired as e:
            current_app.logger.error(f"Service timeout: {e.stderr}")
            return jsonify({"success": False, "error": "Service timeout"}), 500
    
        except subprocess.CalledProcessError as e:
            current_app.logger.error(f"Service failed: {e.stderr}")
            return jsonify({"success": False, "error": "Service failed"}), 500
    
        except Exception as e:
            current_app.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            return jsonify({"success": False, "error": "Internal error"}), 500
            
    @api_bp.route('/media/thumbnail/<filename>', methods=['GET'])
    @login_required
    def get_media_thumbnail(filename):
        try:
            # Всегда используем .jpg в пути
            thumb_name = f"thumb_{Path(filename).stem}.jpg"
        
            # Пробуем отдать готовую миниатюру
            thumb_path = current_app.thumbnail_service.thumbnail_folder / thumb_name
            if thumb_path.exists():
                return send_from_directory(
                    current_app.thumbnail_service.thumbnail_folder,
                    thumb_name,
                    mimetype='image/jpeg',
                    max_age=86400
                )
        
            # Генерация новой миниатюры
            thumb_path = current_app.thumbnail_service.generate_thumbnail(filename)
            if thumb_path:
                return send_from_directory(
                    current_app.thumbnail_service.thumbnail_folder,
                    thumb_path.name,
                    mimetype='image/jpeg',
                    max_age=86400
                )
            
        except Exception as e:
            current_app.logger.error(f"Thumbnail error: {str(e)}")
        
        # Fallback
        return send_from_directory(
            current_app.static_folder,
            'images/default-preview.jpg',
            max_age=3600
        )
            
    @api_bp.route('/debug/thumbnails', methods=['GET'])
    @login_required
    def list_thumbnails():
        """Debug endpoint to list generated thumbnails"""
        thumb_dir = current_app.thumbnail_service.thumbnail_folder
        thumbs = []
        for f in thumb_dir.glob('thumb_*'):
            thumbs.append({
                'name': f.name,
                'size': f.stat().st_size,
                'modified': f.stat().st_mtime
            })
        return jsonify({'thumbnails': thumbs})

    @api_bp.route('/debug/thumbnail/<filename>', methods=['GET'])
    @login_required
    def get_thumbnail_debug(filename):
        """Debug endpoint to inspect a specific thumbnail"""
        thumb_path = current_app.thumbnail_service.thumbnail_folder / f"thumb_{filename}"
        if not thumb_path.exists():
            return jsonify({'error': 'Thumbnail not found'}), 404
        
        try:
            with Image.open(thumb_path) as img:
                return jsonify({
                    'filename': filename,
                    'size': thumb_path.stat().st_size,
                    'format': img.format,
                    'mode': img.mode,
                    'width': img.width,
                    'height': img.height
                })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
