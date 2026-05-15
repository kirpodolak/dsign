import os
from pathlib import Path

class PlaybackConstants:
    DEFAULT_LOGO = 'idle_logo.jpg'
    SOCKET_PATH = '/var/lib/dsign/mpv/socket'
    SOCKET_DIR = os.path.dirname(SOCKET_PATH)
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
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
    
    MPV_ENV = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/home/dsign",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "LIBVA_DRIVER_NAME": "kms",
        "GST_VAAPI_ALL_DRIVERS": "1"
    }
