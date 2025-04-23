class PlaybackConstants:
    DEFAULT_LOGO = 'idle_logo.jpg'
    SOCKET_PATH = '/var/lib/dsign/mpv/socket'
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
    MAX_RETRIES = 5  # Увеличено с 3
    RETRY_DELAY = 2.0  # Увеличено с 1.0
    SOCKET_TIMEOUT = 10.0  # Увеличено с 5.0
    
    MPV_ENV = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/home/dsign",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "LIBVA_DRIVER_NAME": "kms",
        "GST_VAAPI_ALL_DRIVERS": "1"
    }
