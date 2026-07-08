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
    playlist_lead: 'Порядок строк — порядок воспроизведения. Добавляйте файлы кнопкой «Добавить медиа»; уберите строку кнопкой «Удалить».',
    pl_col_num: '#',
    pl_col_include: 'Вкл',
    pl_col_remove: 'Удалить',
    pl_row_remove_aria: 'Убрать из плейлиста',
    pl_col_preview: 'Превью',
    pl_col_filename: 'Файл',
    pl_col_mute: 'Без звука',
    pl_col_duration: 'Длит. (сек)',
    playlist_empty: 'Нет медиафайлов. Сначала добавьте файлы в папку.',
    playlist_editor_empty: 'Плейлист пуст. Нажмите «Добавить медиа», чтобы выбрать файлы со склада.',
    playlist_save_success: 'Плейлист сохранён. Переход на главную…',
    playlist_save_cleared: 'Пустой плейлист сохранён. Переход на главную…',
    btn_save_playlist: 'Сохранить плейлист',
    btn_add_media: 'Добавить медиа',
    pl_modal_title: 'Добавить медиа в плейлист',
    pl_modal_filter: 'Папка',
    pl_modal_cart: 'Корзина',
    pl_modal_cart_items: 'позиций',
    pl_modal_add_to_pl: 'Добавить в плейлист',
    pl_modal_no_files: 'Нет файлов по фильтру',
    pl_filter_all: 'Все медиа',
    pl_filter_unsorted: 'Несортированное',
    pl_already_in_pl: 'Уже в плейлисте',
    pl_in_cart: 'В корзине',
    pl_cart_remove: 'Убрать из корзины',
    pl_cart_empty: 'В корзине ничего нет',
    pl_append_ok: 'Добавлено в конец плейлиста',
    pl_append_skipped: 'пропущено',
    btn_cancel: 'Отмена',
    saving_ellipsis: 'Сохранение…',
    pl_video_full: 'Полное видео',
    gallery_toolbar_aria: 'Действия галереи',
    playlist_col_preview: 'Превью',
    playlist_col_name: 'Название',
    playlist_col_customer: 'Клиент',
    playlist_col_files: 'Файлы',
    playlist_col_status: 'Статус',
    playlist_col_actions: 'Действия',
    playlists_heading: 'Плейлисты',
    btn_new_playlist: 'Новый плейлист',
    current_settings: 'Текущие настройки',
    now_on_screen: 'Сейчас на экране',
    system_panel: 'Система',
    waiting_screen: 'Экран ожидания',
    waiting_screen_hint: 'Показывается при простое',
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
    replace_logo: 'Заменить',
    no_preview: 'Нет превью',
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
    metric_services_title: 'Системные сервисы',
    service_digital_signage_short: 'DGS',
    service_digital_signage_full: 'digital-signage.service',
    service_mpv_short: 'MPV',
    service_mpv_full: 'dsign-mpv.service',
    service_network_assistant_short: 'NTA',
    service_network_assistant_full: 'dsign-network-assistant.service',
    service_screenshot_short: 'SCS',
    service_screenshot_full: 'screenshot.service',
    os_linux_short: 'OSL',
    os_linux_full: 'ОС Linux',
    network_assist_toggle_off: 'ВЫКЛ',
    network_assist_toggle_auto: 'ОФЛ',
    network_assist_toggle_force: 'ВКЛ',
    network_assistant_boot_tooltip: 'Помощник Wi‑Fi при загрузке: ВЫКЛ — только IP; ОФЛ — nmtui если нет сети; ВКЛ — всегда показать nmtui.',
    restart_service: 'Перезапустить сервис',
    reboot_system: 'Перезагрузить систему',
    svc_status_active: 'Активен',
    svc_status_dead: 'Сбой',
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
    status_stopped: 'Остановлен',
    status_idle: 'Простой',
    unnamed: 'Без названия',
    play_title: 'Воспроизвести',
    stop_title: 'Стоп',
    edit_title: 'Правка',
    delete_title: 'Удалить',
    deleting_ellipsis: 'Удаление…',
    gallery_search_sort: 'Поиск и сортировка',
    gallery_folders_title: 'Папки и вид',
    gallery_view_by_folder: 'По папкам',
    gallery_new_folder_ph: 'Имя новой папки',
    gallery_create_folder: 'Создать папку',
    gallery_folder_label: 'Папка',
    gallery_folder_created: 'Папка создана',
    gallery_folder_create_err: 'Не удалось создать папку',
    gallery_new_folder_card_title: 'Новая папка',
    gallery_folders_list_title: 'Папки',
    gallery_bulk_move_label: 'В папку',
    gallery_bulk_move_card_title: 'Перемещение выбранных',
    gallery_bulk_move_btn: 'Переместить выбранные',
    gallery_bulk_moved: 'Перемещено: {n}',
    gallery_bulk_move_err: 'Не удалось переместить',
    gallery_bulk_move_none: 'Отметьте файлы галочками',
    gallery_toolbar_move: 'Переместить',
    gallery_move_modal_title: 'Переместить выбранное',
    gallery_move_modal_hint: 'Выберите папку назначения для отмеченных файлов.',
    gallery_move_modal_folder_label: 'Папка назначения',
    gallery_move_modal_confirm: 'Подтвердить',
    gallery_folder_rename: 'Переименовать папку',
    gallery_folder_rename_prompt: 'Новое имя папки',
    gallery_folder_renamed: 'Папка переименована',
    gallery_folder_rename_err: 'Не удалось переименовать',
    gallery_folder_delete: 'Удалить папку',
    gallery_folder_delete_confirm: 'Удалить папку «{name}»? Файлы останутся на складе (без привязки к папке).',
    gallery_folder_deleted: 'Папка удалена',
    gallery_folder_delete_err: 'Не удалось удалить папку',
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
    settings_schedule_time_h: 'Расписание и часы',
    settings_timezone_label: 'Часовой пояс',
    settings_ntp_server_label: 'NTP-сервер',
    settings_ntp_sync_btn: 'Синхронизировать время',
    settings_ntp_syncing: 'Синхронизация…',
    settings_ntp_sync_ok: 'Синхронизация времени выполнена',
    settings_ntp_sync_partial: 'NTP недоступен (best-effort)',
    settings_ntp_sync_err: 'Не удалось синхронизировать время',
    settings_schedule_time_hint: 'Сетка расписания использует этот часовой пояс. NTP — по возможности, может требовать sudo.',
    settings_schedule_time_save_err: 'Не удалось сохранить настройки времени',
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
    tab_playlists: 'Плейлисты',
    tab_schedule: 'Расписание',
    playback_source_heading: 'Источник вещания',
    playback_source_idle: 'Простой',
    playback_source_schedule: 'По расписанию',
    playback_source_manual: 'Ручной режим',
    playback_source_override: 'Переопределение',
    return_to_schedule: 'Вернуться к расписанию',
    schedule_today: 'Сегодня',
    schedule_time_col: 'Время',
    schedule_legend_live: 'В эфире',
    schedule_legend_planned: 'Запланирован',
    schedule_legend_conflict: 'Конфликт',
    schedule_legend_archived: 'Архив',
    schedule_panel_edit: 'Редактировать слот',
    schedule_panel_create: 'Новый слот',
    schedule_field_playlist: 'Плейлист',
    schedule_field_days: 'Дни недели',
    schedule_field_start: 'Начало',
    schedule_field_end: 'Окончание',
    schedule_field_repeat: 'Повторение',
    schedule_repeat_once: 'Разово',
    schedule_repeat_weekly: 'Еженедельно',
    schedule_repeat_monthly: 'Ежемесячно',
    schedule_view_week: 'Неделя',
    schedule_view_month: 'Месяц',
    schedule_ctx_skip_day: 'Пропустить этот день',
    schedule_skip_day_ok: 'Этот день пропущен',
    schedule_month_more: 'ещё',
    schedule_field_valid_from: 'Активен с',
    schedule_field_valid_until: 'Активен до',
    schedule_field_valid_until_hint: 'Оставьте пустым для бессрочного.',
    schedule_field_priority: 'Приоритет при конфликте',
    schedule_priority_high: '1 — Высокий (перебивает)',
    schedule_priority_low: '10 — Низкий (уступает)',
    schedule_priority_tier_high: 'Высокий приоритет',
    schedule_priority_tier_mid: 'Средний приоритет',
    schedule_priority_tier_low: 'Низкий приоритет',
    schedule_conflict_title: 'Конфликт расписания',
    schedule_conflict_body: 'В это же время запланирован:',
    schedule_btn_archive: 'В архив',
    schedule_ctx_edit: 'Редактировать',
    schedule_ctx_toggle: 'Приостановить / возобновить',
    schedule_ctx_archive: 'В архив',
    schedule_day_dialog_title: 'Перенос на другой день',
    schedule_day_dialog_body: 'Слот входит в повторяющееся правило. Что сделать?',
    schedule_day_duplicate: 'Дублировать',
    schedule_day_transfer: 'Перенести',
    schedule_day_mon: 'Пн',
    schedule_day_tue: 'Вт',
    schedule_day_wed: 'Ср',
    schedule_day_thu: 'Чт',
    schedule_day_fri: 'Пт',
    schedule_day_sat: 'Сб',
    schedule_day_sun: 'Вс',
    schedule_badge_archived: 'Архив',
    schedule_saved: 'Расписание сохранено',
    schedule_archived: 'Слот отправлен в архив',
    schedule_toggled: 'Статус слота изменён',
    schedule_no_playlists: 'Нет плейлистов',
    schedule_err_days: 'Выберите хотя бы один день',
    schedule_err_time: 'Время окончания должно быть позже начала',
    schedule_err_playlist: 'Выберите плейлист',
    btn_save: 'Сохранить',
    return_to_schedule_ok: 'Воспроизведение возвращено к расписанию',
    return_to_schedule_err: 'Не удалось вернуться к расписанию',
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
    playlist_lead: 'Row order is playback order. Use «Add media» to add files from the warehouse; remove a row with «Remove».',
    pl_col_num: '#',
    pl_col_include: 'Include',
    pl_col_remove: 'Remove',
    pl_row_remove_aria: 'Remove from playlist',
    pl_col_preview: 'Preview',
    pl_col_filename: 'File name',
    pl_col_mute: 'Mute',
    pl_col_duration: 'Duration (sec)',
    playlist_empty: 'No media files found. Add files to the media folder first.',
    playlist_editor_empty: 'This playlist is empty. Click «Add media» to pick files from the warehouse.',
    playlist_save_success: 'Playlist saved. Returning to home…',
    playlist_save_cleared: 'Empty playlist saved. Returning to home…',
    btn_save_playlist: 'Save playlist',
    btn_add_media: 'Add media',
    pl_modal_title: 'Add media to playlist',
    pl_modal_filter: 'Folder',
    pl_modal_cart: 'Cart',
    pl_modal_cart_items: 'items',
    pl_modal_add_to_pl: 'Add to playlist',
    pl_modal_no_files: 'No files for this filter',
    pl_filter_all: 'All media',
    pl_filter_unsorted: 'Unsorted',
    pl_already_in_pl: 'Already in playlist',
    pl_in_cart: 'In cart',
    pl_cart_remove: 'Remove from cart',
    pl_cart_empty: 'The cart is empty',
    pl_append_ok: 'Appended to playlist',
    pl_append_skipped: 'skipped',
    btn_cancel: 'Cancel',
    saving_ellipsis: 'Saving…',
    pl_video_full: 'Full video',
    gallery_toolbar_aria: 'Gallery actions',
    playlist_col_preview: 'Preview',
    playlist_col_name: 'Name',
    playlist_col_customer: 'Customer',
    playlist_col_files: 'Files',
    playlist_col_status: 'Status',
    playlist_col_actions: 'Actions',
    playlists_heading: 'Playlists',
    btn_new_playlist: 'New Playlist',
    current_settings: 'Current Settings',
    now_on_screen: 'Now on screen',
    system_panel: 'System',
    waiting_screen: 'Waiting screen',
    waiting_screen_hint: 'Shown when idle',
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
    replace_logo: 'Replace',
    no_preview: 'No preview',
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
    metric_services_title: 'System services',
    service_digital_signage_short: 'DGS',
    service_digital_signage_full: 'digital-signage.service',
    service_mpv_short: 'MPV',
    service_mpv_full: 'dsign-mpv.service',
    service_network_assistant_short: 'NTA',
    service_network_assistant_full: 'dsign-network-assistant.service',
    service_screenshot_short: 'SCS',
    service_screenshot_full: 'screenshot.service',
    os_linux_short: 'OSL',
    os_linux_full: 'Linux OS',
    network_assist_toggle_off: 'OFF',
    network_assist_toggle_auto: 'OFL',
    network_assist_toggle_force: 'ALW',
    network_assistant_boot_tooltip: 'Wi‑Fi assist at boot: OFF — IP only; OFL — nmtui when offline; ALW — always show nmtui.',
    restart_service: 'Restart service',
    reboot_system: 'Reboot system',
    svc_status_active: 'Active',
    svc_status_dead: 'Dead',
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
    gallery_folders_title: 'Folders & view',
    gallery_view_by_folder: 'By folder',
    gallery_new_folder_ph: 'New folder name',
    gallery_create_folder: 'Create folder',
    gallery_folder_label: 'Folder',
    gallery_folder_created: 'Folder created',
    gallery_folder_create_err: 'Could not create folder',
    gallery_new_folder_card_title: 'New folder',
    gallery_folders_list_title: 'Folders',
    gallery_bulk_move_label: 'Move to',
    gallery_bulk_move_card_title: 'Move selection',
    gallery_bulk_move_btn: 'Move selected',
    gallery_bulk_moved: 'Moved: {n}',
    gallery_bulk_move_err: 'Move failed',
    gallery_bulk_move_none: 'Select files with checkboxes first',
    gallery_toolbar_move: 'Move',
    gallery_move_modal_title: 'Move selected items',
    gallery_move_modal_hint: 'Choose the destination folder for the selected files.',
    gallery_move_modal_folder_label: 'Destination folder',
    gallery_move_modal_confirm: 'Confirm',
    gallery_folder_rename: 'Rename folder',
    gallery_folder_rename_prompt: 'New folder name',
    gallery_folder_renamed: 'Folder renamed',
    gallery_folder_rename_err: 'Rename failed',
    gallery_folder_delete: 'Delete folder',
    gallery_folder_delete_confirm: 'Delete folder «{name}»? Files stay in the warehouse (unassigned from this folder).',
    gallery_folder_deleted: 'Folder deleted',
    gallery_folder_delete_err: 'Delete folder failed',
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
    settings_schedule_time_h: 'Schedule & clock',
    settings_timezone_label: 'Timezone',
    settings_ntp_server_label: 'NTP server',
    settings_ntp_sync_btn: 'Sync time',
    settings_ntp_syncing: 'Syncing…',
    settings_ntp_sync_ok: 'Time sync completed',
    settings_ntp_sync_partial: 'NTP unavailable (best-effort)',
    settings_ntp_sync_err: 'Could not sync time',
    settings_schedule_time_hint: 'Schedule grid uses this timezone. NTP sync is best-effort and may require sudo.',
    settings_schedule_time_save_err: 'Could not save time settings',
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
    tab_playlists: 'Playlists',
    tab_schedule: 'Schedule',
    playback_source_heading: 'Playback source',
    playback_source_idle: 'Idle',
    playback_source_schedule: 'On schedule',
    playback_source_manual: 'Manual',
    playback_source_override: 'Override',
    return_to_schedule: 'Return to schedule',
    schedule_today: 'Today',
    schedule_time_col: 'Time',
    schedule_legend_live: 'On air',
    schedule_legend_planned: 'Scheduled',
    schedule_legend_conflict: 'Conflict',
    schedule_legend_archived: 'Archived',
    schedule_panel_edit: 'Edit slot',
    schedule_panel_create: 'New slot',
    schedule_field_playlist: 'Playlist',
    schedule_field_days: 'Days of week',
    schedule_field_start: 'Start',
    schedule_field_end: 'End',
    schedule_field_repeat: 'Repeat',
    schedule_repeat_once: 'Once',
    schedule_repeat_weekly: 'Weekly',
    schedule_repeat_monthly: 'Monthly',
    schedule_view_week: 'Week',
    schedule_view_month: 'Month',
    schedule_ctx_skip_day: 'Skip this day',
    schedule_skip_day_ok: 'This day skipped',
    schedule_month_more: 'more',
    schedule_field_valid_from: 'Active from',
    schedule_field_valid_until: 'Active until',
    schedule_field_valid_until_hint: 'Leave empty for no end date.',
    schedule_field_priority: 'Conflict priority',
    schedule_priority_high: '1 — High (wins)',
    schedule_priority_low: '10 — Low (yields)',
    schedule_priority_tier_high: 'High priority',
    schedule_priority_tier_mid: 'Medium priority',
    schedule_priority_tier_low: 'Low priority',
    schedule_conflict_title: 'Schedule conflict',
    schedule_conflict_body: 'Overlaps with:',
    schedule_btn_archive: 'Archive',
    schedule_ctx_edit: 'Edit',
    schedule_ctx_toggle: 'Pause / resume',
    schedule_ctx_archive: 'Archive',
    schedule_day_dialog_title: 'Move to another day',
    schedule_day_dialog_body: 'This slot belongs to a recurring rule. What should happen?',
    schedule_day_duplicate: 'Duplicate',
    schedule_day_transfer: 'Move',
    schedule_day_mon: 'Mon',
    schedule_day_tue: 'Tue',
    schedule_day_wed: 'Wed',
    schedule_day_thu: 'Thu',
    schedule_day_fri: 'Fri',
    schedule_day_sat: 'Sat',
    schedule_day_sun: 'Sun',
    schedule_badge_archived: 'Archived',
    schedule_saved: 'Schedule saved',
    schedule_archived: 'Slot archived',
    schedule_toggled: 'Slot status updated',
    schedule_no_playlists: 'No playlists',
    schedule_err_days: 'Select at least one day',
    schedule_err_time: 'End time must be after start time',
    schedule_err_playlist: 'Select a playlist',
    btn_save: 'Save',
    return_to_schedule_ok: 'Playback returned to schedule',
    return_to_schedule_err: 'Could not return to schedule',
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
