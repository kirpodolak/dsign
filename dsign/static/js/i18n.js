/**
 * UI strings: Russian (default) and English. Persists in localStorage.
 */
const STORAGE_KEY = 'dsign_ui_lang';

const CATALOG = {
  ru: {
    brand_center: 'Digital Signage',
    app_window_title: 'Control Panel',
    nav_home: 'Главная',
    nav_gallery: 'Галерея',
    nav_settings: 'Настройки',
    nav_playlist: 'Плейлист',
    logout: 'Выход',
    lang_ru: 'Русский',
    lang_en: 'English',
    lang_aria: 'Язык интерфейса',
    page_home_title: 'Панель управления',
    page_gallery_title: 'Медиа-галерея',
    page_settings_title: 'Настройки',
    page_playlist_title: 'Редактирование плейлиста',
    playlist_lead: 'Выберите слайды и задайте длительность показа для изображений.',
    pl_col_num: '#',
    pl_col_include: 'Вкл',
    pl_col_preview: 'Превью',
    pl_col_filename: 'Файл',
    pl_col_mute: 'Без звука',
    pl_col_duration: 'Длит. (сек)',
    playlist_empty: 'Нет медиафайлов. Сначала добавьте файлы в папку.',
    btn_save_playlist: 'Сохранить плейлист',
    btn_cancel: 'Отмена',
    saving_ellipsis: 'Сохранение…',
    pl_video_full: 'Полное видео',
    gallery_toolbar_aria: 'Действия галереи',
    playlist_col_name: 'Название',
    playlist_col_customer: 'Клиент',
    playlist_col_files: 'Файлы',
    playlist_col_status: 'Статус',
    playlist_col_actions: 'Действия',
    playlists_heading: 'Плейлисты',
    btn_new_playlist: 'Новый плейлист',
    current_settings: 'Текущие настройки',
    mpv_preview: 'Предпросмотр MPV',
    current_logo: 'Текущий логотип',
    last_updated_label: 'Обновлено',
    last_updated: 'Обновлено',
    never: '—',
    modal_new_playlist: 'Новый плейлист',
    form_playlist_name: 'Название плейлиста',
    form_customer: 'Клиент',
    btn_create: 'Создать',
    choose_file: 'Файл…',
    apply_logo: 'Применить',
    no_file: 'Файл не выбран',
    refresh_preview: 'Обновить предпросмотр',
    metric_ops_title: 'Операционные метрики',
    metric_screen: 'Экран',
    metric_volume: 'Громкость',
    metric_broadcast: 'Вещание',
    metric_storage: 'Хранилище',
    metric_cpu_temp: 'Температура CPU',
    metric_cpu_load: 'Загрузка CPU',
    metric_transcode: 'Оптимизация видео',
    metric_ip: 'Текущий IP',
    value_na: 'н/д',
    value_auto: 'Авто',
    value_mute: 'Без звука',
    broadcast_logo: 'Логотип',
    transcode_on: 'Вкл',
    transcode_off: 'Выкл',
    auto_preview_bold: 'Авто-предпросмотр',
    preview_status_off: 'Авто-предпросмотр: выкл',
    preview_status_on: 'каждые {0} мин',
    preview_block_hint: 'Фоновый захват отключён. Используйте кнопку «Обновить».',
    preview_interval_line: 'Совет: на Pi 3B+ — реже или выкл.',
    preview_lines_on: (mins) => [
      `<span class="mpv-auto-refresh-line"><strong>Авто-предпросмотр:</strong> каждые ${mins} мин</span>`,
      '<span class="mpv-auto-refresh-line">Совет: на Pi 3B+ — реже или выкл.</span>',
    ].join(''),
    status_playing: 'Воспроизведение',
    status_stopped: 'Остановлено',
    status_idle: 'Простой',
    unnamed: 'Без названия',
    play_title: 'Воспроизвести',
    stop_title: 'Стоп',
    edit_title: 'Правка',
    delete_title: 'Удалить',
    deleting_ellipsis: 'Удаление…',
    gallery_search_sort: 'Поиск и сортировка',
    gallery_add_link: 'Ссылка',
    gallery_upload: 'Загрузка',
    btn_upload: 'Загрузить',
    upload_ellipsis: 'Загрузка…',
    processing_ellipsis: 'Обработка…',
    ext_placeholder: 'Ссылка VK / Rutube…',
    ext_hint: 'Поддержка: VK Video, Rutube',
    btn_add: 'Добавить',
    search_placeholder: 'Поиск файлов…',
    sort_name_az: 'Имя (А–Я)',
    sort_name_za: 'Имя (Я–А)',
    sort_date_new: 'Дата (новые)',
    sort_date_old: 'Дата (старые)',
    sort_type: 'По типу',
    group_none: 'Без групп',
    group_type: 'По типу',
    group_date: 'По дате',
    select_all: 'Выбрать все',
    deselect_all: 'Снять выбор',
    delete_selected: 'Удалить выбранные',
    preview_close: 'Закрыть',
    settings_status_h: 'Состояние',
    settings_playback_h: 'Воспроизведение плейлистов',
    settings_playback_lead:
      'Включите «Переопределение» для настроек по плейлисту. Сохраняется автоматически. Выключите, чтобы вернуться к умолчанию.',
    btn_advanced_mpv: 'Расширенные MPV…',
    err_title: 'Ошибка',
    dash_title_storage: 'Хранилище (медиа)',
    dash_title_cput: 'Темп. CPU',
    dash_title_cpuu: 'Загрузка CPU',
    dash_title_audio: 'Аудио',
    dash_audio_hint: 'Потяните кольцо вверх/вниз',
    dash_audio_muted: 'Без звука',
    dash_cpu_from_stat: 'из /proc/stat',
    dash_cpu_estimated: 'оценка (loadavg)',
    audio_unavailable: 'amixer недоступен',
    word_used: 'занято',
    word_total: 'всего',
    alert_error: 'Ошибка',
    alert_success: 'Успех',
    alert_warning: 'Внимание',
    alert_info: 'Информация',
  },
  en: {
    brand_center: 'Digital Signage',
    app_window_title: 'Control Panel',
    nav_home: 'Home',
    nav_gallery: 'Gallery',
    nav_settings: 'Settings',
    nav_playlist: 'Playlist',
    logout: 'Logout',
    lang_ru: 'Русский',
    lang_en: 'English',
    lang_aria: 'Interface language',
    page_home_title: 'Control Panel',
    page_gallery_title: 'Media Gallery',
    page_settings_title: 'Settings',
    page_playlist_title: 'Edit playlist',
    playlist_lead: 'Choose slides and set how long each image is shown.',
    pl_col_num: '#',
    pl_col_include: 'Include',
    pl_col_preview: 'Preview',
    pl_col_filename: 'File name',
    pl_col_mute: 'Mute',
    pl_col_duration: 'Duration (sec)',
    playlist_empty: 'No media files found. Add files to the media folder first.',
    btn_save_playlist: 'Save playlist',
    btn_cancel: 'Cancel',
    saving_ellipsis: 'Saving…',
    pl_video_full: 'Full video',
    gallery_toolbar_aria: 'Gallery actions',
    playlist_col_name: 'Name',
    playlist_col_customer: 'Customer',
    playlist_col_files: 'Files',
    playlist_col_status: 'Status',
    playlist_col_actions: 'Actions',
    playlists_heading: 'Playlists',
    btn_new_playlist: 'New Playlist',
    current_settings: 'Current Settings',
    mpv_preview: 'MPV Player Preview',
    current_logo: 'Current Logo',
    last_updated_label: 'Last updated',
    last_updated: 'Last updated',
    never: '—',
    modal_new_playlist: 'New Playlist',
    form_playlist_name: 'Playlist name',
    form_customer: 'Customer',
    btn_create: 'Create',
    choose_file: 'Choose File',
    apply_logo: 'Apply Logo',
    no_file: 'No file selected',
    refresh_preview: 'Refresh Preview',
    metric_ops_title: 'Operational Metrics',
    metric_screen: 'Screen',
    metric_volume: 'Volume',
    metric_broadcast: 'Broadcast',
    metric_storage: 'Storage',
    metric_cpu_temp: 'CPU Temperature',
    metric_cpu_load: 'CPU Load',
    metric_transcode: 'Video Optimization',
    metric_ip: 'Current IP Address',
    value_na: 'N/A',
    value_auto: 'Auto',
    value_mute: 'Mute',
    broadcast_logo: 'Logo',
    transcode_on: 'On',
    transcode_off: 'Off',
    auto_preview_bold: 'Auto preview',
    preview_status_off: 'Auto preview: Off',
    preview_status_on: 'Every {0} min',
    preview_block_hint: 'Background capture is blocked. Use the Refresh button.',
    preview_interval_line: 'Tip: On Pi 3B+ use Off or infrequent.',
    preview_lines_on: (mins) => [
      `<span class="mpv-auto-refresh-line"><strong>Auto preview:</strong> Every ${mins} min</span>`,
      '<span class="mpv-auto-refresh-line">Tip: On Pi 3B+ use Off or infrequent.</span>',
    ].join(''),
    status_playing: 'Playing',
    status_stopped: 'Stopped',
    status_idle: 'Idle',
    unnamed: 'Unnamed',
    play_title: 'Play',
    stop_title: 'Stop',
    edit_title: 'Edit',
    delete_title: 'Delete',
    deleting_ellipsis: 'Deleting…',
    gallery_search_sort: 'Search & Sort',
    gallery_add_link: 'Add link',
    gallery_upload: 'Upload',
    btn_upload: 'Upload',
    upload_ellipsis: 'Uploading…',
    processing_ellipsis: 'Processing…',
    ext_placeholder: 'Paste VK / Rutube link…',
    ext_hint: 'Supported: VK Video, Rutube',
    btn_add: 'Add',
    search_placeholder: 'Search files…',
    sort_name_az: 'Name (A–Z)',
    sort_name_za: 'Name (Z–A)',
    sort_date_new: 'Date (newest)',
    sort_date_old: 'Date (oldest)',
    sort_type: 'File type',
    group_none: 'No grouping',
    group_type: 'By type',
    group_date: 'By date',
    select_all: 'Select All',
    deselect_all: 'Deselect All',
    delete_selected: 'Delete Selected',
    preview_close: 'Close preview',
    settings_status_h: 'Status',
    settings_playback_h: 'Playlist playback settings',
    settings_playback_lead:
      'Toggle Override to customize playback per playlist. Changes save automatically. Turn Override off to return to default.',
    btn_advanced_mpv: 'Advanced MPV…',
    err_title: 'Error',
    dash_title_storage: 'Storage (media)',
    dash_title_cput: 'CPU temp',
    dash_title_cpuu: 'CPU usage',
    dash_title_audio: 'Audio',
    dash_audio_hint: 'Drag ring vertically to change',
    dash_audio_muted: 'Muted',
    dash_cpu_from_stat: 'from /proc/stat',
    dash_cpu_estimated: 'estimated (loadavg)',
    audio_unavailable: 'amixer not available',
    word_used: 'used',
    word_total: 'total',
    alert_error: 'Error',
    alert_success: 'Success',
    alert_warning: 'Warning',
    alert_info: 'Info',
  },
};

