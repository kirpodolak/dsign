MPV_SETTINGS_SCHEMA = {
    # Аудио
    "ao": {
        "label": "Аудиовыход (ao)",
        "type": "select",
        "options": ["auto", "alsa", "pulse", "jack", "sdl", "openal"]
    },
    "audio-device": {
        "label": "Аудиоустройство",
        "type": "select",
        "options": ["auto"]  # Список можно динамически заполнять из audio-device-list
    },
    "volume": {
        "label": "Громкость",
        "type": "range",
        "min": 0,
        "max": 100,
        "step": 1
    },
    "ao-volume": {
        "label": "Громкость аудиовыхода",
        "type": "range",
        "min": 0,
        "max": 100,
        "step": 1
    },
    "mute": {
        "label": "Без звука",
        "type": "boolean"
    },

    # Видео
    "vo": {
        "label": "Видеовыход (vo)",
        "type": "select",
        "options": ["gpu", "xv", "sdl", "drm", "vdpau", "vaapi"]
    },
    "video-aspect": {
        "label": "Соотношение сторон",
        "type": "select",
        "options": ["16:9", "4:3", "1.85:1", "2.35:1", "-1"]  # -1 для автоматического
    },
    "video-aspect-override": {
        "label": "Переопределение соотношения",
        "type": "select",
        "options": ["no", "16:9", "4:3", "1.85:1"]
    },
    "video-rotate": {
        "label": "Поворот видео",
        "type": "select",
        "options": [0, 90, 180, 270]
    },
    "dwidth": {
        "label": "Ширина вывода",
        "type": "number"
    },
    "dheight": {
        "label": "Высота вывода",
        "type": "number"
    },
    "video-scale-x": {
        "label": "Масштаб по X",
        "type": "range",
        "min": 0.1,
        "max": 10,
        "step": 0.1
    },
    "video-scale-y": {
        "label": "Масштаб по Y",
        "type": "range",
        "min": 0.1,
        "max": 10,
        "step": 0.1
    },
    "video-zoom": {
        "label": "Зум видео",
        "type": "range",
        "min": 0.1,
        "max": 10,
        "step": 0.1
    },
    "fullscreen": {
        "label": "Полноэкранный режим",
        "type": "boolean"
    },
    "sub-visibility": {
        "label": "Показывать субтитры",
        "type": "boolean"
    },

    # Цветокоррекция
    "brightness": {
        "label": "Яркость",
        "type": "range",
        "min": -100,
        "max": 100,
        "step": 1
    },
    "contrast": {
        "label": "Контраст",
        "type": "range",
        "min": -100,
        "max": 100,
        "step": 1
    },
    "saturation": {
        "label": "Насыщенность",
        "type": "range",
        "min": -100,
        "max": 100,
        "step": 1
    },
    "gamma": {
        "label": "Гамма",
        "type": "range",
        "min": -100,
        "max": 100,
        "step": 1
    },
    "hue": {
        "label": "Оттенок",
        "type": "range",
        "min": -100,
        "max": 100,
        "step": 1
    }
}
