class PlaybackConstants:
    DEFAULT_LOGO = 'idle_logo.jpg'
    SOCKET_PATH = '/tmp/mpv-socket'
    DEFAULT_RESOLUTION = '1920x1080'
    DEFAULT_ASPECT_RATIO = '16:9'
    MAX_RETRIES = 3
    RETRY_DELAY = 0.5
    SOCKET_TIMEOUT = 2.0
    MPV_START_TIMEOUT = 30
    MPV_ENV = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/var/www",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"
    }
