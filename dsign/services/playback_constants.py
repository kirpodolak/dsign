class PlaybackConstants:
    DEFAULT_LOGO = 'idle_logo.jpg'
    SOCKET_PATH = '/tmp/mpv-socket'
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # Увеличили задержку
    SOCKET_TIMEOUT = 5.0  # Увеличили таймаут
    MPV_START_TIMEOUT = 60  # Увеличили таймаут запуска
    MPV_ENV = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/home/dsign",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"
    }
    
    # Базовые параметры MPV
    MPV_BASE_PARAMS = [
        "--idle",
        f"--input-ipc-server={SOCKET_PATH}",
        "--no-config",
        "--no-osc",
        "--no-terminal",
        "--quiet",
        "--log-file=/var/log/mpv.log",
        "--msg-level=all=info"
    ]
    
    # Параметры для DRM режима
    MPV_DRM_PARAMS = [
        "--vo=drm",
        "--hwdec=drm",
        "--gpu-context=drm",
        "--drm-atomic=yes",
        "--drm-mode=preferred"
    ]
    
    # Параметры для fallback режима
    MPV_FALLBACK_PARAMS = [
        "--vo=gpu",
        "--hwdec=auto-safe"
    ]
