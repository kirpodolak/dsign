import { showAlert, showError } from './utils/alerts.js';
import { toggleButtonState } from './utils/helpers.js';
import { fetchAPI, getCSRFToken } from './utils/api.js';
import { t, getUiLang } from './i18n.js';
import { initSchedule, showScheduleView, hideScheduleView, refreshScheduleIfVisible } from './schedule.js';

function setBtnIconText(btn, text) {
    if (!btn) return;
    let el = btn.querySelector('.btn-icon');
    if (!el) {
        el = document.createElement('span');
        el.className = 'btn-icon';
        el.setAttribute('aria-hidden', 'true');
        btn.prepend(el);
    }
    el.textContent = String(text || '');
}

// Application configuration
const CONFIG = {
    api: {
        baseUrl: '',
        endpoints: {
            settings: '/api/settings/current',
            playlists: '/api/playlists',
            playback: '/api/playback',
            scheduleWeek: '/api/schedule/week',
            returnToSchedule: '/api/playback/return-to-schedule',
            systemStatus: '/api/system/status',
            networkStatus: '/api/system/network/status',
            uploadLogo: '/api/media/upload_logo',
            media: '/api/media/files',
            mediaUpload: '/api/media/upload',
            serveMedia: '/api/media',
            previewImage: '/api/media/mpv_screenshot'
        },
        headers: {
            'Accept': 'application/json',
            'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content ||
                          document.cookie.match(/csrf_token=([^;]+)/)?.[1] || ''
        }
    },
    selectors: {
        playlistCards: '#playlist-cards',
        createPlaylistBtn: '#create-playlist-btn',
        modal: '#create-playlist-modal',
        modalClose: '.modal .close',
        playlistForm: '#create-playlist-form',
        statusIndicator: '#playlist-status',
        logoReplaceBtn: '#logo-replace-btn',
        logoForm: '#logo-upload-form',
        settingsPanel: '#settings-container',
        logoImage: '#idle-logo',
        previewImage: '#mpv-preview-image',
        mpvPreviewPlaceholder: '#mpv-preview-placeholder',
        nowScreenTitle: '#now-screen-title',
        currentSettings: '#current-settings',
        loadingIndicator: '#loading-indicator',
        logoFileInput: '#logo-upload',
        refreshPreviewBtn: '#refresh-mpv-preview',
        mpvLastUpdate: '#mpv-last-update',
        viewTabs: '.home-view-tab',
        viewPlaylists: '#view-playlists',
        viewSchedule: '#view-schedule',
        playbackSourceBadge: '#playback-source-badge',
        playbackSourceLabel: '#playback-source-label',
        returnToScheduleBtn: '#return-to-schedule-btn',
    },
    defaultLogo: '/static/images/default-logo.jpg',
    defaultPreview: '/static/images/default-preview.jpg',
    // Polling cadence. Playing used 2s × 4 endpoints → noisy logs and IPC contention; 5s is enough for UI.
    refreshIntervalActiveMs: 5000,
    refreshIntervalIdleMs: 30000,
    // Refresh heavy /settings/current only occasionally during auto-poll (playback/system/network stay fresher).
    settingsPollEveryNTicks: 6,
    // When we have socket push, polling becomes a slow safety net only.
    refreshIntervalSocketFallbackMs: 60000,
    previewRefreshInterval: 15000,
    maxImageLoadAttempts: 3
};

// Initialize DOM elements
const elements = Object.fromEntries(
    Object.entries(CONFIG.selectors)
        .map(([key, selector]) => [key, document.querySelector(selector)])
);

function sortPlaylistsByOrder(playlists) {
    return [...(playlists || [])].sort((a, b) => {
        const ao = Number(a.sort_order ?? 0);
        const bo = Number(b.sort_order ?? 0);
        if (ao !== bo) return ao - bo;
        return Number(a.id) - Number(b.id);
    });
}

function playlistsFromDomOrder(orderIds, playlists) {
    const byId = new Map((playlists || []).map((p) => [Number(p.id), p]));
    return orderIds.map((id) => byId.get(id)).filter(Boolean);
}

// Application state
const state = {
    playlists: [],
    currentSettings: {},
    playbackStatus: null,
    refreshIntervalId: null,
    previewRefreshId: null,
    logoLoadAttempts: 0,
    previewLoadAttempts: 0,
    fallbackLogoUsed: false,
    fallbackPreviewUsed: false,
    isPreviewRefreshing: false,
    previewCaptureCooldownUntil: 0,
    sockets: null,
    usingSocketPush: false,
    refreshTimerId: null,
    refreshInFlight: false,
    refreshBackoffMs: 0,
    autoRefreshTickCount: 0,
    lastMpvPreviewRefreshAt: 0,
    mpvPreviewRequestId: 0,
    lastAppliedPlaybackKey: '',
    activeHomeView: 'playlists',
};

