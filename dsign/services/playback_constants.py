import os
import pwd
from pathlib import Path

class PlaybackConstants:
    VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")
    AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".aac", ".opus")
    DEFAULT_LOGO = 'idle_logo.jpg'
    SOCKET_PATH = '/var/lib/dsign/mpv/socket'
    SOCKET_DIR = os.path.dirname(SOCKET_PATH)
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
    LABWC_CONFIG_DIR = '/var/lib/dsign/labwc'
    COMPOSITOR_SYSTEMD_UNIT = 'dsign-compositor.service'
    LOGO_SYSTEMD_UNIT = 'dsign-logo.service'
    MPV_DRM_SYSTEMD_UNIT = 'dsign-mpv.service'
    MPV_WAYLAND_SYSTEMD_UNIT = 'dsign-mpv-wayland.service'
    # VK via ytdl://: prefer muxed A/V <=1080p (4K software decode overheats signage PCs).
    VK_YTDL_FORMAT = (
        "best[height<=1080][acodec!=none]/"
        "bestvideo[height<=1080]+bestaudio/"
        "best[height<=1080]"
    )
    MAX_RETRIES = 3  # Увеличено с 3
    # Used between retries for errors where MPV likely needs systemd recovery (socket missing, etc.).
    RETRY_DELAY = 5.0  # Увеличено с 1.0
    # Fast backoff when MPV resets/closes the IPC socket mid-command (avoid starving HTTP workers).
    RETRY_DELAY_TRANSPORT_SEC = (0.15, 0.35, 0.75)
    SOCKET_TIMEOUT = 10.0  # Увеличено с 5.0

    @classmethod
    def display_backend(cls) -> str:
        """drm (default) | wayland | labwc"""
        raw = (os.getenv("DSIGN_DISPLAY_BACKEND") or "").strip().lower()
        if raw in ("wayland", "labwc", "wl"):
            return "wayland"
        if raw in ("drm", "kms", "tty"):
            return "drm"
        if os.getenv("WAYLAND_DISPLAY"):
            return "wayland"
        return "drm"

    @classmethod
    def is_wayland_backend(cls) -> bool:
        return cls.display_backend() == "wayland"

    @classmethod
    def mpv_backend_label(cls) -> str:
        return "Wayland/gpu" if cls.is_wayland_backend() else "DRM"

    @classmethod
    def mpv_systemd_unit(cls) -> str:
        explicit = (os.getenv("DSIGN_MPV_UNIT") or "").strip()
        if explicit:
            return explicit
        if cls.is_wayland_backend():
            return cls.MPV_WAYLAND_SYSTEMD_UNIT
        return cls.MPV_DRM_SYSTEMD_UNIT

    @classmethod
    def dsign_uid(cls) -> int:
        try:
            return pwd.getpwnam("dsign").pw_uid
        except KeyError:
            return int(os.getenv("DSIGN_UID") or "1000")

    @classmethod
    def xdg_runtime_dir(cls) -> str:
        explicit = (os.getenv("XDG_RUNTIME_DIR") or "").strip()
        if explicit:
            return explicit
        return f"/run/user/{cls.dsign_uid()}"

    @classmethod
    def wayland_display(cls) -> str:
        return (os.getenv("WAYLAND_DISPLAY") or "wayland-0").strip() or "wayland-0"

    @classmethod
    def wayland_env(cls) -> dict:
        env = dict(cls.MPV_ENV)
        env["WAYLAND_DISPLAY"] = cls.wayland_display()
        env["XDG_RUNTIME_DIR"] = cls.xdg_runtime_dir()
        if cls.is_wayland_backend():
            env.pop("DISPLAY", None)
        return env
    
    MPV_ENV = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/home/dsign",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "LIBVA_DRIVER_NAME": "iHD",
        "GST_VAAPI_ALL_DRIVERS": "1",
    }
