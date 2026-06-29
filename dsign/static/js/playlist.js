import { t, getUiLang, applyI18n } from './i18n.js';
import { AddToPlaylistModal } from './add-to-playlist-modal.js';
import { audioFormatLabel } from './media-tile-ui.js';

const AUDIO_EXTENSIONS = ['mp3', 'wav', 'ogg', 'oga', 'flac', 'm4a', 'aac', 'opus'];

function fileExtLower(filename) {
    const fn = String(filename || '').toLowerCase();
    return fn.includes('.') ? fn.split('.').pop() : '';
}

function isPlaylistAudioFile(file) {
    if (file?.is_audio) return true;
    return AUDIO_EXTENSIONS.includes(fileExtLower(file?.filename));
}

function isPlaylistVideoFile(file) {
    if (file?.is_video) return true;
    if (file?.is_external) return true;
    const fn = String(file?.filename || '');
    if (fn.startsWith('ext-')) return true;
    return ['mp4', 'avi', 'mov', 'mkv', 'webm', 'm4v'].includes(fileExtLower(fn));
}

function isPlaylistTimedMedia(file) {
    return isPlaylistVideoFile(file) || isPlaylistAudioFile(file);
}

// Кэш для превью медиафайлов
const previewCache = new Map();

// Утилитные функции
function getPlaylistId() {
    const params = new URLSearchParams(window.location.search);
    let id = params.get('id') || window.location.pathname.split('/').pop();
    return id && !isNaN(id) ? id : null;
}

function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function toggleButtonState(button, isLoading) {
    if (!button) return;
    button.disabled = isLoading;
    const lang = getUiLang();
    if (isLoading) {
        button.innerHTML = `⏳ <span class="save-playlist__label">${t('saving_ellipsis', lang)}</span>`;
    } else {
        button.innerHTML = `💾 <span class="save-playlist__label" data-i18n="btn_save_playlist">${t('btn_save_playlist', lang)}</span>`;
    }
}

// UI компонент для уведомлений
class PlaylistUI {
    constructor() {
        this.setupStyles();
    }

