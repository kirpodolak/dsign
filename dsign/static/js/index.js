import { showAlert, showError } from './utils/alerts.js';
import { toggleButtonState } from './utils/helpers.js';
import { fetchAPI, getCSRFToken } from './utils/api.js';

// Application configuration
const CONFIG = {
    api: {
        baseUrl: '',
        endpoints: {
            settings: '/api/settings/current',
            playlists: '/api/playlists',
            playback: '/api/playback',
            systemStatus: '/api/system/status',
            networkStatus: '/api/system/network/status',
            uploadLogo: '/api/media/upload_logo',
            media: '/api/media/files',
            mediaUpload: '/api/media/upload',
            serveMedia: '/api/media',
            previewImage: '/api/media/mpv_screenshot'
        },
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || 
                          document.cookie.match(/csrf_token=([^;]+)/)?.[1] || ''                
        }
    },
    selectors: {
        playlistTable: '#playlist-table',
        playlistTableBody: '#playlist-table-body',
        createPlaylistBtn: '#create-playlist-btn',
        modal: '#create-playlist-modal',
        modalClose: '.modal .close',
        playlistForm: '#create-playlist-form',
        statusIndicator: '#playlist-status',
        uploadLogoBtn: '#upload-logo-btn',
        logoForm: '#logo-upload-form',
        settingsPanel: '.info-panel .info-card',
        logoImage: '#idle-logo',
        previewImage: '#mpv-preview-image',
        currentSettings: '#current-settings',
        loadingIndicator: '#loading-indicator',
        logoFileInput: '#logo-upload-form input[type="file"]',
        logoSelectedFile: '#logo-selected-file',
        refreshPreviewBtn: '#refresh-mpv-preview',
        mpvLastUpdate: '#mpv-last-update'
    },
    defaultLogo: '/static/images/default-logo.jpg',
    defaultPreview: '/static/images/default-preview.jpg',
    // Polling strategy:
    // - Playback can feel "real-time"
    // - System/network should be slower (nmcli/procfs reads + DOM updates are noticeable on Pi)
    refreshPlaybackInterval: 2000,
    refreshSystemInterval: 10000,
    previewRefreshInterval: 15000,
    maxImageLoadAttempts: 3
};

// Initialize DOM elements
const elements = Object.fromEntries(
    Object.entries(CONFIG.selectors)
        .map(([key, selector]) => [key, document.querySelector(selector)])
);

// Application state
const state = {
    playlists: [],
    currentSettings: {},
    playbackStatus: null,
    systemStatus: null,
    networkStatus: null,
    lastSystemRefreshAt: 0,
    refreshIntervalId: null,
    previewRefreshId: null,
    logoLoadAttempts: 0,
    previewLoadAttempts: 0,
    fallbackLogoUsed: false,
    fallbackPreviewUsed: false,
    isPreviewRefreshing: false,
    previewCaptureCooldownUntil: 0
};

