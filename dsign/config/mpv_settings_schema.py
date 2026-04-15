# MPV options exposed in Settings → Advanced (digital signage).
# Global volume is controlled from the Status dashboard (amixer), not here.
# Per-playlist rotation / mute / output use playlist overrides.

MPV_SETTINGS_SCHEMA = {
    "audio-route": {
        "label": "Куда выводить звук",
        "type": "select",
        "options": ["auto", "hdmi", "headphones"],
        "option_labels": {
            "auto": "По умолчанию (система)",
            "hdmi": "HDMI",
            "headphones": "Аналог 3.5 мм",
        },
    },
    "video-aspect": {
        "label": "Соотношение сторон",
        "type": "select",
        "options": ["16:9", "4:3", "1.85:1", "2.35:1", "-1"],
    },
    "panscan": {
        "label": "Panscan (0 — вписать, 1 — заполнить с обрезкой)",
        "type": "range",
        "min": 0,
        "max": 1,
        "step": 0.05,
        "default": 0,
    },
    "video-zoom": {
        "label": "Зум видео",
        "type": "range",
        "min": 0,
        "max": 10,
        "step": 0.1,
        "default": 0,
    },
    "dwidth": {
        "label": "Ширина вывода (px, нестандартные экраны)",
        "type": "number",
    },
    "dheight": {
        "label": "Высота вывода (px)",
        "type": "number",
    },
}