    setupStyles() {
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes fadeOut {
                to { opacity: 0; transform: translateX(100%); }
            }
        `;
        document.head.appendChild(style);
    }

    showAlert(message, type = 'info', duration = 5000) {
        // Создаем контейнер для уведомлений, если его еще нет
        let alertsContainer = document.getElementById('alerts-container');
        if (!alertsContainer) {
            alertsContainer = document.createElement('div');
            alertsContainer.id = 'alerts-container';
            alertsContainer.style.position = 'fixed';
            alertsContainer.style.top = '20px';
            alertsContainer.style.right = '20px';
            alertsContainer.style.zIndex = '10000';
            alertsContainer.style.maxWidth = '350px';
            alertsContainer.style.width = '100%';
            document.body.appendChild(alertsContainer);
        }

        // Создаем элемент уведомления
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.style.cssText = `
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 8px;
            background: ${this.getAlertColor(type)};
            color: white;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            animation: slideIn 0.3s ease-out forwards;
            display: flex;
            align-items: center;
            justify-content: space-between;
        `;

        // Добавляем иконку в зависимости от типа
        const icons = {
            success: '✓',
            error: '×',
            warning: '!',
            info: 'i'
        };

        const lang = getUiLang();
        const title =
            type === 'error' ? t('alert_error', lang) :
            type === 'success' ? t('alert_success', lang) :
            type === 'warning' ? t('alert_warning', lang) :
            t('alert_info', lang);

        alertDiv.innerHTML = `
            <div style="display: flex; align-items: center; gap: 10px;">
                <span aria-hidden="true" style="font-size: 1.35rem; line-height: 1;">${icons[type] || 'i'}</span>
                <div>
                    <div style="font-weight: bold; margin-bottom: 5px;">${title}</div>
                    <div>${message}</div>
                </div>
            </div>
            <button class="alert-close-btn" style="background: none; border: none; color: white; cursor: pointer;">
                <span aria-hidden="true">×</span>
            </button>
        `;

        // Добавляем уведомление в контейнер
        alertsContainer.prepend(alertDiv);

        // Настраиваем закрытие по клику
        const closeBtn = alertDiv.querySelector('.alert-close-btn');
        closeBtn.addEventListener('click', () => {
            this.closeAlert(alertDiv);
        });

        // Автоматическое закрытие
        setTimeout(() => {
            this.closeAlert(alertDiv);
        }, duration);

        return {
            element: alertDiv,
            close: () => this.closeAlert(alertDiv)
        };
    }

    closeAlert(alertDiv) {
        if (alertDiv.parentNode) {
            alertDiv.style.animation = 'fadeOut 0.3s ease-in forwards';
            setTimeout(() => alertDiv.remove(), 300);
        }
    }

    getAlertColor(type) {
        const colors = {
            success: '#28a745',
            error: '#dc3545',
            warning: '#ffc107',
            info: '#17a2b8'
        };
        return colors[type] || colors.info;
    }
}

// Основной класс плейлиста
export class PlaylistManager {
    constructor() {
        if (window.App?.PlaylistManager) {
            return window.App.PlaylistManager;
        }
        this.playlistId = getPlaylistId();
        this.fileListEl = document.getElementById('file-list');
        this.saveBtn = document.getElementById('save-playlist');
        this.addMediaBtn = document.getElementById('add-media-modal-btn');
        this.exportBtn = document.getElementById('export-m3u');
        this.emptyMessage = document.getElementById('empty-playlist-message');
        this.ui = new PlaylistUI();
        this._thumbObserver = null;
        this._lastFiles = null;

        this.init();
    }

    init() {
        if (!this.fileListEl || !this.saveBtn) {
            console.error('Не найдены необходимые элементы DOM');
            return;
        }

        this.saveBtn.addEventListener('click', () => this.savePlaylist());

        if (this.addMediaBtn && this.playlistId) {
            this._addModal = new AddToPlaylistModal(this.playlistId, {
                getCSRFToken,
                onAppended: () => {
                    sessionStorage.removeItem(`playlist-editor-files-${this.playlistId}`);
                    this.loadMediaFiles();
                },
                showMessage: (msg, type = 'info') => this.ui.showAlert(msg, type),
            });
            this.addMediaBtn.addEventListener('click', () => this._addModal.open());
        }

        if (this.exportBtn) {
            this.exportBtn.addEventListener('click', () => this.exportM3U());
        }
        
        this.loadMediaFiles();
        this.fileListEl.addEventListener('click', (e) => {
            const btn = e.target.closest('.pl-row-remove');
            if (!btn || !this.fileListEl.contains(btn)) return;
            e.preventDefault();
            const tr = btn.closest('tr');
            tr?.remove();
            this._renumberPlaylistRows();
            sessionStorage.removeItem(`playlist-editor-files-${this.playlistId}`);
        });

        document.addEventListener('dsign:language-changed', () => {
            applyI18n();
            if (this._lastFiles) {
                this.renderFileTable(this._lastFiles);
            }
        });
        
        if (window.App?.Sockets?.socket) {
            window.App.Sockets.socket.on('playlist_updated', (data) => {
                if (data.playlist_id == this.playlistId) {
                    sessionStorage.removeItem(`playlist-editor-files-${this.playlistId}`);
                    this.loadMediaFiles();
                    
                    if (data.m3u_generated) {
                        this.ui.showAlert('M3U файл был автоматически обновлен', 'info');
                    }
                }
            });
        }
    }

    _renumberPlaylistRows() {
        if (!this.fileListEl) return;
        this.fileListEl.querySelectorAll('tr').forEach((tr, i) => {
            const num = tr.querySelector('.playlist-col-num');
            if (num) num.textContent = String(i + 1);
        });
        if (!this.fileListEl.querySelector('tr')) {
            if (this.emptyMessage) this.emptyMessage.style.display = 'block';
        } else if (this.emptyMessage) {
            this.emptyMessage.style.display = 'none';
        }
    }

    // Загрузка медиафайлов с кэшированием
    async loadMediaFiles() {
        if (!this.playlistId) {
            this.ui.showAlert('Неверный ID плейлиста', 'error');
            return;
        }

        try {
            const cacheKey = `playlist-editor-files-${this.playlistId}`;
            const cachedData = sessionStorage.getItem(cacheKey);

            if (cachedData) {
                const cache = JSON.parse(cachedData);
                if (Date.now() - cache.timestamp < 60000) {
                    this.renderFileTable(cache.data.files);
                    return;
                }
            }

            const itemsRes = await fetch(`/api/playlists/${this.playlistId}/items`, {
                headers: { 'Accept': 'application/json' },
                credentials: 'include'
            });

            if (!itemsRes.ok) throw new Error(`Ошибка списка плейлиста: ${itemsRes.status}`);

            const itemsData = await itemsRes.json();
            if (!itemsData?.success) throw new Error(itemsData?.error || 'Неверный ответ items');

            const list = (itemsData.items || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
            const files = list.map((it) => ({
                filename: it.file_name,
                duration: it.duration,
                muted: Boolean(it.muted),
                is_video: Boolean(it.is_video),
                is_audio: Boolean(it.is_audio),
                is_external: Boolean(it.is_external),
            }));

            sessionStorage.setItem(cacheKey, JSON.stringify({
                timestamp: Date.now(),
                data: { files }
            }));
            this.renderFileTable(files);
        } catch (error) {
            console.error('Ошибка загрузки файлов:', error);
            this.ui.showAlert(`Не удалось загрузить медиафайлы: ${error.message}`, 'error');
        }
    }

    // Предпросмотр изображений
    _getPreviewUrl(file) {
        if (isPlaylistAudioFile(file)) {
            return '/static/images/placeholder.jpg';
        }
        return `/api/media/thumbnail/${encodeURIComponent(file.filename)}`;
    }

    _ensureThumbObserver() {
        if (this._thumbObserver) return;

        // Lazy-load thumbnails only when they become visible.
        this._thumbObserver = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                const img = entry.target;
                const src = img.dataset.src;
                if (src && img.src !== src) {
                    img.src = src;
                }
                this._thumbObserver.unobserve(img);
            }
        }, { root: null, rootMargin: '300px 0px', threshold: 0.01 });
    }

    // Рендеринг таблицы файлов
    renderFileTable(files) {
        if (!this.fileListEl) return;
        this._lastFiles = files;

        if (!files || files.length === 0) {
            this.fileListEl.innerHTML = '';
            if (this.emptyMessage) this.emptyMessage.style.display = 'block';
            return;
        }

        if (this.emptyMessage) this.emptyMessage.style.display = 'none';
        this.fileListEl.innerHTML = '';
        this._ensureThumbObserver();
        const lang = getUiLang();

        files.forEach((file, index) => {
            const row = document.createElement('tr');
            row.dataset.filename = file.filename;

            const isVideo = isPlaylistVideoFile(file);
            const isAudio = isPlaylistAudioFile(file);
            const isTimed = isVideo || isAudio;
            row.dataset.isVideo = isVideo ? 'true' : 'false';
            row.dataset.isAudio = isAudio ? 'true' : 'false';

            const numTd = document.createElement('td');
            numTd.className = 'playlist-col-num';
            numTd.textContent = String(index + 1);

            const rmTd = document.createElement('td');
            rmTd.className = 'playlist-col-remove';
            const rmBtn = document.createElement('button');
            rmBtn.type = 'button';
            rmBtn.className = 'pl-row-remove btn secondary';
            rmBtn.setAttribute('aria-label', t('pl_row_remove_aria', lang));
            rmBtn.innerHTML = '<span aria-hidden="true">×</span>';
            rmTd.appendChild(rmBtn);

            const prevTd = document.createElement('td');
            prevTd.className = 'playlist-col-preview';
            const wrap = document.createElement('div');
            wrap.className = 'playlist-thumb-wrap';

            let img = null;
            let previewUrl = '';
            if (isAudio) {
                wrap.classList.add('playlist-thumb-wrap--audio');
                const glyph = document.createElement('span');
                glyph.className = 'playlist-audio-glyph';
                glyph.setAttribute('aria-hidden', 'true');
                glyph.textContent = '♪';
                wrap.appendChild(glyph);
                const fmt = document.createElement('span');
                fmt.className = 'playlist-audio-format';
                fmt.textContent = audioFormatLabel(file.filename);
                wrap.appendChild(fmt);
            } else {
                img = document.createElement('img');
                img.src = '/static/images/default-preview.jpg';
                img.alt = 'Preview';
                img.className = `playlist-thumb-img${isVideo ? ' playlist-thumb-img--video' : ''}`;
                img.dataset.filename = file.filename;
                img.loading = 'lazy';
                img.decoding = 'async';

                previewUrl = this._getPreviewUrl(file);
                img.dataset.src = previewUrl;
                img.dataset.thumbRetries = '0';
                img.onerror = function () {
                    const maxRetries = 6;
                    const cur = parseInt(this.dataset.thumbRetries || '0', 10) || 0;
                    if (cur >= maxRetries) {
                        this.src = '/static/images/default-preview.jpg';
                        return;
                    }
                    this.dataset.thumbRetries = String(cur + 1);
                    const delay = Math.min(15000, Math.round(800 * Math.pow(2, cur)));
                    this.src = '/static/images/default-preview.jpg';
                    setTimeout(() => {
                        const base = this.dataset.src || '';
                        if (!base) return;
                        const joiner = base.includes('?') ? '&' : '?';
                        this.src = `${base}${joiner}r=${Date.now()}`;
                    }, delay);
                };

                wrap.appendChild(img);
            }
            prevTd.appendChild(wrap);

            const nameTd = document.createElement('td');
            nameTd.className = 'playlist-col-name';
            nameTd.textContent = file.filename;

            const muteTd = document.createElement('td');
            muteTd.className = 'playlist-col-mute';
            if (isTimed) {
                const mc = document.createElement('input');
                mc.type = 'checkbox';
                mc.className = 'mute-checkbox';
                mc.dataset.filename = file.filename;
                if (file.muted) mc.checked = true;
                muteTd.appendChild(mc);
            } else {
                const span = document.createElement('span');
                span.className = 'playlist-video-hint';
                span.textContent = '—';
                muteTd.appendChild(span);
            }

            const durTd = document.createElement('td');
            durTd.className = 'playlist-col-duration';
            const videoFullLabel = t('pl_video_full', lang);
            const imageSeconds = (() => {
                const d = file.duration;
                if (d != null && d !== '') {
                    const n = parseInt(String(d), 10);
                    if (Number.isFinite(n) && n >= 1) return n;
                }
                return 10;
            })();
            if (isTimed) {
                const span = document.createElement('span');
                span.className = 'playlist-video-hint';
                span.textContent = videoFullLabel;
                durTd.appendChild(span);
            } else {
                const inp = document.createElement('input');
                inp.type = 'number';
                inp.className = 'duration-input';
                inp.dataset.filename = file.filename;
                inp.value = String(imageSeconds);
                inp.min = '1';
                durTd.appendChild(inp);
            }

            row.appendChild(numTd);
            row.appendChild(rmTd);
            row.appendChild(prevTd);
            row.appendChild(nameTd);
            row.appendChild(muteTd);
            row.appendChild(durTd);
            this.fileListEl.appendChild(row);

            if (img) {
                if (index < 24) {
                    img.src = previewUrl;
                } else if (this._thumbObserver) {
                    this._thumbObserver.observe(img);
                } else {
                    img.src = previewUrl;
                }
            }
        });
    }

    // Сохранение плейлиста с генерацией M3U
    async savePlaylist() {
        if (!this.playlistId) {
            this.ui.showAlert('Неверный ID плейлиста', 'error');
            return;
        }

        toggleButtonState(this.saveBtn, true);

        try {
            const rows = Array.from(document.querySelectorAll('#file-list tr'));
            const selectedFiles = [];
            let hasErrors = false;
            let selectedOrder = 0;
            const lang = getUiLang();

            for (const [index, row] of rows.entries()) {
                try {
                    const filename = row.dataset.filename;
                    if (!filename || typeof filename !== 'string') {
                        throw new Error(`Некорректное имя файла в строке ${index + 1}`);
                    }

                    const lower = String(filename || '').toLowerCase();
                    const isExternal = lower.startsWith('ext-');
                    const fileExt = lower.includes('.') ? lower.split('.').pop() : '';
                    const isVideo =
                        row.dataset.isVideo === 'true' ||
                        isExternal ||
                        ['mp4', 'avi', 'mov', 'mkv', 'webm', 'm4v'].includes(fileExt);
                    const isAudio =
                        row.dataset.isAudio === 'true' ||
                        AUDIO_EXTENSIONS.includes(fileExt);
                    const isTimed = isVideo || isAudio;

                    let duration = 10;
                    if (!isTimed) {
                        const durationInput = row.querySelector('.duration-input');
                        duration = Math.max(1, parseInt(durationInput?.value || 10));
                        
                        if (isNaN(duration)) {
                            throw new Error(`Некорректная длительность для файла ${filename}`);
                        }
                    }

                    selectedFiles.push({
                        file_name: filename,
                        duration: isTimed ? 0 : duration,
                        muted: isTimed ? Boolean(row.querySelector('.mute-checkbox')?.checked) : false,
                        order: (selectedOrder += 1)
                    });

                } catch (error) {
                    console.error(`Ошибка обработки файла: ${error.message}`);
                    this.ui.showAlert(error.message, 'warning');
                    hasErrors = true;
                }
            }

            if (hasErrors) {
                throw new Error('Обнаружены ошибки в данных файлов');
            }

            const response = await fetch(`/api/playlists/${this.playlistId}/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({
                    files: selectedFiles,
                    meta: {
                        generate_m3u: true
                    }
                })
            });

            const result = await response.json();
            
            if (!response.ok || !result.success) {
                const errorMsg = result.error || `HTTP error ${response.status}`;
                
                if (errorMsg.includes('file_name') || errorMsg.includes('invalid')) {
                    throw new Error('Ошибка данных. Пожалуйста, обновите страницу и попробуйте снова.');
                }
                throw new Error(errorMsg);
            }

            this.ui.showAlert(
                selectedFiles.length ? t('playlist_save_success', lang) : t('playlist_save_cleared', lang),
                'success'
            );
            sessionStorage.removeItem(`playlist-editor-files-${this.playlistId}`);

            if (window.App?.Sockets) {
                window.App.Sockets.emit('playlist_updated', {
                    playlist_id: this.playlistId,
                    updated_files: selectedFiles.length,
                    m3u_generated: true
                });
            }

            setTimeout(() => {
                window.location.href = '/';
            }, 400);

        } catch (error) {
            console.error('Ошибка сохранения:', error);
            
            let errorMessage = error.message;
            if (error.message.includes('недостаточно места')) {
                errorMessage = 'Недостаточно места на сервере';
            } else if (error.message.includes('validation')) {
                errorMessage = 'Ошибка валидации данных';
            }
            
            this.ui.showAlert(errorMessage || 'Не удалось сохранить плейлист', 'error');

        } finally {
            toggleButtonState(this.saveBtn, false);
        }
    }

    // Экспорт M3U
    async exportM3U() {
        if (!this.playlistId) return;
        
        try {
            const response = await fetch(`/api/playlists/${this.playlistId}/export-m3u`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCSRFToken()
                }
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.ui.showAlert(`M3U файл успешно экспортирован: ${result.filename}`, 'success');
            } else {
                throw new Error(result.error || 'Ошибка экспорта');
            }
        } catch (error) {
            console.error('Ошибка экспорта:', error);
            this.ui.showAlert(error.message || 'Не удалось экспортировать M3U', 'error');
        }
    }
}

// Инициализация при загрузке DOM
document.addEventListener('DOMContentLoaded', () => {
    window.App = window.App || {};
    if (window.App.PlaylistManager) return;
    const playlistManager = new PlaylistManager();
    
    // Для обратной совместимости
    window.App.PlaylistManager = playlistManager;
}, { once: true });

export default PlaylistManager;