// API functions
const api = {
    async request(url, options = {}) {
        const { showLoading = true, ...fetchOptions } = options || {};
        try {
            // Avoid blocking UI for background polling / image refreshes.
            if (showLoading && elements.loadingIndicator) {
                elements.loadingIndicator.style.display = 'block';
            }

            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || 
                            document.cookie.match(/csrf_token=([^;]+)/)?.[1] || 
                            '';

            const method = (fetchOptions.method || 'GET').toUpperCase();
            const mergedHeaders = {
                ...CONFIG.api.headers,
                'X-CSRFToken': csrfToken,
                ...(fetchOptions.headers || {})
            };
            if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
                if (!mergedHeaders['Content-Type'] && !mergedHeaders['content-type']) {
                    mergedHeaders['Content-Type'] = 'application/json';
                }
            } else {
                delete mergedHeaders['Content-Type'];
                delete mergedHeaders['content-type'];
            }

            const response = await fetch(`${CONFIG.api.baseUrl}${url}`, {
                ...fetchOptions,
                method,
                headers: mergedHeaders,
                credentials: 'include' // Ensure cookies are sent with requests
            });

            if (response.status === 401) {
                const path = window.location.pathname || '';
                if (!path.includes('/api/auth/login')) {
                    const next = encodeURIComponent(path + window.location.search);
                    window.location.href = `/api/auth/login?next=${next}`;
                }
                throw new Error('Authentication required');
            }

            if (!response.ok) {
                let errorDetails = '';
                try {
                    const errorResponse = await response.json();
                    errorDetails = errorResponse.message || JSON.stringify(errorResponse);
                } catch (e) {
                    errorDetails = await response.text();
                }
                
                const error = new Error(`HTTP error! status: ${response.status}. Details: ${errorDetails}`);
                error.status = response.status;
                error.details = errorDetails;
                throw error;
            }

            return await response.json();
        } catch (error) {
            console.error(`API request failed: ${url}`, error);
            throw error;
        } finally {
            if (showLoading && elements.loadingIndicator) {
                elements.loadingIndicator.style.display = 'none';
            }
        }
    },

    async getSettings() {
        const resp = await this.request(CONFIG.api.endpoints.settings);
        // API may return wrapper: { success, settings, profile }
        if (resp && typeof resp === 'object' && !Array.isArray(resp) && resp.settings) {
            return resp.settings;
        }
        return resp;
    },

    async getPlaylists() {
        const response = await this.request(CONFIG.api.endpoints.playlists);
        const playlists = Array.isArray(response) ? response : (response.playlists || []);
        // Ensure customer is always a string (even empty)
        return sortPlaylistsByOrder(playlists.map(playlist => ({
            ...playlist,
            customer: playlist.customer || ''
        })));
    },

    async reorderPlaylists(order) {
        return this.request(`${CONFIG.api.endpoints.playlists}/reorder`, {
            method: 'POST',
            body: JSON.stringify({ order })
        });
    },

    async createPlaylist(data) {
        const response = await this.request(CONFIG.api.endpoints.playlists, {
            method: 'POST',
            body: JSON.stringify({
                ...data,
                customer: data.customer || '' // Always send string
            })
        });
        return response;
    },

    async deletePlaylist(id) {
        return this.request(`${CONFIG.api.endpoints.playlists}/${id}`, {
            method: 'DELETE'
        });
    },

    async startPlayback(playlistId) {
        return this.request(`${CONFIG.api.endpoints.playback}/play`, {
            method: 'POST',
            body: JSON.stringify({ playlist_id: playlistId })
        });
    },

    async stopPlayback() {
        return this.request(`${CONFIG.api.endpoints.playback}/stop`, {
            method: 'POST'
        });
    },

    async getPlaybackStatus() {
        return this.request(`${CONFIG.api.endpoints.playback}/status`, { showLoading: false });
    },

    async returnToSchedule() {
        return this.request(CONFIG.api.endpoints.returnToSchedule, { method: 'POST' });
    },

    async getSystemStatus() {
        return this.request(CONFIG.api.endpoints.systemStatus, { showLoading: false });
    },

    async getNetworkStatus() {
        return this.request(CONFIG.api.endpoints.networkStatus, { showLoading: false });
    },

    async uploadLogo(formData) {
        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || 
                            document.cookie.match(/csrf_token=([^;]+)/)?.[1] || '';
            
            const response = await fetch(`${CONFIG.api.baseUrl}${CONFIG.api.endpoints.uploadLogo}`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken
                },
                body: formData,
                credentials: 'include'
            });

            if (!response.ok) {
                let errorDetails = '';
                try {
                    const errorResponse = await response.json();
                    errorDetails = errorResponse.message || JSON.stringify(errorResponse);
                } catch (e) {
                    errorDetails = await response.text();
                }
                
                throw new Error(`Logo upload failed: ${errorDetails}`);
            }

            const result = await response.json();
            
            // Update application state
            state.fallbackLogoUsed = false;
            state.logoLoadAttempts = 0;
            
            return result;
        } catch (error) {
            console.error('Logo upload error:', error);
            showError(`Failed to upload logo: ${error.message}`);
            throw error;
        }
    },

    async uploadMediaFiles(formData) {
        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || 
                            document.cookie.match(/csrf_token=([^;]+)/)?.[1] || 
                            '';
            
            const response = await fetch(`${CONFIG.api.baseUrl}${CONFIG.api.endpoints.mediaUpload}`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken
                },
                body: formData,
                credentials: 'include'
            });

            if (!response.ok) {
                let errorDetails = '';
                try {
                    const errorResponse = await response.json();
                    errorDetails = errorResponse.message || JSON.stringify(errorResponse);
                } catch (e) {
                    errorDetails = await response.text();
                }
                throw new Error(`Media upload failed: ${errorDetails}`);
            }

            return await response.json();
        } catch (error) {
            console.error('Media upload error:', error);
            throw error;
        }
    },

    async refreshPreview() {
        try {
            state.isPreviewRefreshing = true;
            const result = await this.request(`${CONFIG.api.endpoints.previewImage}/capture`, {
                method: 'POST',
                headers: {
                    // Explicitly mark this as a user-initiated capture (manual click).
                    // Server may reject background/implicit captures when Auto preview is Off.
                    'X-DSIGN-Preview-Intent': 'manual'
                }
            });
            return result && typeof result === 'object' ? result : { success: true };
        } catch (error) {
            console.warn('Preview refresh failed:', error);
            return { success: false, error };
        } finally {
            state.isPreviewRefreshing = false;
        }
    }
};

