# DSign — Digital Signage System

Digital signage platform built on Raspberry Pi / Linux, using **Wayland (labwc) + MPV (vo=gpu)** for reliable, flicker-free video playback with idle logo overlay.

---

## Architecture

```
+---------------------------------------------+
|  Wayland (wayland-1) — labwc compositor   |
|  +-------------------------------------+    |
|  |  imv — idle logo (always on, bottom)|    |
|  |  MPV — video playback (top layer) |    |
|  |  foot/wofi — Wi-Fi / UI overlay   |    |
|  +-------------------------------------+    |
|              ↓ DRM/KMS                      |
+---------------------------------------------+
```

| Layer | Component | Role |
|-------|-----------|------|
| Compositor | **labwc** | Wayland compositor, window management, seamless transitions |
| Logo viewer | **imv** | Always-on idle logo, independent from MPV |
| Video player | **MPV (vo=gpu)** | Hardware-accelerated video via Wayland |
| Controller | **Flask + Socket.IO** | Web UI, API, playlist management |
| Downloader | **yt-dlp** | VK / Rutube / direct URL fetching |

---

## Why Wayland + labwc?

| Problem (vo=drm) | Solution (labwc + vo=gpu) |
|--------------------|---------------------------|
| Terminal flicker between videos | Compositor handles frame transitions |
| MPV crash = black screen / terminal | imv logo stays visible underneath |
| Idle logo via MPV IPC (complex, fragile) | imv runs independently via systemd |
| No overlay UI possible | Any Wayland app runs on top |
| TTY1 / getty conflicts | Pure Wayland, no TTY needed |

---

## Quick Start

### Requirements

- Raspberry Pi 4/5 (or any Linux with DRM/KMS)
- Raspberry Pi OS Bookworm (or Debian 12+)
- User `dsign` in groups: `video`, `audio`, `input`

### Install

```bash
git clone https://github.com/kirpodolak/dsign.git
cd dsign
sudo bash install_dsign.sh
```

The installer sets up:
- `labwc`, `imv`, `seatd`, `mpv`
- Python venv with dependencies
- Systemd services (see below)
- Wi-Fi captive portal (first boot)

### Services

| Service | Purpose | Start order |
|---------|---------|-------------|
| `dsign-compositor.service` | labwc Wayland compositor | 1st |
| `dsign-logo.service` | imv idle logo viewer | 2nd |
| `dsign-mpv.service` | MPV video player (vo=gpu) | 3rd |
| `digital-signage.service` | Flask controller + API | 4th |

```bash
sudo systemctl enable --now dsign-compositor dsign-logo dsign-mpv digital-signage
```

---

## Configuration

### Environment Variables

```bash
# /etc/systemd/system/digital-signage.service.d/override.conf
[Service]
Environment="WAYLAND_DISPLAY=wayland-1"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
Environment="DSIGN_LOGO_PATH=/var/lib/dsign/media/idle_logo.jpg"
Environment="DSIGN_MPV_PROFILE=signboard"
```

### MPV Profiles

Profiles live in `~/.config/mpv/mpv.conf` or `/etc/mpv/mpv.conf`:

```ini
[signboard]
vo=gpu
gpu-context=wayland
hwdec=auto
video-sync=display-resample
fullscreen=yes
idle=yes
```

### labwc Config

```xml
<!-- /var/lib/dsign/labwc/rc.xml -->
<labwc_config>
  <core>
    <decoration>none</decoration>
    <gap>0</gap>
  </core>
  <windowRules>
    <windowRule identifier="mpv" fullscreen="yes" serverDecoration="no"/>
    <windowRule identifier="imv" fullscreen="yes" serverDecoration="no"/>
  </windowRules>
</labwc_config>
```

---

## Web Interface

After install, open `http://<pi-ip>:5000`:

- **Upload** — drag & drop videos/images
- **Playlists** — create schedules with start/end times
- **Live** — start/stop playback, preview status
- **Settings** — logo, Wi-Fi, display mode

Default login: `admin` / `admin` (change on first boot).

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/playback/start` | Start playlist by ID |
| POST | `/api/playback/stop` | Stop, return to idle logo |
| GET | `/api/playback/status` | Current state, file, progress |
| POST | `/api/media/upload` | Upload video/image |
| GET | `/api/media` | List all media |
| POST | `/api/playlist` | Create playlist |

---

## Project Structure

```
dsign/
├── dsign/                    # Python application
│   ├── api/                  # Flask routes (REST + Socket.IO)
│   ├── services/             # Business logic
│   │   ├── playback_service.py      # Main playback controller
│   │   ├── playlist_management.py   # Playlist engine
│   │   ├── logo_management.py      # Idle logo (imv control)
│   │   ├── mpv_management.py       # MPV IPC wrapper
│   │   ├── media_downloader.py     # VK/Rutube/URL fetcher
│   │   └── network_manager.py      # Wi-Fi / connectivity
│   ├── models/               # SQLAlchemy models
│   ├── templates/            # Jinja2 web UI
│   └── static/               # CSS, JS, placeholder images
├── etc/
│   └── systemd/system/       # Service units
│       ├── dsign-compositor.service
│       ├── dsign-logo.service
│       ├── dsign-mpv.service
│       └── digital-signage.service
├── usr/local/bin/            # Launch scripts
│   ├── dsign-mpv-launch-wayland
│   ├── dsign-labwc-autostart
│   └── dsign-show-startup-ip
├── var/lib/dsign/labwc/      # labwc config
│   └── rc.xml
├── install_dsign.sh          # One-shot installer
└── README.md                 # This file
```

---

## Troubleshooting

### Black screen, no logo

```bash
sudo systemctl status dsign-compositor   # labwc running?
sudo systemctl status dsign-logo         # imv running?
ls -la /run/user/1000/wayland-1          # socket exists?
```

### MPV not showing video

```bash
# Check MPV logs
sudo journalctl -u dsign-mpv -f

# Verify vo=gpu
grep "VO:" /var/lib/dsign/mpv/mpv.log
```

### High CPU on Pi 3B+

Switch labwc to software rendering:

```bash
sudo systemctl edit dsign-compositor
# Add:
[Service]
Environment="WLR_RENDERER=pixman"
```

### Wi-Fi captive portal not showing

```bash
sudo systemctl restart dsign-show-startup-ip
# Or manually:
foot --font=monospace:16 nmtui
```

---

## Development

```bash
# Setup dev environment
cd dsign
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run tests
pytest

# Run locally (without systemd)
WAYLAND_DISPLAY=wayland-1 python run.py
```

---

## License

MIT License — see [LICENSE](LICENSE).

---

## Credits

- [MPV](https://mpv.io/) — video player
- [labwc](https://labwc.github.io/) — Wayland compositor
- [imv](https://github.com/eXeC64/imv) — image viewer
- [Flask](https://flask.palletsprojects.com/) — web framework
- [Socket.IO](https://socket.io/) — real-time communication