function getLang() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'en' || v === 'ru') return v;
  } catch {
    /* ignore */
  }
  return 'ru';
}

/**
 * @param {string} key
 * @param {string} [lang]
 * @param {unknown[]} [repl]
 */
export function t(key, lang, ...repl) {
  const l = lang || getLang();
  const row = CATALOG[l] || CATALOG.ru;
  let val = row[key];
  if (val === undefined) val = CATALOG.ru[key];
  if (val === undefined) return key;
  if (typeof val === 'function') {
    return val(...repl);
  }
  if (typeof val === 'string' && repl.length) {
    return val.replace(/\{(\d+)\}/g, (_, i) => String(repl[Number(i)] ?? ''));
  }
  return val;
}

export function getUiLang() {
  return getLang();
}

export function setUiLang(lang) {
  const next = lang === 'en' ? 'en' : 'ru';
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = next === 'en' ? 'en' : 'ru';
    document.documentElement.setAttribute('data-dsign-lang', next);
  }
  applyI18n();
  window.dispatchEvent(new CustomEvent('dsign:language-changed', { detail: { lang: next } }));
}

/**
 * Set text/placeholder/aria for elements marked with data-i18n, data-i18n-placeholder, data-i18n-aria, data-i18n-title
 */
export function applyI18n() {
  const lang = getLang();
  if (typeof document === 'undefined') return;

  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const k = el.getAttribute('data-i18n');
    if (k) el.textContent = t(k, lang);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    const k = el.getAttribute('data-i18n-placeholder');
    if (k && 'placeholder' in el) el.placeholder = t(k, lang);
  });
  document.querySelectorAll('[data-i18n-aria]').forEach((el) => {
    const k = el.getAttribute('data-i18n-aria');
    if (k) el.setAttribute('aria-label', t(k, lang));
  });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => {
    const k = el.getAttribute('data-i18n-title');
    if (k) el.title = t(k, lang);
  });

  // Lang toggle active state
  const ruBtn = document.querySelector('[data-lang="ru"]');
  const enBtn = document.querySelector('[data-lang="en"]');
  if (ruBtn) ruBtn.setAttribute('aria-pressed', lang === 'ru' ? 'true' : 'false');
  if (enBtn) enBtn.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
  if (ruBtn) ruBtn.classList.toggle('lang-toggle__btn--active', lang === 'ru');
  if (enBtn) enBtn.classList.toggle('lang-toggle__btn--active', lang === 'en');

  // Page <title> = "{page} · Digital Signage"
  const pageKey = document.body?.getAttribute('data-page-title-i18n');
  const brand = t('brand_center', lang);
  if (pageKey) {
    const pagePart = t(pageKey, lang);
    document.title = `${pagePart} · ${brand}`;
  } else {
    document.title = brand;
  }
}

function bindLangToggles() {
  document.querySelectorAll('[data-lang-set]').forEach((btn) => {
    if (btn.dataset.dsignLangBound === '1') return;
    btn.dataset.dsignLangBound = '1';
    btn.addEventListener('click', () => {
      const l = btn.getAttribute('data-lang-set');
      if (l === 'en' || l === 'ru') setUiLang(l);
    });
  });
}

export function initI18n() {
  if (typeof document === 'undefined' || !document.documentElement) return;
  if (document.documentElement.dataset.dsignI18nInit === '1') {
    applyI18n();
    bindLangToggles();
    return;
  }
  document.documentElement.dataset.dsignI18nInit = '1';
  const lang = getLang();
  document.documentElement.setAttribute('data-dsign-lang', lang);
  document.documentElement.lang = lang === 'en' ? 'en' : 'ru';
  bindLangToggles();
  applyI18n();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initI18n());
  } else {
    initI18n();
  }
}
