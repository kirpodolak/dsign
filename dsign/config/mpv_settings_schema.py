MPV_SETTINGS_SCHEMA = {
    "resolution": {
        "label": "Разрешение видео",
        "type": "select",
        "options": ["Автоматическое", "1080p", "2K", "4K"]
    },
    "aspect_ratio": {
        "label": "Соотношение сторон",
        "type": "select",
        "options": ["16:9", "4:3", "1.85:1"]
    },
    "rotation": {
        "label": "Поворот",
        "type": "select",
        "options": [0, 90, 180, 270]
    },
    "fullscreen": {
        "label": "Полноэкранный режим",
        "type": "boolean"
    },
    "video_zoom": {
        "label": "Зуммирование видео",
        "type": "range",
        "min": 0,
        "max": 100,
        "step": 5,
        "unit": "%"
    },
    "volume": {
        "label": "Громкость",
        "type": "range",
        "min": 0,
        "max": 100,
        "step": 1
    },
    "mute": {
        "label": "Без звука",
        "type": "boolean"
    },
    "hwdec": {
        "label": "Аппаратное декодирование (hwdec)",
        "type": "select",
        "options": ["no", "auto", "vaapi", "nvdec"]
    },
    "vo": {
        "label": "Video Output (vo)",
        "type": "select",
        "options": ["gpu", "xv", "sdl"]
    },
    "scale": {
        "label": "Масштабирование (scale)",
        "type": "select",
        "options": ["bilinear", "bicubic", "lanczos"]
    }
}