function stableStringify(value) {
    try {
        if (value === null || value === undefined) return '';
        if (typeof value !== 'object') return String(value);
        if (Array.isArray(value)) return JSON.stringify(value);
        const keys = Object.keys(value).sort();
        const out = {};
        keys.forEach((k) => {
            out[k] = value[k];
        });
        return JSON.stringify(out);
    } catch {
        return '';
    }
}

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

            const response = await fetch(`${CONFIG.api.baseUrl}${url}`, {
                ...fetchOptions,
                headers: {
                    ...CONFIG.api.headers,
                    'X-CSRFToken': csrfToken,
                    ...(fetchOptions.headers || {})
                },
                credentials: 'include' // Ensure cookies are sent with requests
            });

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
        return playlists.map(playlist => ({
            ...playlist,
            customer: playlist.customer || ''
        }));
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

    updatePreviewAutoStatus(settings) {
        const el = document.querySelector('#mpv-auto-refresh-status');
        if (!el) return;
        const sec = Number(settings?.display?.preview_auto_interval_sec || 0);
        if (!sec) {
            el.innerHTML = [
                '<span class="mpv-auto-refresh-line"><strong>Auto preview:</strong> Off</span>',
                '<span class="mpv-auto-refresh-line">Background capture is blocked. Use the Refresh button.</span>'
            ].join('');
            el.classList.add('is-off');
        } else {
            const mins = Math.round(sec / 60);
            el.innerHTML = [
                `<span class="mpv-auto-refresh-line"><strong>Auto preview:</strong> Every ${mins} min</span>`,
                '<span class="mpv-auto-refresh-line">Tip: On Pi 3B+ use Off or infrequent.</span>'
            ].join('');
            el.classList.remove('is-off');
        }
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
        if (!Number.isFinite(value) || value < 0) return 'N/A';
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
        return preset === 'auto' ? 'Auto' : 'N/A';
    },

    renderSettings(settings, runtime = {}) {
        if (!elements.settingsPanel) return;

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
            ? 'Mute'
            : (volumeNum !== null ? `${Math.max(0, Math.min(100, Math.round(volumeNum)))}%` : 'N/A');

        const playbackState = String(playbackStatus?.status || '').toLowerCase();
        const activePlaylistId = playbackStatus?.playlist_id;
        const activePlaylist = playlists.find((item) => String(item.id) === String(activePlaylistId));
        const broadcastRaw = playbackState === 'playing'
            ? (activePlaylist?.name || `Playlist #${activePlaylistId ?? ''}`.trim() || 'Playlist')
            : 'Logo';
        const broadcastValue = this._truncateText(broadcastRaw, 32);

        const storageData = systemStatus?.storage?.media || systemStatus?.storage?.root || null;
        const storagePercent = this._clampPercent(storageData?.used_percent);
        const storageValue = storagePercent !== null
            ? `${Math.round(storagePercent)}% (${this._formatBytes(storageData.used)} / ${this._formatBytes(storageData.total)})`
            : 'N/A';

        const cpuTempRaw = Number(systemStatus?.cpu?.temp_c);
        const cpuTemp = Number.isFinite(cpuTempRaw) ? cpuTempRaw : null;
        const cpuTempPercent = cpuTemp === null ? null : this._clampPercent(cpuTemp);
        const cpuTempValue = cpuTemp === null ? 'N/A' : `${cpuTemp.toFixed(1)}°C`;

        const cpuLoadRaw = Number(systemStatus?.cpu?.usage_percent ?? systemStatus?.cpu?.load_percent);
        const cpuLoad = Number.isFinite(cpuLoadRaw) ? this._clampPercent(cpuLoadRaw) : null;
        const cpuLoadValue = cpuLoad === null ? 'N/A' : `${cpuLoad.toFixed(1)}%`;

        const transcodeEnabledRaw = settings?.display?.auto_transcode_videos;
        const transcodeEnabled = transcodeEnabledRaw === true || String(transcodeEnabledRaw).toLowerCase() === 'true';
        const transcodeValue = transcodeEnabled ? 'On' : 'Off';

        const ipValue = networkStatus?.primary_ip || 'N/A';

        const html = `
            <div class="settings-section">
                <h3>Operational Metrics</h3>
                <div class="metrics-grid">
                    <div class="metric-item">
                        <div class="metric-label">Screen</div>
                        <div class="metric-value">${this.escapeHtml(screenResolution)}</div>
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">Volume</div>
                        <div class="metric-value">${this.escapeHtml(volumeValue)}</div>
                    </div>
                    <div class="metric-item metric-item--full">
                        <div class="metric-label">Broadcast</div>
                        <div class="metric-value">${this.escapeHtml(broadcastValue)}</div>
                    </div>
                    <div class="metric-item metric-item--full">
                        <div class="metric-label">Storage</div>
                        <div class="metric-value">${this.escapeHtml(storageValue)}</div>
                        ${this._renderMetricBar(storagePercent)}
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">CPU Temperature</div>
                        <div class="metric-value">${this.escapeHtml(cpuTempValue)}</div>
                        ${this._renderMetricBar(cpuTempPercent)}
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">CPU Load</div>
                        <div class="metric-value">${this.escapeHtml(cpuLoadValue)}</div>
                        ${this._renderMetricBar(cpuLoad)}
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">Video Optimization</div>
                        <div class="metric-value">${this.escapeHtml(transcodeValue)}</div>
                    </div>
                    <div class="metric-item">
                        <div class="metric-label">Current IP Address</div>
                        <div class="metric-value">${this.escapeHtml(ipValue)}</div>
                    </div>
                </div>
            </div>
        `;
        elements.settingsPanel.innerHTML = html;
        this.updatePreviewAutoStatus(settings);
    },

    renderPlaylists(playlists) {
        let tableBody = document.querySelector('#playlist-table-body');
        if (!tableBody) {
            const table = document.querySelector('#playlist-table');
            if (table) {
                tableBody = document.createElement('tbody');
                tableBody.id = 'playlist-table-body';
                table.appendChild(tableBody);
            } else {
                console.error('Playlist table not found');
                return;
            }
        }

        const playlistsArray = Array.isArray(playlists) ? playlists : [];
    
        console.log('Rendering playlists with customer data:', playlistsArray.map(p => ({
            id: p.id,
            name: p.name,
            customer: p.customer,
            files_count: p.files_count
        })));

        tableBody.innerHTML = playlistsArray.map(playlist => `
            <tr data-id="${playlist.id}">
                <td class="playlist-td-name">${this.escapeHtml(playlist.name || 'Unnamed')}</td>
                <td class="playlist-td-customer">${this.escapeHtml(playlist.customer)}</td>
                <td class="playlist-td-files">${playlist.files_count || 0}</td>
                <td class="playlist-td-status">
                    <div class="playlist-td-inner">
                        <span class="status-badge"></span>
                    </div>
                </td>
                <td class="playlist-td-actions">
                    <div class="playlist-td-inner">
                        <div class="actions">
                        <button class="btn play" data-id="${playlist.id}" title="Play">
                            <i class="fas fa-play"></i>
                        </button>
                        <button class="btn stop" data-id="${playlist.id}" title="Stop" disabled>
                            <i class="fas fa-stop"></i>
                        </button>
                        <button class="btn edit" data-id="${playlist.id}" title="Edit">
                            <i class="fas fa-edit"></i>
                        </button>
                        <button class="btn delete" data-id="${playlist.id}" title="Delete">
                            <i class="fas fa-trash"></i>
                        </button>
                        </div>
                    </div>
                </td>
            </tr>
        `).join('');
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
     */
    setPlaybackRowState(playlistId, mode) {
        const rows = document.querySelectorAll(`tr[data-id="${playlistId}"]`);
        if (!rows.length) return;

        rows.forEach((row) => {
            const playBtn = row.querySelector('.play');
            const stopBtn = row.querySelector('.stop');
            const statusBadge = row.querySelector('.status-badge');
            if (!playBtn || !stopBtn || !statusBadge) return;

            if (mode === 'playing') {
                playBtn.disabled = true;
                stopBtn.disabled = false;
                statusBadge.textContent = 'Playing';
                statusBadge.className = 'status-badge active playing';
            } else if (mode === 'stopped') {
                playBtn.disabled = false;
                stopBtn.disabled = true;
                statusBadge.textContent = 'Stopped';
                statusBadge.className = 'status-badge stopped';
            } else {
                playBtn.disabled = false;
                stopBtn.disabled = true;
                statusBadge.textContent = 'Idle';
                statusBadge.className = 'status-badge idle';
            }
        });
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
            rawPid != null && rawPid !== '' ? String(rawPid) : null;
        const st = String(statusPayload?.status || '').toLowerCase();

        ids.forEach((id) => {
            if (pid === id && st === 'playing') {
                this.setPlaybackRowState(id, 'playing');
            } else if (pid === id && st === 'stopped') {
                this.setPlaybackRowState(id, 'stopped');
            } else {
                this.setPlaybackRowState(id, 'idle');
            }
        });
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

        const { updateTimestamp = true } = options || {};
        const newSrc = `${CONFIG.api.endpoints.previewImage}?t=${Date.now()}`;

        elements.previewImage.onload = function() {
            this.style.display = 'block';
            state.previewLoadAttempts = 0;
            if (updateTimestamp && elements.mpvLastUpdate) {
                elements.mpvLastUpdate.textContent = new Date().toLocaleTimeString();
            }
        };

        elements.previewImage.onerror = function() {
            state.previewLoadAttempts++;
            
            if (state.previewLoadAttempts >= CONFIG.maxImageLoadAttempts && !state.fallbackPreviewUsed) {
                console.warn('Max preview load attempts reached, using fallback');
                state.fallbackPreviewUsed = true;
                this.src = `${CONFIG.defaultPreview}?t=${Date.now()}`;
            } else if (!state.fallbackPreviewUsed) {
                setTimeout(() => {
                    this.src = `${CONFIG.api.endpoints.previewImage}?t=${Date.now()}`;
                }, 3000);
            }
        };
    
        elements.previewImage.style.display = 'none';
        elements.previewImage.src = newSrc;
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

    updateLogoFileSelection(file) {
        const selectedLabel = elements.logoSelectedFile;
        if (selectedLabel) {
            selectedLabel.textContent = file ? file.name : 'No file selected';
        }

        if (elements.uploadLogoBtn) {
            elements.uploadLogoBtn.disabled = !file;
        }
    }
};

// Event handlers
const handlers = {
    async init() {
        try {
            console.log('Initializing application...');
            
            await this.ensureTableBodyExists();
            console.log('Playlist table element found:', elements.playlistTableBody);

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

            console.log('Received playlists:', playlists);

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

            try {
                ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
            } catch (e) {
                console.warn('Failed to apply playback status to UI:', e);
            }

            ui.updateLogo(settings.display?.logo);
            // Initial paint: if Auto preview is enabled, show that preview is refreshing.
            // Otherwise keep the timestamp unchanged to avoid implying background capture.
            const initPreviewAutoSec = Number(settings?.display?.preview_auto_interval_sec || 0);
            ui.updatePreviewImage({ updateTimestamp: initPreviewAutoSec > 0 });

            this.setupEventListeners();
            this.startAutoRefresh();
            this.startPreviewRefresh(settings);

        } catch (error) {
            console.error('Initialization failed:', error);
            showError('Failed to initialize application');
        }
    },

    async ensureTableBodyExists() {
        return new Promise((resolve) => {
            const checkTableBody = () => {
                if (elements.playlistTableBody) {
                    resolve();
                } else {
                    const table = document.querySelector('#playlist-table');
                    if (table) {
                        const tableBody = document.createElement('tbody');
                        tableBody.id = 'playlist-table-body';
                        table.appendChild(tableBody);
                        elements.playlistTableBody = tableBody;
                        resolve();
                    } else {
                        setTimeout(checkTableBody, 100);
                    }
                }
            };
            checkTableBody();
        });
    },

    setupEventListeners() {
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

                state.playlists = [...state.playlists, {
                    id: response.playlist_id,
                    name: formData.get('name'),
                    customer: formData.get('customer') || '',
                    files_count: 0
                }];
                
                ui.renderPlaylists(state.playlists);
                ui.applyPlaybackStatusFromServer(state.playbackStatus, state.playlists);
                ui.toggleModal(false);
                elements.playlistForm.reset();
                showAlert('Playlist created successfully', 'success');
            } catch (error) {
                showError('Failed to create playlist: ' + error.message);
            }
        });

        // Logo upload
        elements.uploadLogoBtn?.addEventListener('click', async () => {
            const fileInput = elements.logoFileInput;
            if (!fileInput.files || fileInput.files.length === 0) {
                showError('Please select a logo file first');
                return;
            }

            const file = fileInput.files[0];
            if (!file.type.match('image.*')) {
                showError('Only image files are allowed');
                return;
            }

            if (file.size > 5 * 1024 * 1024) {
                showError('File size should be less than 5MB');
                return;
            }

            const btnText = elements.uploadLogoBtn.querySelector('.btn-text');
            const spinner = elements.uploadLogoBtn.querySelector('.loading-spinner');
            btnText.style.display = 'none';
            spinner.style.display = 'inline-block';
            elements.uploadLogoBtn.disabled = true;

            try {
                const formData = new FormData(elements.logoForm);
                const result = await api.uploadLogo(formData);
                
                state.fallbackLogoUsed = false;
                state.logoLoadAttempts = 0;
                
                ui.updateLogo(result.filename);
                showAlert('Logo updated successfully', 'success');
                
                fileInput.value = '';
                ui.updateLogoFileSelection(null);
                
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
                ui.updateLogo(state.currentSettings.display?.logo);
            } finally {
                btnText.style.display = 'inline-block';
                spinner.style.display = 'none';
                elements.uploadLogoBtn.disabled = false;
            }
        });

        // Logo file preview
        elements.logoFileInput?.addEventListener('change', (e) => {
            const file = e.target.files && e.target.files[0] ? e.target.files[0] : null;
            ui.updateLogoFileSelection(file);
            if (file) ui.previewLogo(file);
        });

        // Refresh preview button
        elements.refreshPreviewBtn?.addEventListener('click', async () => {
            if (state.isPreviewRefreshing) return;
            
            try {
                elements.refreshPreviewBtn.disabled = true;
                const icon = elements.refreshPreviewBtn.querySelector('i');
                if (icon) {
                    icon.className = 'fas fa-spinner fa-spin';
                }
                
                const r = await api.refreshPreview();
                const now = Date.now();
                const retryMs = Math.max(0, Math.round((r?.retry_in_sec || 0) * 1000));
                if (r?.skipped) {
                    // Capture was throttled on server; just reload existing image and inform user.
                    state.previewCaptureCooldownUntil = now + retryMs;
                    ui.updatePreviewImage({ updateTimestamp: false });
                    showAlert('Preview is up to date', 'info');
                } else if (r?.success) {
                    // Service may still be writing the file; reload after a short delay.
                    setTimeout(() => ui.updatePreviewImage({ updateTimestamp: true }), 1200);
                    showAlert('Preview refreshed', 'success');
                } else {
                    throw new Error(r?.error || 'Preview refresh failed');
                }
            } catch (error) {
                console.warn('Failed to refresh preview:', error);
                showError('Failed to refresh preview');
            } finally {
                elements.refreshPreviewBtn.disabled = false;
                const icon = elements.refreshPreviewBtn.querySelector('i');
                if (icon) {
                    icon.className = 'fas fa-sync-alt';
                }
            }
        });

        // Playlist actions
        elements.playlistTable?.addEventListener('click', async (e) => {
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
                            const icon = btn.querySelector('i');
                            if (icon) {
                                icon.className = 'fas fa-spinner fa-spin';
                            }
                            btn.disabled = true;

                            const delResult = await api.deletePlaylist(playlistId);
                            if (delResult && typeof delResult === 'object' && delResult.success === false) {
                                throw new Error(delResult.error || 'Delete failed');
                            }
                            
                            // Remove the playlist row immediately
                            const row = document.querySelector(`tr[data-id="${playlistId}"]`);
                            if (row) {
                                row.remove();
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
                            const icon = btn.querySelector('i');
                            if (icon) {
                                icon.className = 'fas fa-trash';
                            }
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

    startAutoRefresh() {
        if (state.refreshIntervalId) {
            clearInterval(state.refreshIntervalId);
        }

        state.refreshIntervalId = setInterval(async () => {
            try {
                const now = Date.now();
                const shouldRefreshSystem =
                    !state.lastSystemRefreshAt ||
                    (now - state.lastSystemRefreshAt) >= CONFIG.refreshSystemInterval;

                const requests = [
                    api.getSettings(),
                    api.getPlaybackStatus().catch(() => null),
                    shouldRefreshSystem ? api.getSystemStatus().catch(() => null) : Promise.resolve(null),
                    shouldRefreshSystem ? api.getNetworkStatus().catch(() => null) : Promise.resolve(null),
                ];

                const [settings, playbackStatusResp, systemStatusResp, networkStatusResp] = await Promise.all(requests);

                const latestPlaybackStatus =
                    playbackStatusResp &&
                    typeof playbackStatusResp.status === 'object' &&
                    playbackStatusResp.status !== null &&
                    !Array.isArray(playbackStatusResp.status)
                        ? playbackStatusResp.status
                        : state.playbackStatus;
                const nextSystemStatus =
                    systemStatusResp &&
                    typeof systemStatusResp.status === 'object' &&
                    systemStatusResp.status !== null &&
                    !Array.isArray(systemStatusResp.status)
                        ? systemStatusResp.status
                        : null;
                const nextNetworkStatus =
                    networkStatusResp &&
                    typeof networkStatusResp.network === 'object' &&
                    networkStatusResp.network !== null &&
                    !Array.isArray(networkStatusResp.network)
                        ? networkStatusResp.network
                        : null;

                // Cheaper comparisons than repeated JSON.stringify on large nested objects.
                const settingsChanged = stableStringify(state.currentSettings) !== stableStringify(settings);
                const playbackChanged = stableStringify(state.playbackStatus) !== stableStringify(latestPlaybackStatus);
                const systemChanged =
                    shouldRefreshSystem &&
                    nextSystemStatus &&
                    stableStringify(state.systemStatus) !== stableStringify(nextSystemStatus);
                const networkChanged =
                    shouldRefreshSystem &&
                    nextNetworkStatus &&
                    stableStringify(state.networkStatus) !== stableStringify(nextNetworkStatus);

                if (settingsChanged) {
                    state.currentSettings = settings;
                    ui.updateLogo(settings.display?.logo);
                    // If Auto preview interval changed in Settings, reflect it here.
                    this.startPreviewRefresh(settings);
                }
                state.playbackStatus = latestPlaybackStatus;
                if (shouldRefreshSystem) {
                    if (nextSystemStatus) state.systemStatus = nextSystemStatus;
                    if (nextNetworkStatus) state.networkStatus = nextNetworkStatus;
                    state.lastSystemRefreshAt = now;
                }

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
            }
        }, CONFIG.refreshPlaybackInterval);
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
            // Do not trigger expensive capture here; only refresh the <img> src.
            // Also avoid hammering the browser cache if the user just requested a manual capture.
            if (Date.now() < (state.previewCaptureCooldownUntil || 0)) return;
            ui.updatePreviewImage({ updateTimestamp: true });
        }, intervalMs);
    },

    cleanup() {
        if (state.refreshIntervalId) {
            clearInterval(state.refreshIntervalId);
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
