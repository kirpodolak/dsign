class PlaybackConstants:
    DEFAULT_LOGO = 'idle_logo.jpg'
    SOCKET_PATH = '/tmp/mpv-socket'
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
    MAX_RETRIES = 3
    RETRY_DELAY = 3.0  # Увеличенная задержка между попытками
    SOCKET_TIMEOUT = 10.0  # Увеличенный таймаут для сокета
    MPV_START_TIMEOUT = 30  # Оптимальный таймаут запуска
    
    MPV_ENV = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/home/dsign",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "LIBVA_DRIVER_NAME": "drm",
        "GST_VAAPI_DRM_DEVICE": "/dev/dri/renderD128"
    }
    
    # Базовые параметры MPV (общие для всех режимов)
    MPV_BASE_PARAMS = [
        "--idle",
        f"--input-ipc-server={SOCKET_PATH}",
        "--no-config",
        "--no-osc",
        "--no-terminal",
        "--quiet",
        "--log-file=/var/log/mpv.log",
        "--msg-level=all=info",
        "--hwdec=auto-safe",  # Безопасное аппаратное декодирование
        "--opengl-es=yes",
        "--gpu-hwdec-interop=drm",
        "--vd-lavc-dr=yes",
        "--profile=sw-fast"
    ]
    
    # Параметры для DRM режима (для RPi 4 с новым ядром)
    MPV_DRM_PARAMS = [
        "--vo=drm",
        "--drm-connector=HDMI-A-1",  # Явное указание выхода
        "--drm-mode=preferred",
        "--drm-atomic=yes",
        "--hwdec=drm",
        "--gpu-context=drm",
        "--drm-draw-plane=primary",
        "--drm-drmprime-video-plane=overlay"
    ]
    
    # Параметры для оптимального режима Raspberry Pi
    MPV_RPI_PARAMS = [
        "--vo=gpu",
        "--gpu-context=drm",
        "--hwdec=v4l2m2m",  # Используем V4L2 M2M декодер
        "--hwdec-codecs=all"
    ]
    
    # Параметры для fallback режима
    MPV_FALLBACK_PARAMS = [
        "--vo=xv",  # Простой X11 вывод
        "--hwdec=no"  # Полностью программный рендеринг
    ]