// UI functions
const ui = {
    showAlert(message, type = 'info', duration = 3000) {
        showAlert(message, type, duration);
    },

    getAlertColor(type) {
        const colors = {
            success: '#28a745',
            error: '#dc3545',
            warning: '#ffc107',
            info: '#17a2b8'
        };
        return colors[type] || colors.info;
    },

    updateStatus(message, type = 'info') {
        if (elements.statusIndicator) {
            elements.statusIndicator.textContent = message;
            elements.statusIndicator.className = `status-${type}`;
        }
    },

    updatePreviewAutoStatus() {
        /* Auto-preview hint removed from home UI */
    },

    _metricBarHtml(percent) {
        const safe = this._clampPercent(percent);
        if (safe === null) return '';
        const barClass = this._barClass(safe);
        const fillClass = barClass === 'is-danger' ? 'is-danger' : (barClass === 'is-warn' ? 'is-warn' : '');
        return `<div class="metric-bar"><div class="metric-bar-fill ${fillClass}" style="width:${Math.round(safe)}%;"></div></div>`;
    },

    _clampPercent(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) return null;
        return Math.max(0, Math.min(100, num));
    },

    _barClass(percent) {
        if (percent === null) return 'is-ok';
        if (percent < 50) return 'is-ok';
        if (percent < 80) return 'is-warn';
        return 'is-danger';
    },

    _formatBytes(bytes) {
        const value = Number(bytes);
        if (!Number.isFinite(value) || value < 0) return t('value_na', getUiLang());
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let idx = 0;
        let size = value;
        while (size >= 1024 && idx < units.length - 1) {
            size /= 1024;
            idx += 1;
        }
        const precision = idx <= 1 ? 0 : 1;
        return `${size.toFixed(precision)} ${units[idx]}`;
    },

    _truncateText(input, maxLen = 28) {
        const text = String(input ?? '').trim();
        if (!text) return '';
        if (text.length <= maxLen) return text;
        return `${text.slice(0, maxLen - 3)}...`;
    },

    thumbnailUrl(filename) {
        if (!filename) return '';
        return `/api/media/thumbnail/${encodeURIComponent(filename)}`;
    },

    updateNowOnScreen(broadcastRaw) {
        const titleEl = elements.nowScreenTitle;
        if (titleEl) {
            titleEl.textContent = this._truncateText(broadcastRaw, 64) || '—';
        }
    },

    _nowScreenTitle(playbackStatus, playlists) {
        const lang = getUiLang();
        const st = String(playbackStatus?.status || '').toLowerCase();
        if (st === 'playing') {
            const rawPid = playbackStatus?.playlist_id;
            const active = (playlists || []).find((item) => String(item.id) === String(rawPid));
            return active?.name || `Playlist #${rawPid ?? ''}`.trim() || t('unnamed', lang);
        }
        return t('status_idle', lang);
    },

    _cardActionIcons() {
        return {
            play: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M9 5v14l12-7-12-7z" fill="currentColor"/></svg>',
            stop: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M6 6h12v12H6z" fill="currentColor"/></svg>',
            edit: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" fill="currentColor"/></svg>',
            del: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" fill="currentColor"/></svg>',
        };
    },

    formatCardMeta(customer, filesCount, lang) {
        const cust = String(customer ?? '').trim() || '—';
        const files = this.formatFilesCount(filesCount, lang);
        if (lang === 'ru') {
            return `Заказчик: ${cust} • ${files}`;
        }
        return `Customer: ${cust} • ${files}`;
    },

    formatFilesCount(count, lang) {
        const n = Number(count) || 0;
        if (lang === 'ru') {
            const mod10 = n % 10;
            const mod100 = n % 100;
            if (mod10 === 1 && mod100 !== 11) return `${n} файл`;
            if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} файла`;
            return `${n} файлов`;
        }
        return n === 1 ? `${n} file` : `${n} files`;
    },

    _activePlaylist(runtime = {}) {
        const playlists = Array.isArray(runtime.playlists) ? runtime.playlists : [];
        const playbackStatus = runtime.playbackStatus || {};
        const rawPid = playbackStatus?.playlist_id;
        if (rawPid === null || rawPid === undefined || rawPid === '') return null;
        return playlists.find((item) => String(item.id) === String(rawPid)) || null;
    },

    _renderMetricBar(percent) {
        if (percent === null) return '';
        const safePercent = this._clampPercent(percent);
        if (safePercent === null) return '';
        return `
            <div class="metric-bar">
                <div class="metric-bar__fill ${this._barClass(safePercent)}" style="width: ${safePercent}%;"></div>
            </div>
        `;
    },

    _resolveScreenValue(settings, systemStatus = {}) {
        const actualMode = String(systemStatus?.display?.current_resolution || '').trim();
        if (actualMode) return actualMode;
        const preset = String(settings?.display?.hdmi_mode_preset || '').trim().toLowerCase();
        if (preset === '1080p60') return '1920x1080';
        if (preset === '4k30') return '3840x2160';
        const explicitResolution = String(settings?.resolution || '').trim();
        if (explicitResolution) return explicitResolution;
        return preset === 'auto' ? t('value_auto', getUiLang()) : t('value_na', getUiLang());
    },

    renderSettings(settings, runtime = {}) {
        if (!elements.settingsPanel) return;
        const lang = getUiLang();

        const playlists = Array.isArray(runtime.playlists) ? runtime.playlists : [];
        const playbackStatus = runtime.playbackStatus || {};
        const systemStatus = runtime.systemStatus || {};
        const networkStatus = runtime.networkStatus || {};

        const screenResolution = this._resolveScreenValue(settings, systemStatus);

        const systemAudio = systemStatus?.audio || {};
        const audioAvailable = Boolean(systemAudio?.available);
        const systemMuted = systemAudio?.muted;
        const settingsMuted = settings?.mute;
        const isMuted = typeof systemMuted === 'boolean'
            ? systemMuted
            : (typeof settingsMuted === 'boolean' ? settingsMuted : false);
        const systemVolumeNum = Number(systemAudio?.volume_percent);
        const settingsVolumeNum = Number(settings?.volume);
        const volumeNum = audioAvailable && Number.isFinite(systemVolumeNum)
            ? systemVolumeNum
            : (Number.isFinite(settingsVolumeNum) ? settingsVolumeNum : null);
        const volumeValue = isMuted
            ? t('value_mute', lang)
            : (volumeNum !== null ? `${Math.max(0, Math.min(100, Math.round(volumeNum)))}%` : t('value_na', lang));

        this.updateNowOnScreen(this._nowScreenTitle(playbackStatus, playlists));

        const storageData = systemStatus?.storage?.media || systemStatus?.storage?.root || null;
        const storagePercent = this._clampPercent(storageData?.used_percent);
        const storagePercentText = storagePercent !== null ? `${Math.round(storagePercent)}%` : t('value_na', lang);
        const storageSubLabel = storageData
            ? `${this._formatBytes(storageData.used)} / ${this._formatBytes(storageData.total)}`
            : '';

        const cpuTempRaw = Number(systemStatus?.cpu?.temp_c);
        const cpuTemp = Number.isFinite(cpuTempRaw) ? cpuTempRaw : null;
        const cpuTempValue = cpuTemp === null ? t('value_na', lang) : `${cpuTemp.toFixed(1)}°C`;

        const cpuLoadRaw = Number(systemStatus?.cpu?.usage_percent ?? systemStatus?.cpu?.load_percent);
        const cpuLoad = Number.isFinite(cpuLoadRaw) ? this._clampPercent(cpuLoadRaw) : null;
        const cpuLoadValue = cpuLoad === null ? t('value_na', lang) : `${cpuLoad.toFixed(1)}%`;

        const transcodeEnabledRaw = settings?.display?.auto_transcode_videos;
        const transcodeEnabled = transcodeEnabledRaw === true || String(transcodeEnabledRaw).toLowerCase() === 'true';
        const transcodeValue = transcodeEnabled ? t('transcode_on', lang) : t('transcode_off', lang);

        const ipValue = networkStatus?.primary_ip || t('value_na', lang);

        const screenDisplay = String(screenResolution).replace(/x/i, '×');
        const cpuLoadLabel = cpuLoad === null
            ? ''
            : `${t('metric_cpu_load', lang)} ${cpuLoadValue}`;

        const html = `
            <div class="metric">
                <div class="metric-icon" aria-hidden="true">📺</div>
                <div class="metric-value">${this.escapeHtml(screenDisplay)}</div>
                <div class="metric-label">${this.escapeHtml(t('metric_screen', lang))}</div>
            </div>
            <div class="metric">
                <div class="metric-icon" aria-hidden="true">🔊</div>
                <div class="metric-value">${this.escapeHtml(volumeValue)}</div>
                <div class="metric-label">${this.escapeHtml(t('metric_volume', lang))}</div>
            </div>
            <div class="metric">
                <div class="metric-icon" aria-hidden="true">💾</div>
                <div class="metric-value">${this.escapeHtml(storagePercentText)}</div>
                ${this._metricBarHtml(storagePercent)}
                <div class="metric-label">${this.escapeHtml(storageSubLabel || t('metric_storage', lang))}</div>
            </div>
            <div class="metric">
                <div class="metric-icon" aria-hidden="true">🌡</div>
                <div class="metric-value">${this.escapeHtml(cpuTempValue)}</div>
                ${this._metricBarHtml(cpuLoad)}
                <div class="metric-label">${this.escapeHtml(cpuLoadLabel || t('metric_cpu_temp', lang))}</div>
            </div>
            <div class="metric">
                <div class="metric-icon" aria-hidden="true">🎬</div>
                <div class="metric-value">${this.escapeHtml(transcodeValue)}</div>
                <div class="metric-label">${this.escapeHtml(t('metric_transcode', lang))}</div>
            </div>
            <div class="metric">
                <div class="metric-icon" aria-hidden="true">🌐</div>
                <div class="metric-value">${this.escapeHtml(ipValue)}</div>
                <div class="metric-label">${this.escapeHtml(t('metric_ip', lang))}</div>
            </div>
        `;
        elements.settingsPanel.innerHTML = html;
    },

    renderPlaylists(playlists) {
        const grid = elements.playlistCards;
        if (!grid) {
            console.error('Playlist cards container not found');
            return;
        }

        const playlistsArray = Array.isArray(playlists) ? playlists : [];
        const lang = getUiLang();
        const un = t('unnamed', lang);
        const pt = t('play_title', lang);
        const st = t('stop_title', lang);
        const et = t('edit_title', lang);
        const dt = t('delete_title', lang);
        const noPreview = `📷 ${t('no_preview', lang)}`;
        const icons = this._cardActionIcons();

        grid.innerHTML = playlistsArray.map((playlist) => {
            const previewFile = playlist.preview_filename;
            const hasPreview = Number(playlist.files_count || 0) > 0 && previewFile;
            const thumbSrc = hasPreview ? this.escapeHtml(this.thumbnailUrl(previewFile)) : '';
            const meta = this.escapeHtml(this.formatCardMeta(playlist.customer, playlist.files_count, lang));

            return `
            <article class="playlist-card" data-id="${playlist.id}" role="listitem" draggable="true">
                <div class="card-thumb-wrap">
                    ${hasPreview ? `<img class="card-thumb" src="${thumbSrc}" alt="" loading="lazy" decoding="async" onerror="this.style.display='none';this.nextElementSibling.hidden=false;">` : ''}
                    <div class="card-thumb-placeholder" ${hasPreview ? 'hidden' : ''}>
                        <span>${this.escapeHtml(noPreview)}</span>
                    </div>
                    <span class="card-status status-idle"></span>
                </div>
                <div class="card-body">
                    <div class="card-title">${this.escapeHtml(playlist.name || un)}</div>
                    <div class="card-meta">${meta}</div>
                    <div class="card-actions">
                        <button type="button" class="icon-btn play" data-id="${playlist.id}" title="${this.escapeHtml(pt)}">${icons.play}<span class="sr-only">${this.escapeHtml(pt)}</span></button>
                        <button type="button" class="icon-btn stop" data-id="${playlist.id}" title="${this.escapeHtml(st)}" disabled>${icons.stop}<span class="sr-only">${this.escapeHtml(st)}</span></button>
                        <button type="button" class="icon-btn edit" data-id="${playlist.id}" title="${this.escapeHtml(et)}">${icons.edit}<span class="sr-only">${this.escapeHtml(et)}</span></button>
                        <button type="button" class="icon-btn danger delete" data-id="${playlist.id}" title="${this.escapeHtml(dt)}">${icons.del}<span class="sr-only">${this.escapeHtml(dt)}</span></button>
                    </div>
                </div>
            </article>
            `;
        }).join('');

        try {
            this.applyPlaybackStatusFromServer(state.playbackStatus, playlistsArray);
        } catch (e) {
            console.warn('Failed to apply playback status to cards:', e);
        }
    },

    refreshLanguageUI() {
        if (!elements.settingsPanel && !elements.playlistCards) return;
        this.renderSettings(state.currentSettings, {
            playlists: state.playlists,
            playbackStatus: state.playbackStatus,
            systemStatus: state.systemStatus,
            networkStatus: state.networkStatus,
        });
        this.renderPlaylists(state.playlists);
        try {
            this.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
        } catch (e) {
            console.warn(e);
        }
    },

    escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        return unsafe.toString()
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    },

    /**
     * @param {string|number} playlistId
     * @param {'playing'|'stopped'|'idle'} mode
     * @param {string|null|undefined} [currentMedia]
     */
    setPlaybackCardState(playlistId, mode, currentMedia) {
        const card = document.querySelector(`.playlist-card[data-id="${playlistId}"]`);
        if (!card) return;

        const playBtn = card.querySelector('button.play');
        const stopBtn = card.querySelector('button.stop');
        const badge = card.querySelector('.card-status');
        if (!playBtn || !stopBtn || !badge) return;

        const lang = getUiLang();
        card.classList.toggle('active', mode === 'playing');

        if (mode === 'playing') {
            playBtn.disabled = true;
            stopBtn.disabled = false;
            const media = String(currentMedia || '').trim();
            const mediaHtml = media
                ? `<span class="card-status__media">${this.escapeHtml(this._truncateText(media, 48))}</span>`
                : '';
            badge.innerHTML = `<span class="card-status__main">▶ ${this.escapeHtml(t('status_playing', lang))}</span>${mediaHtml}`;
            badge.className = 'card-status status-live';
        } else if (mode === 'stopped') {
            playBtn.disabled = false;
            stopBtn.disabled = true;
            badge.textContent = `■ ${t('status_stopped', lang)}`;
            badge.className = 'card-status status-stopped';
        } else {
            playBtn.disabled = false;
            stopBtn.disabled = true;
            badge.textContent = `⚪ ${t('status_idle', lang)}`;
            badge.className = 'card-status status-idle';
        }
    },

    /**
     * Sets Playing / Stopped on the matching playlist and Idle on all others.
     * @param {object|null|undefined} statusPayload - { status, playlist_id } from API or local state
     * @param {Array<{id:number}>} playlists - current list (defaults to state.playlists)
     */
    applyPlaybackStatusFromServer(statusPayload, playlists) {
        const list = playlists && playlists.length ? playlists : state.playlists || [];
        const ids = list.map((p) => String(p.id));
        if (!ids.length) return;

        const rawPid = statusPayload && statusPayload.playlist_id;
        const pid =
            rawPid != null && rawPid !== '' ? String(rawPid) : '';
        const st = String(statusPayload?.status || '').toLowerCase();

        const currentMedia = statusPayload?.current_media;
        ids.forEach((id) => {
            if (pid === id && st === 'playing') {
                this.setPlaybackCardState(id, 'playing', currentMedia);
            } else if (pid === id && st === 'stopped') {
                this.setPlaybackCardState(id, 'stopped');
            } else {
                this.setPlaybackCardState(id, 'idle');
            }
        });

        const playbackKey = `${st}|${pid}`;
        const playbackChanged = playbackKey !== state.lastAppliedPlaybackKey;
        state.lastAppliedPlaybackKey = playbackKey;

        this.updateNowOnScreen(this._nowScreenTitle(statusPayload, list));
        this.updatePlaybackSourceBadge(statusPayload);
        if (playbackChanged) {
            this.updatePreviewImage({ updateTimestamp: false, force: true });
        }
        refreshScheduleIfVisible();
    },

    updatePlaybackSourceBadge(statusPayload) {
        const lang = getUiLang();
        const badge = elements.playbackSourceBadge;
        const label = elements.playbackSourceLabel;
        const returnBtn = elements.returnToScheduleBtn;
        if (!badge || !label) return;

        const source = String(
            statusPayload?.schedule?.source || statusPayload?.source || 'idle'
        ).toLowerCase();

        const sourceMap = {
            schedule: { cls: 'playback-source-badge--schedule', key: 'playback_source_schedule' },
            manual: { cls: 'playback-source-badge--manual', key: 'playback_source_manual' },
            override: { cls: 'playback-source-badge--override', key: 'playback_source_override' },
            idle: { cls: 'playback-source-badge--idle', key: 'playback_source_idle' },
        };
        const meta = sourceMap[source] || sourceMap.idle;
        badge.className = `playback-source-badge ${meta.cls}`;
        label.textContent = t(meta.key, lang);

        if (returnBtn) {
            const showReturn = source === 'manual';
            returnBtn.hidden = !showReturn;
            returnBtn.disabled = false;
        }
    },

    updateLogo(logoPath) {
        if (!elements.logoImage) return;
        // The active logo is stored as a canonical file (idle_logo.jpg).
        // Do not trust settings.display.logo here (profiles may override display).
        if (logoPath) {
            state.fallbackLogoUsed = false;
            state.logoLoadAttempts = 0;
        }

        const basePath = state.fallbackLogoUsed
            ? CONFIG.defaultLogo
            : `${CONFIG.api.endpoints.serveMedia}/idle_logo.jpg`;

        const newSrc = `${basePath}?t=${Date.now()}`;

        elements.logoImage.onload = function() {
            this.style.display = 'block';
            state.logoLoadAttempts = 0;
        };

        elements.logoImage.onerror = function() {
            state.logoLoadAttempts++;
            
            if (state.logoLoadAttempts >= CONFIG.maxImageLoadAttempts && !state.fallbackLogoUsed) {
                console.warn('Max logo load attempts reached, using fallback');
                state.fallbackLogoUsed = true;
                this.src = `${CONFIG.defaultLogo}?t=${Date.now()}`;
            } else if (!state.fallbackLogoUsed) {
                setTimeout(() => {
                    this.src = `${CONFIG.api.endpoints.serveMedia}/idle_logo.jpg?t=${Date.now()}`;
                }, 2000);
            }
        };
        
        elements.logoImage.src = newSrc;
        elements.logoImage.style.display = 'none';
    },

    updatePreviewImage(options = {}) {
        if (!elements.previewImage) return;

        const { updateTimestamp = true, force = false } = options || {};
        const now = Date.now();
        const minGapMs = 5000;
        if (!force && state.lastMpvPreviewRefreshAt && now - state.lastMpvPreviewRefreshAt < minGapMs) {
            return;
        }

        const img = elements.previewImage;
        const placeholder = elements.mpvPreviewPlaceholder;
        const requestId = ++state.mpvPreviewRequestId;
        const newSrc = `${CONFIG.api.endpoints.previewImage}?t=${now}`;

        const onSuccess = () => {
            if (requestId !== state.mpvPreviewRequestId) return;
            img.style.display = 'block';
            if (placeholder) placeholder.classList.remove('is-visible');
            state.previewLoadAttempts = 0;
            state.lastMpvPreviewRefreshAt = Date.now();
            if (updateTimestamp && elements.mpvLastUpdate) {
                elements.mpvLastUpdate.textContent = new Date().toLocaleTimeString();
            }
        };

        const onFail = () => {
            if (requestId !== state.mpvPreviewRequestId) return;
            state.previewLoadAttempts += 1;
            if (state.previewLoadAttempts >= CONFIG.maxImageLoadAttempts && !state.fallbackPreviewUsed) {
                state.fallbackPreviewUsed = true;
                const fallback = new Image();
                fallback.onload = () => {
                    img.src = fallback.src;
                    onSuccess();
                };
                fallback.src = `${CONFIG.defaultPreview}?t=${Date.now()}`;
                return;
            }
            if (!state.fallbackPreviewUsed) {
                setTimeout(() => ui.updatePreviewImage({ updateTimestamp, force: true }), 3000);
                return;
            }
            img.style.display = 'none';
            if (placeholder) {
                placeholder.textContent = t('no_preview', getUiLang());
                placeholder.classList.add('is-visible');
            }
        };

        const loader = new Image();
        loader.onload = () => {
            if (requestId !== state.mpvPreviewRequestId) return;
            img.src = loader.src;
            onSuccess();
        };
        loader.onerror = onFail;
        loader.src = newSrc;
    },

    toggleModal(show = true) {
        if (elements.modal) {
            elements.modal.style.display = show ? 'block' : 'none';
        }
    },

    previewLogo(file) {
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            elements.logoImage.src = e.target.result;
            elements.logoImage.style.display = 'block';
        };
        reader.onerror = (e) => {
            console.error('FileReader error:', e);
            showError('Failed to preview logo file');
        };
        reader.readAsDataURL(file);
    },

    setLogoUploadBusy(busy) {
        const btn = elements.logoReplaceBtn;
        if (!btn) return;
        const spinner = btn.querySelector('.loading-spinner');
        btn.disabled = busy;
        btn.querySelectorAll('span:not(.loading-spinner)').forEach((el) => {
            el.style.display = busy ? 'none' : '';
        });
        if (spinner) spinner.style.display = busy ? 'inline-block' : 'none';
    }
};

// Event handlers
const handlers = {
    async init() {
        try {
            const [settings, playlists, playbackStatusResp, systemStatusResp, networkStatusResp] = await Promise.all([
                api.getSettings(),
                api.getPlaylists(),
                api.getPlaybackStatus().catch((e) => {
                    console.warn('Failed to load playback status:', e);
                    return null;
                }),
                api.getSystemStatus().catch((e) => {
                    console.warn('Failed to load system status:', e);
                    return null;
                }),
                api.getNetworkStatus().catch((e) => {
                    console.warn('Failed to load network status:', e);
                    return null;
                }),
            ]);

            state.currentSettings = settings;
            state.playlists = playlists;
            const innerStatus =
                playbackStatusResp &&
                typeof playbackStatusResp.status === 'object' &&
                playbackStatusResp.status !== null &&
                !Array.isArray(playbackStatusResp.status)
                    ? playbackStatusResp.status
                    : null;
            state.playbackStatus = innerStatus;
            state.systemStatus =
                systemStatusResp &&
                typeof systemStatusResp.status === 'object' &&
                systemStatusResp.status !== null &&
                !Array.isArray(systemStatusResp.status)
                    ? systemStatusResp.status
                    : null;
            state.networkStatus =
                networkStatusResp &&
                typeof networkStatusResp.network === 'object' &&
                networkStatusResp.network !== null &&
                !Array.isArray(networkStatusResp.network)
                    ? networkStatusResp.network
                    : null;

            ui.renderSettings(settings, {
                playlists: state.playlists,
                playbackStatus: state.playbackStatus,
                systemStatus: state.systemStatus,
                networkStatus: state.networkStatus,
            });
            ui.renderPlaylists(playlists);
            ui.updateLogo(settings.display?.logo);

            initSchedule({ getPlaylists: () => state.playlists });
            this.setupHomeTabs();
            this.setupEventListeners();
            document.addEventListener('dsign:language-changed', () => {
                try {
                    ui.refreshLanguageUI();
                } catch (e) {
                    console.warn('Language refresh failed', e);
                }
            });
            this.setupSocketSubscriptions();

            requestAnimationFrame(() => {
                try {
                    ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                    ui.updatePreviewImage({ updateTimestamp: true, force: true });
                } catch (e) {
                    console.warn('Initial playback/preview paint failed', e);
                }
                this.startAutoRefresh();
                this.startPreviewRefresh(settings);
            });

        } catch (error) {
            console.error('Initialization failed:', error);
            showError('Failed to initialize application');
        }
    },

    setupHomeTabs() {
        const tabs = document.querySelectorAll(CONFIG.selectors.viewTabs);
        const viewPlaylists = elements.viewPlaylists || document.querySelector(CONFIG.selectors.viewPlaylists);
        const viewSchedule = elements.viewSchedule || document.querySelector(CONFIG.selectors.viewSchedule);
        if (!tabs.length || !viewPlaylists || !viewSchedule) return;

        const switchView = (view) => {
            const next = view === 'schedule' ? 'schedule' : 'playlists';
            state.activeHomeView = next;
            try {
                sessionStorage.setItem('dsign_home_view', next);
            } catch {
                /* ignore */
            }

            tabs.forEach((tab) => {
                const active = tab.dataset.view === next;
                tab.classList.toggle('active', active);
                tab.setAttribute('aria-selected', active ? 'true' : 'false');
            });

            const showPlaylists = next === 'playlists';
            viewPlaylists.hidden = !showPlaylists;
            viewPlaylists.classList.toggle('home-view-content--hidden', !showPlaylists);
            viewSchedule.hidden = showPlaylists;
            viewSchedule.classList.toggle('home-view-content--hidden', showPlaylists);

            if (showPlaylists) {
                hideScheduleView();
            } else {
                showScheduleView();
            }
        };

        tabs.forEach((tab) => {
            tab.addEventListener('click', () => switchView(tab.dataset.view));
        });

        let initial = 'playlists';
        try {
            const stored = sessionStorage.getItem('dsign_home_view');
            if (stored === 'schedule' || stored === 'playlists') initial = stored;
        } catch {
            /* ignore */
        }
        switchView(initial);
    },

    setupEventListeners() {
        elements.returnToScheduleBtn?.addEventListener('click', async () => {
            const btn = elements.returnToScheduleBtn;
            if (!btn || btn.disabled) return;
            btn.disabled = true;
            try {
                const resp = await api.returnToSchedule();
                if (!resp?.success) throw new Error(resp?.error || t('return_to_schedule_err', getUiLang()));
                const statusResp = await api.getPlaybackStatus();
                const inner =
                    statusResp?.status && typeof statusResp.status === 'object'
                        ? statusResp.status
                        : null;
                state.playbackStatus = inner;
                ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                showAlert(t('return_to_schedule_ok', getUiLang()), 'success');
            } catch (err) {
                showError(err.message || t('return_to_schedule_err', getUiLang()));
            } finally {
                if (btn) btn.disabled = false;
            }
        });

        // Playlist modal
        elements.createPlaylistBtn?.addEventListener('click', () => {
            ui.toggleModal(true);
        });

        elements.modalClose?.addEventListener('click', () => {
            ui.toggleModal(false);
        });

        window.addEventListener('click', (e) => {
            if (e.target === elements.modal) {
                ui.toggleModal(false);
            }
        });

        // Create playlist form
        elements.playlistForm?.addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(elements.playlistForm);
            
            try {
                const response = await api.createPlaylist({
                    name: formData.get('name'),
                    customer: formData.get('customer') || ''
                });

                state.playlists = sortPlaylistsByOrder([...state.playlists, {
                    id: response.playlist_id,
                    name: formData.get('name'),
                    customer: formData.get('customer') || '',
                    files_count: 0,
                    sort_order: state.playlists.length,
                }]);
                
                ui.renderPlaylists(state.playlists);
                ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                ui.toggleModal(false);
                elements.playlistForm.reset();
                showAlert('Playlist created successfully', 'success');
            } catch (error) {
                showError('Failed to create playlist: ' + error.message);
            }
        });

        elements.logoReplaceBtn?.addEventListener('click', () => {
            elements.logoFileInput?.click();
        });

        elements.logoFileInput?.addEventListener('change', async (e) => {
            const file = e.target.files && e.target.files[0] ? e.target.files[0] : null;
            if (!file) return;

            if (!file.type.match('image.*')) {
                showError('Only image files are allowed');
                e.target.value = '';
                return;
            }

            if (file.size > 5 * 1024 * 1024) {
                showError('File size should be less than 5MB');
                e.target.value = '';
                return;
            }

            ui.previewLogo(file);
            ui.setLogoUploadBusy(true);

            try {
                const formData = new FormData(elements.logoForm);
                const result = await api.uploadLogo(formData);

                state.fallbackLogoUsed = false;
                state.logoLoadAttempts = 0;

                ui.updateLogo(result.filename);
                ui.updatePreviewImage({ updateTimestamp: true, force: true });
                showAlert('Logo updated successfully', 'success');

                const settings = await api.getSettings();
                state.currentSettings = settings;
                ui.renderSettings(settings, {
                    playlists: state.playlists,
                    playbackStatus: state.playbackStatus,
                    systemStatus: state.systemStatus,
                    networkStatus: state.networkStatus,
                });
            } catch (error) {
                console.error('Logo upload failed:', error);
                showError('Failed to upload logo: ' + error.message);
                ui.updateLogo(state.currentSettings?.display?.logo);
            } finally {
                e.target.value = '';
                ui.setLogoUploadBusy(false);
            }
        });

        this.setupPlaylistDragDrop();

        // Refresh preview button
        elements.refreshPreviewBtn?.addEventListener('click', async () => {
            if (state.isPreviewRefreshing) return;

            try {
                elements.refreshPreviewBtn.disabled = true;

                const r = await api.refreshPreview();
                const now = Date.now();
                const retryMs = Math.max(0, Math.round((r?.retry_in_sec || 0) * 1000));
                if (r?.skipped) {
                    // Capture was throttled on server; just reload existing image and inform user.
                    state.previewCaptureCooldownUntil = now + retryMs;
                    ui.updatePreviewImage({ updateTimestamp: false, force: true });
                    showAlert('Preview is up to date', 'info');
                } else if (r?.success) {
                    setTimeout(() => ui.updatePreviewImage({ updateTimestamp: true, force: true }), 1200);
                    showAlert('Preview refreshed', 'success');
                } else {
                    throw new Error(r?.error || 'Preview refresh failed');
                }
            } catch (error) {
                console.warn('Failed to refresh preview:', error);
                showError('Failed to refresh preview');
            } finally {
                elements.refreshPreviewBtn.disabled = false;
            }
        });

        // Playlist actions
        elements.playlistCards?.addEventListener('click', async (e) => {
            const btn = e.target.closest('button');
            if (!btn || !btn.dataset.id) return;

            const playlistId = btn.dataset.id;
            
            try {
                if (btn.classList.contains('play')) {
                    await api.startPlayback(playlistId);
                    state.playbackStatus = {
                        status: 'playing',
                        playlist_id: Number(playlistId)
                    };
                    ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                    ui.renderSettings(state.currentSettings, {
                        playlists: state.playlists,
                        playbackStatus: state.playbackStatus,
                        systemStatus: state.systemStatus,
                        networkStatus: state.networkStatus,
                    });
                    showAlert('Playback started', 'success');
                    
                } else if (btn.classList.contains('stop')) {
                    await api.stopPlayback();
                    state.playbackStatus = {
                        status: 'stopped',
                        playlist_id: Number(playlistId)
                    };
                    ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                    ui.renderSettings(state.currentSettings, {
                        playlists: state.playlists,
                        playbackStatus: state.playbackStatus,
                        systemStatus: state.systemStatus,
                        networkStatus: state.networkStatus,
                    });
                    showAlert('Playback stopped', 'info');
                    
                } else if (btn.classList.contains('edit')) {
                    window.location.href = `/playlist/${playlistId}`;
                    
                } else if (btn.classList.contains('delete')) {
                    if (confirm('Are you sure you want to delete this playlist?')) {
                        try {
                            // Show loading state
                            setBtnIconText(btn, '…');
                            btn.disabled = true;

                            const delResult = await api.deletePlaylist(playlistId);
                            if (delResult && typeof delResult === 'object' && delResult.success === false) {
                                throw new Error(delResult.error || 'Delete failed');
                            }
                            
                            // Remove the playlist row immediately
                            const card = document.querySelector(`.playlist-card[data-id="${playlistId}"]`);
                            if (card) {
                                card.remove();
                            }
                            
                            // Update state
                            state.playlists = state.playlists.filter(p => p.id !== playlistId);
                            if (
                                state.playbackStatus &&
                                String(state.playbackStatus.playlist_id) === String(playlistId)
                            ) {
                                state.playbackStatus = null;
                            }
                            ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                            showAlert('Playlist deleted', 'info');
                        } catch (error) {
                            console.error('Delete failed:', error);
                            showError('Failed to delete playlist: ' + (error.details || error.message));
                            
                            // Reset button state
                            setBtnIconText(btn, '🗑');
                            btn.disabled = false;
                        }
                    }
                }
            } catch (error) {
                console.error('Action failed:', error);
                showError(error.status === 403 ? 
                    'Permission denied' : 'Action failed: ' + error.message);
            }
        });
    },

    setupPlaylistDragDrop() {
        const grid = elements.playlistCards;
        if (!grid || grid.dataset.dndBound === '1') return;
        grid.dataset.dndBound = '1';

        let draggedId = null;
        let dragOverCard = null;

        const blockDragTarget = (el) => el?.closest?.('button, a, input, textarea, select, label');

        grid.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.playlist-card');
            if (!card || blockDragTarget(e.target)) {
                e.preventDefault();
                return;
            }
            draggedId = card.dataset.id;
            card.classList.add('is-dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', draggedId);
        });

        grid.addEventListener('dragend', () => {
            grid.querySelectorAll('.playlist-card.is-dragging').forEach((c) => c.classList.remove('is-dragging'));
            dragOverCard?.classList.remove('is-drag-over');
            dragOverCard = null;
            draggedId = null;
        });

        grid.addEventListener('dragover', (e) => {
            e.preventDefault();
            const card = e.target.closest('.playlist-card');
            const dragging = grid.querySelector('.playlist-card.is-dragging');
            if (!card || !dragging || card === dragging) return;
            e.dataTransfer.dropEffect = 'move';
            if (dragOverCard && dragOverCard !== card) {
                dragOverCard.classList.remove('is-drag-over');
            }
            dragOverCard = card;
            card.classList.add('is-drag-over');
            const rect = card.getBoundingClientRect();
            const after = e.clientY - rect.top > rect.height / 2;
            if (after) {
                card.after(dragging);
            } else {
                card.before(dragging);
            }
        });

        grid.addEventListener('dragleave', (e) => {
            const card = e.target.closest('.playlist-card');
            if (card && card === dragOverCard && !card.contains(e.relatedTarget)) {
                card.classList.remove('is-drag-over');
                if (dragOverCard === card) dragOverCard = null;
            }
        });

        grid.addEventListener('drop', async (e) => {
            e.preventDefault();
            dragOverCard?.classList.remove('is-drag-over');
            dragOverCard = null;
            grid.querySelectorAll('.playlist-card.is-dragging').forEach((c) => c.classList.remove('is-dragging'));

            if (!draggedId) return;

            const orderIds = [...grid.querySelectorAll('.playlist-card')].map((c) => Number(c.dataset.id));
            const prevPlaylists = state.playlists.map((p) => ({ ...p }));
            state.playlists = playlistsFromDomOrder(orderIds, state.playlists);

            try {
                const result = await api.reorderPlaylists(orderIds);
                if (!result?.success) {
                    throw new Error(result?.error || 'Reorder failed');
                }
                state.playlists = state.playlists.map((p, idx) => ({ ...p, sort_order: idx }));
            } catch (err) {
                console.warn('Playlist reorder failed:', err);
                state.playlists = prevPlaylists;
                ui.renderPlaylists(state.playlists);
                ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                showError('Failed to save playlist order');
            }
            draggedId = null;
        });
    },

    setupSocketSubscriptions() {
        // Socket manager is attached to window.App by base init.
        const sockets = window.App?.Sockets;
        if (!sockets || typeof sockets.on !== 'function') return;
        state.sockets = sockets;

        sockets.on('connect', () => {
            state.usingSocketPush = true;
            this.startAutoRefresh();
        });
        sockets.on('disconnect', () => {
            state.usingSocketPush = false;
            this.startAutoRefresh();
        });
        sockets.on('playback_update', (payload) => {
            // payload: { status, playlist_id, timestamp }
            state.playbackStatus = payload && typeof payload === 'object' ? payload : state.playbackStatus;
            try {
                ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                ui.renderSettings(state.currentSettings, {
                    playlists: state.playlists,
                    playbackStatus: state.playbackStatus,
                    systemStatus: state.systemStatus,
                    networkStatus: state.networkStatus,
                });
            } catch (e) {
                console.warn('Failed to apply socket playback update:', e);
            }
        });
    },

    startAutoRefresh() {
        // Stop previous loop (if any).
        if (state.refreshIntervalId) {
            clearInterval(state.refreshIntervalId);
            state.refreshIntervalId = null;
        }
        if (state.refreshTimerId) {
            clearTimeout(state.refreshTimerId);
            state.refreshTimerId = null;
        }

        const isPlaying = () => {
            const s = state.playbackStatus || {};
            const v = (s.status || s.state || s.mode || '').toString().toLowerCase();
            return v === 'playing' || v === 'play';
        };

        const computeBaseIntervalMs = () => {
            if (state.usingSocketPush) return CONFIG.refreshIntervalSocketFallbackMs;
            return isPlaying() ? CONFIG.refreshIntervalActiveMs : CONFIG.refreshIntervalIdleMs;
        };

        const scheduleNext = (delayMs) => {
            const safeDelay = Math.max(3000, Number(delayMs) || 0);
            state.refreshTimerId = setTimeout(tick, safeDelay);
        };

        const tick = async () => {
            // Prevent overlapping refreshes (important when network is slow).
            if (state.refreshInFlight) {
                scheduleNext(computeBaseIntervalMs());
                return;
            }
            state.refreshInFlight = true;

            try {
                state.autoRefreshTickCount = (state.autoRefreshTickCount || 0) + 1;
                const tickN = state.autoRefreshTickCount;
                const settingsEvery = Math.max(1, Number(CONFIG.settingsPollEveryNTicks) || 6);
                const shouldFetchSettings =
                    tickN === 1 || tickN % settingsEvery === 0;

                const [settings, playbackStatusResp, systemStatusResp, networkStatusResp] = await Promise.all([
                    shouldFetchSettings
                        ? api.getSettings().catch(() => state.currentSettings)
                        : Promise.resolve(state.currentSettings),
                    api.getPlaybackStatus().catch(() => null),
                    api.getSystemStatus().catch(() => null),
                    api.getNetworkStatus().catch(() => null),
                ]);

                const latestPlaybackStatus =
                    playbackStatusResp &&
                    typeof playbackStatusResp.status === 'object' &&
                    playbackStatusResp.status !== null &&
                    !Array.isArray(playbackStatusResp.status)
                        ? playbackStatusResp.status
                        : state.playbackStatus;
                const latestSystemStatus =
                    systemStatusResp &&
                    typeof systemStatusResp.status === 'object' &&
                    systemStatusResp.status !== null &&
                    !Array.isArray(systemStatusResp.status)
                        ? systemStatusResp.status
                        : state.systemStatus;
                const latestNetworkStatus =
                    networkStatusResp &&
                    typeof networkStatusResp.network === 'object' &&
                    networkStatusResp.network !== null &&
                    !Array.isArray(networkStatusResp.network)
                        ? networkStatusResp.network
                        : state.networkStatus;

                const settingsChanged = JSON.stringify(state.currentSettings) !== JSON.stringify(settings);
                const playbackChanged = JSON.stringify(state.playbackStatus) !== JSON.stringify(latestPlaybackStatus);
                const systemChanged = JSON.stringify(state.systemStatus) !== JSON.stringify(latestSystemStatus);
                const networkChanged = JSON.stringify(state.networkStatus) !== JSON.stringify(latestNetworkStatus);

                if (settingsChanged) {
                    state.currentSettings = settings;
                    ui.updateLogo(settings.display?.logo);
                    // If Auto preview interval changed in Settings, reflect it here.
                    this.startPreviewRefresh(settings);
                }
                state.playbackStatus = latestPlaybackStatus;
                state.systemStatus = latestSystemStatus;
                state.networkStatus = latestNetworkStatus;

                if (settingsChanged || playbackChanged || systemChanged || networkChanged) {
                    ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                    ui.renderSettings(state.currentSettings, {
                        playlists: state.playlists,
                        playbackStatus: state.playbackStatus,
                        systemStatus: state.systemStatus,
                        networkStatus: state.networkStatus,
                    });
                }
            } catch (error) {
                console.error('Auto-refresh failed:', error);
                // Exponential backoff on failures to avoid flooding the browser/server.
                state.refreshBackoffMs = state.refreshBackoffMs ? Math.min(state.refreshBackoffMs * 2, 60000) : 2000;
            } finally {
                state.refreshInFlight = false;
            }
            const base = computeBaseIntervalMs();
            const delay = Math.max(base, state.refreshBackoffMs || 0);
            scheduleNext(delay);
        };

        state.refreshBackoffMs = 0;
        scheduleNext(computeBaseIntervalMs());
    },

    startPreviewRefresh(settings) {
        if (state.previewRefreshId) {
            clearInterval(state.previewRefreshId);
            state.previewRefreshId = null;
        }

        const intervalSec = Number(settings?.display?.preview_auto_interval_sec || 0);
        // If Auto preview is Off, do not background-refresh the preview image.
        if (!intervalSec) return;

        const intervalMs = Math.max(15000, intervalSec * 1000);
        state.previewRefreshId = setInterval(() => {
            if (Date.now() < (state.previewCaptureCooldownUntil || 0)) return;
            ui.updatePreviewImage({ updateTimestamp: true, force: false });
        }, intervalMs);
    },

    cleanup() {
        if (state.refreshIntervalId) {
            clearInterval(state.refreshIntervalId);
        }
        if (state.refreshTimerId) {
            clearTimeout(state.refreshTimerId);
        }
        if (state.previewRefreshId) {
            clearInterval(state.previewRefreshId);
        }
    }
};

// Initialize the application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    handlers.init();
});

// Cleanup when page unloads
window.addEventListener('beforeunload', () => {
    handlers.cleanup();
});

export { api, ui, handlers, CONFIG, state, elements };
