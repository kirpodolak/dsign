import { t, getUiLang, applyI18n } from './i18n.js';

// Utility functions that can be shared across modules
export function showAlert(message, type = 'info') {
    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.textContent = message;
    alert.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 20px;
        border-radius: 4px;
        background-color: ${getAlertColor(type)};
        color: white;
        z-index: 1000;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        animation: fadeIn 0.3s ease-out;
    `;

    document.body.appendChild(alert);

    setTimeout(() => {
        alert.style.opacity = '0';
        setTimeout(() => alert.remove(), 300);
    }, 3000);
}

function getAlertColor(type) {
    switch (type) {
        case 'success': return '#28a745';
        case 'error': return '#dc3545';
        case 'warning': return '#ffc107';
        default: return '#17a2b8';
    }
}

export function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

// Main Settings Module
export class SettingsManager {
    constructor() {
        this.performance = this.createPerformanceTracker('settings');
        this.elements = {
            profileNameInput: document.getElementById('profile-name'),
            profileTypeSelect: document.getElementById('profile-type'),
            saveProfileBtn: document.getElementById('save-profile'),
            settingsEditor: document.getElementById('profile-settings-editor'),
            currentSettingsPanel: document.getElementById('current-settings-panel'),
            mpvSettingsForm: document.getElementById('mpv-settings-form'),
            profilesGrid: document.getElementById('profiles-grid'),
            playlistAssignments: document.getElementById('playlist-assignments'),
            playlistOverrides: document.getElementById('playlist-overrides'),
            systemDashboard: document.getElementById('system-dashboard'),
            currentProfileIndicator: document.getElementById('current-profile-indicator'),
            displayModeSelect: document.getElementById('display-mode-select'),
            applyDisplayModeBtn: document.getElementById('apply-display-mode'),
            previewAutoSelect: document.getElementById('preview-auto-select'),
            transcodeEnabled: document.getElementById('transcode-enabled'),
            transcodeResolution: document.getElementById('transcode-resolution'),
            transcodeFps: document.getElementById('transcode-fps'),
            idleLogoRotate: document.getElementById('idle-logo-rotate'),
            mpvAdvancedBackdrop: document.getElementById('mpv-advanced-backdrop'),
            btnMpvAdvanced: document.getElementById('btn-mpv-advanced'),
            mpvAdvancedSave: document.getElementById('mpv-advanced-save'),
            mpvAdvancedCancel: document.getElementById('mpv-advanced-cancel'),
            mpvAdvancedClose: document.getElementById('mpv-advanced-close'),
        };

        this.state = {
            currentProfile: null,
            currentSettings: {},
            playlists: [],
            playlistOverrides: [],
            settingsSchema: {},
            systemStatus: null,
            systemPollTimer: null,
            systemStatusLoading: false,
            initStarted: false,
            initialized: false,
            /** Local audio state during drag (0–100, muted) */
            audioLocal: { volume: null, muted: null },
            _overrideSaveTimers: new Map(),
            _globalSaveTimer: null,
        };

        this.init();
    }

    createPerformanceTracker(label) {
        const startedAt = performance.now();
        return {
            label,
            startedAt,
            marks: [],
            mark(name) {
                const now = performance.now();
                this.marks.push({ name, ms: Number((now - startedAt).toFixed(1)) });
            },
            flush(reason = 'ready') {
                this.mark(reason);
                const details = this.marks.map((m) => `${m.name}:${m.ms}ms`).join(' | ');
                console.info(`[perf:${label}] ${details}`);
            }
        };
    }

    async init() {
        if (this.state.initStarted || this.state.initialized) {
            return;
        }
        this.state.initStarted = true;
        this.performance.mark('init-start');
        try {
            await this.loadPlaylistOverrides().catch(() => []);
            this.performance.mark('overrides-loaded');
            this.renderPlaylistOverrides();

            await Promise.all([
                this.loadCurrentSettings(),
                this.loadSettingsSchema(),
                this.loadIdleLogoRotation().catch(() => 0)
            ]);
            this.performance.mark('settings-schema-current-loaded');

            this.renderStatusDashboardSkeleton();
            await this.refreshSystemStatus({ startPolling: true }).catch(() => {});
            this.performance.mark('system-status-loaded');
            this._bindVolumeKnob();
            this.renderSettingsForm();
            this.performance.mark('settings-rendered');
            this.setupEventListeners();
            this.startSystemPolling();
            this.state.initialized = true;
            this.performance.flush('init-complete');

        } catch (error) {
            console.error('Initialization error:', error);
            showAlert('Failed to initialize settings. Please try again.', 'error');
            this.state.initStarted = false;
            this.performance.flush('init-failed');
        }
    }

    async loadPlaylistOverrides() {
        const response = await fetch('/api/playlists/overrides', { credentials: 'include' });
        if (!response.ok) throw new Error('Failed to load playlist overrides');
        const data = await response.json();
        if (!data.success) throw new Error(data.error || 'Invalid playlist overrides data');
        this.state.playlistOverrides = Array.isArray(data.playlists) ? data.playlists : [];
        return this.state.playlistOverrides;
    }

    async loadIdleLogoRotation() {
        if (!this.elements.idleLogoRotate) return 0;
        const resp = await fetch('/api/media/idle_logo_rotation', { credentials: 'include' });
        if (!resp.ok) throw new Error('Failed to load idle logo rotation');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Invalid idle rotation data');
        const rotate = Number(data.rotate ?? 0);
        this.elements.idleLogoRotate.value = String([0, 90, 180, 270].includes(rotate) ? rotate : 0);
        return rotate;
    }

    _formatBytes(bytes) {
        if (bytes == null || Number.isNaN(Number(bytes))) return '—';
        const b = Number(bytes);
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let u = 0;
        let v = b;
        while (v >= 1024 && u < units.length - 1) {
            v /= 1024;
            u += 1;
        }
        return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
    }

    renderStatusDashboardSkeleton() {
        if (!this.elements.systemDashboard) return;
        const lang = getUiLang();
        this.elements.systemDashboard.innerHTML = `
            <div class="status-tile status-tile--donut">
              <div class="donut" data-k="diskDonut" style="--p:0;"></div>
              <div class="status-tile__meta">
                <p class="status-tile__title" data-i18n="dash_title_storage">${t('dash_title_storage', lang)}</p>
                <p class="status-tile__value" data-k="mediaFree">—</p>
                <p class="status-tile__sub" data-k="mediaSub"></p>
              </div>
            </div>
            <div class="status-tile status-tile--donut">
              <div class="donut" data-k="tempDonut" style="--p:0;"></div>
              <div class="status-tile__meta">
                <p class="status-tile__title" data-i18n="dash_title_cput">${t('dash_title_cput', lang)}</p>
                <p class="status-tile__value" data-k="cpuTemp">—</p>
                <p class="status-tile__sub" data-k="cpuTempSub"></p>
              </div>
            </div>
            <div class="status-tile status-tile--donut">
              <div class="donut" data-k="cpuDonut" style="--p:0;"></div>
              <div class="status-tile__meta">
                <p class="status-tile__title" data-i18n="dash_title_cpuu">${t('dash_title_cpuu', lang)}</p>
                <p class="status-tile__value" data-k="cpuUsage">—</p>
                <p class="status-tile__sub" data-k="cpuUsageSub"></p>
              </div>
            </div>
            <div class="status-tile status-tile--audio">
              <div class="volume-knob" data-k="audioKnobWrap">
                <div class="volume-knob__ticks" aria-hidden="true"></div>
                <div class="donut" data-k="audioDonut" style="--p:0;"></div>
                <button type="button" class="volume-knob__center" data-k="audioMuteBtn" data-i18n-title="value_mute" title="${t('value_mute', lang)}">M</button>
              </div>
              <div class="status-tile__meta">
                <p class="status-tile__title" data-i18n="dash_title_audio">${t('dash_title_audio', lang)}</p>
                <p class="status-tile__value" data-k="audioValue">—</p>
                <p class="status-tile__sub" data-k="audioSub">${t('dash_audio_hint', lang)}</p>
              </div>
            </div>
        `;
        applyI18n();
    }

    refreshDashboardAfterLangChange() {
        this.renderStatusDashboardSkeleton();
        this._bindVolumeKnob();
        if (this.state.systemStatus) {
            this.applyNonAudioStatusToDashboard();
            this._applyAudioToDashboard(this.state.systemStatus?.audio || {});
        }
    }

    _bindVolumeKnob() {
        const wrap = this._qsDashboard('audioKnobWrap');
        const donut = this._qsDashboard('audioDonut');
        const btn = this._qsDashboard('audioMuteBtn');
        if (!wrap || !donut || !btn) return;

        let drag = null;

        const applyLocalToUi = () => {
            const v = this.state.audioLocal.volume;
            const m = this.state.audioLocal.muted;
            const lang = getUiLang();
            if (v != null) donut.style.setProperty('--p', String(Math.max(0, Math.min(100, v))));
            const valEl = this._qsDashboard('audioValue');
            const subEl = this._qsDashboard('audioSub');
            if (valEl) valEl.textContent = v != null ? `${Math.round(v)}%` : '—';
            if (subEl) subEl.textContent = m ? t('dash_audio_muted', lang) : t('dash_audio_hint', lang);
            btn.classList.toggle('is-muted', Boolean(m));
            btn.textContent = m ? 'Ø' : 'M';
        };

        const scheduleAudioPost = () => {
            clearTimeout(this._audioPostTimer);
            this._audioPostTimer = setTimeout(() => this._flushAudioPost(), 450);
        };

        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const cur = this.state.audioLocal.muted;
            this.state.audioLocal.muted = !cur;
            applyLocalToUi();
            scheduleAudioPost();
        });

        donut.addEventListener('pointerdown', (e) => {
            if (e.target === btn || btn.contains(e.target)) return;
            e.preventDefault();
            let base =
                this.state.audioLocal.volume != null
                    ? this.state.audioLocal.volume
                    : (() => {
                          const raw = donut.style.getPropertyValue('--p').trim();
                          const parsed = parseFloat(raw);
                          if (!Number.isNaN(parsed)) return parsed;
                          const sv = this.state.systemStatus?.audio?.volume_percent;
                          return typeof sv === 'number' ? sv : 50;
                      })();
            base = Math.max(0, Math.min(100, base));
            drag = { y0: e.clientY, v0: base };
            donut.setPointerCapture(e.pointerId);
        });

        donut.addEventListener('pointermove', (e) => {
            if (!drag) return;
            e.preventDefault();
            const dy = drag.y0 - e.clientY;
            const v = Math.max(0, Math.min(100, drag.v0 + dy * 0.35));
            this.state.audioLocal.volume = v;
            if (v > 0) this.state.audioLocal.muted = false;
            applyLocalToUi();
            scheduleAudioPost();
        });

        const endDrag = (e) => {
            if (!drag) return;
            drag = null;
            try {
                donut.releasePointerCapture(e.pointerId);
            } catch (_) { /* noop */ }
        };
        donut.addEventListener('pointerup', endDrag);
        donut.addEventListener('pointercancel', endDrag);
    }

    async _flushAudioPost() {
        const vol = this.state.audioLocal.volume;
        const muted = this.state.audioLocal.muted;
        try {
            const resp = await fetch('/api/system/audio', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                credentials: 'include',
                body: JSON.stringify({
                    volume_percent: vol != null ? Math.round(vol) : null,
                    muted: muted != null ? Boolean(muted) : null,
                }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
            if (data.audio) {
                this.state.systemStatus = this.state.systemStatus || {};
                this.state.systemStatus.audio = data.audio;
                this._applyAudioToDashboard(data.audio);
            }
            this.state.audioLocal = { volume: null, muted: null };
            // Re-fetch status so the donut matches server truth (avoids stale cached /api/system/status audio).
            await this.refreshSystemStatus().catch(() => {});
        } catch (err) {
            console.error('Audio save failed:', err);
        }
    }

    _qsDashboard(k) {
        return this.elements.systemDashboard?.querySelector(`[data-k="${k}"]`);
    }

    startSystemPolling() {
        if (this.state.systemPollTimer) return;
        this.state.systemPollTimer = setInterval(() => {
            if (document.hidden || this.state.systemStatusLoading) {
                return;
            }
            this.refreshSystemStatus().catch(() => {});
        }, 10000);
    }

    stopSystemPolling() {
        if (this.state.systemPollTimer) {
            clearInterval(this.state.systemPollTimer);
            this.state.systemPollTimer = null;
        }
    }

    async refreshSystemStatus({ startPolling = false } = {}) {
        if (startPolling) {
            this.startSystemPolling();
        }
        if (this.state.systemStatusLoading) {
            return;
        }
        this.state.systemStatusLoading = true;
        try {
            const resp = await fetch('/api/system/status', { credentials: 'include' });
            if (!resp.ok) throw new Error('Failed to load system status');
            const data = await resp.json();
            if (!data.success) throw new Error(data.error || 'Invalid system status data');
            this.state.systemStatus = data.status || null;
            this.applyNonAudioStatusToDashboard();
            const audioIdle =
                this.state.audioLocal.volume == null && this.state.audioLocal.muted == null;
            if (audioIdle) {
                this._applyAudioToDashboard(this.state.systemStatus?.audio || {});
            }
        } finally {
            this.state.systemStatusLoading = false;
        }
    }

    cleanup() {
        this.stopSystemPolling();
        clearTimeout(this._audioPostTimer);
        clearTimeout(this._autosavePreviewT);
        clearTimeout(this._autosaveTranscodeT);
        clearTimeout(this._autosaveIdleT);
        this.state._overrideSaveTimers.forEach((timerId) => clearTimeout(timerId));
        this.state._overrideSaveTimers.clear();
    }

    _applyAudioToDashboard(audio) {
        if (!this.elements.systemDashboard) return;
        const lang = getUiLang();
        const audioValue = this._qsDashboard('audioValue');
        const audioSub = this._qsDashboard('audioSub');
        const audioDonut = this._qsDashboard('audioDonut');
        const muteBtn = this._qsDashboard('audioMuteBtn');
        const available = Boolean(audio?.available);
        if (!available) {
            if (audioValue) audioValue.textContent = t('value_na', lang);
            if (audioSub) audioSub.textContent = t('audio_unavailable', lang);
            if (muteBtn) muteBtn.disabled = true;
            if (audioDonut) audioDonut.style.setProperty('--p', '0');
            return;
        }
        const vol = audio.volume_percent;
        const isMuted = Boolean(audio.muted);
        if (audioValue) audioValue.textContent = vol != null ? `${vol}%` : '—';
        if (audioSub) audioSub.textContent = isMuted ? t('dash_audio_muted', lang) : t('dash_audio_hint', lang);
        if (audioDonut) {
            if (vol != null) {
                audioDonut.style.setProperty('--p', String(Math.max(0, Math.min(100, Number(vol)))));
            } else {
                audioDonut.style.setProperty('--p', '0');
            }
        }
        if (muteBtn) {
            muteBtn.disabled = false;
            muteBtn.classList.toggle('is-muted', isMuted);
            muteBtn.textContent = isMuted ? 'Ø' : 'M';
        }
    }

    applyNonAudioStatusToDashboard() {
        const st = this.state.systemStatus;
        if (!st || !this.elements.systemDashboard) return;
        const lang = getUiLang();

        const media = st.storage?.media || st.storage?.root;
        if (media) {
            const free = this._formatBytes(media.free);
            const total = this._formatBytes(media.total);
            const usedPct = media.used_percent != null
                ? `${media.used_percent}% ${t('word_used', lang)}`
                : '';
            const pFree = this._qsDashboard('mediaFree');
            const pSub = this._qsDashboard('mediaSub');
            if (pFree) pFree.textContent = free;
            if (pSub) pSub.textContent = usedPct ? `${usedPct} • ${total} ${t('word_total', lang)}` : '';

            const donut = this._qsDashboard('diskDonut');
            if (donut && media.used_percent != null) {
                donut.style.setProperty('--p', String(Math.max(0, Math.min(100, media.used_percent))));
            }
        }

        const temp = st.cpu?.temp_c;
        const pTemp = this._qsDashboard('cpuTemp');
        if (pTemp) pTemp.textContent = (temp != null ? `${temp}°C` : '—');

        const tempDonut = this._qsDashboard('tempDonut');
        if (tempDonut && temp != null) {
            const p = Math.max(0, Math.min(100, (Number(temp) / 85) * 100));
            tempDonut.style.setProperty('--p', String(p));
        }

        const usage = st.cpu?.usage_percent ?? null;
        const loadFallback = st.cpu?.load_percent ?? null;
        const pUsage = this._qsDashboard('cpuUsage');
        const pUsageSub = this._qsDashboard('cpuUsageSub');
        if (pUsage) pUsage.textContent = (usage != null ? `${usage}%` : (loadFallback != null ? `${loadFallback}%` : '—'));
        if (pUsageSub) pUsageSub.textContent = usage != null ? t('dash_cpu_from_stat', lang) : t('dash_cpu_estimated', lang);
        const cpuDonut = this._qsDashboard('cpuDonut');
        if (cpuDonut) {
            const val = usage != null ? Number(usage) : (loadFallback != null ? Number(loadFallback) : 0);
            cpuDonut.style.setProperty('--p', String(Math.max(0, Math.min(100, val))));
        }
    }

    // Data loading methods
    async loadProfiles() {
        try {
            const response = await fetch('/api/profiles');
            if (!response.ok) throw new Error('Failed to load profiles');
            
            const data = await response.json();
            if (data.success) {
                this.state.profiles = Array.isArray(data.profiles) ? data.profiles : [];
                return this.state.profiles;
            } else {
                throw new Error(data.error || 'Invalid profiles data');
            }
        } catch (error) {
            console.error('Error loading profiles:', error);
            showAlert('Failed to load profiles. ' + error.message, 'error');
            this.state.profiles = [];
            return [];
        }
    }

    async loadPlaylists() {
        try {
            const response = await fetch('/api/playlists');
            if (!response.ok) throw new Error('Failed to load playlists');
            
            const data = await response.json();
            if (data.success) {
                this.state.playlists = Array.isArray(data.playlists) ? data.playlists : [];
                return this.state.playlists;
            } else {
                throw new Error(data.error || 'Invalid playlists data');
            }
        } catch (error) {
            console.error('Error loading playlists:', error);
            showAlert('Failed to load playlists. ' + error.message, 'error');
            this.state.playlists = [];
            return [];
        }
    }

    async loadAssignments() {
        try {
            const response = await fetch('/api/profiles/assignments');
            if (!response.ok) throw new Error('Failed to load assignments');
            
            const data = await response.json();
            if (data.success) {
                this.state.assignments = data.assignments && typeof data.assignments === 'object' 
                    ? data.assignments 
                    : {};
                return this.state.assignments;
            } else {
                throw new Error(data.error || 'Invalid assignments data');
            }
        } catch (error) {
            console.error('Error loading assignments:', error);
            showAlert('Failed to load profile assignments', 'error');
            this.state.assignments = {};
            return {};
        }
    }

    async loadCurrentSettings() {
        try {
            const response = await fetch('/api/settings/current');
            if (!response.ok) throw new Error('Failed to load current settings');
            
            const data = await response.json();
            if (data.success) {
                this.state.currentSettings = data.settings && typeof data.settings === 'object' 
                    ? data.settings 
                    : {};
                this.state.currentProfile = data.profile || null;
                this.applyGlobalSettingsToControls();
                return { settings: this.state.currentSettings, profile: this.state.currentProfile };
            } else {
                throw new Error(data.error || 'Invalid settings data');
            }
        } catch (error) {
            console.error('Error loading current settings:', error);
            showAlert('Failed to load current settings', 'error');
            this.state.currentSettings = {};
            this.state.currentProfile = null;
            return { settings: {}, profile: null };
        }
    }

    async loadSettingsSchema() {
        try {
            const response = await fetch('/api/settings/schema');
            if (!response.ok) throw new Error('Failed to load settings schema');
            
            const data = await response.json();
            if (data.success) {
                this.state.settingsSchema = data.schema && typeof data.schema === 'object' 
                    ? data.schema 
                    : {};
                return this.state.settingsSchema;
            } else {
                throw new Error(data.error || 'Invalid schema data');
            }
        } catch (error) {
            console.error('Error loading settings schema:', error);
            showAlert('Failed to load settings schema', 'error');
            this.state.settingsSchema = {};
            return {};
        }
    }

    renderPlaylistOverrides() {
        if (!this.elements.playlistOverrides) return;
        const el = this.elements.playlistOverrides;
        el.innerHTML = '';

        // Header (desktop)
        const header = document.createElement('div');
        header.className = 'playlist-ov-header';
        header.innerHTML = `
            <div>Playlist</div>
            <div>Override</div>
            <div>Output</div>
            <div>Rotate</div>
            <div>Fit</div>
            <div>Mute</div>
            <div>Status</div>
        `;
        el.appendChild(header);

        (this.state.playlistOverrides || []).forEach((row) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'playlist-ov-row';
            wrapper.dataset.playlistId = String(row.playlist_id);

            const enabled = Boolean(row.has_overrides);
            const rotate = Number(row.overrides?.video_rotate ?? 0);
            const panscan = Number(row.overrides?.panscan ?? 0);
            const mute = Boolean(row.overrides?.mute ?? false);
            const dwidth = row.overrides?.dwidth ?? null;
            const dheight = row.overrides?.dheight ?? null;
            const disabledClass = enabled ? '' : 'ov-muted';
            const isDefault = !enabled;
            const outPreset =
                (dwidth === 1920 && dheight === 1080) ? '1080p'
                : (dwidth === 1280 && dheight === 720) ? '720p'
                : 'auto';

            wrapper.innerHTML = `
                <div class="playlist-ov-name">
                  <span class="playlist-name"></span>
                </div>

                <label class="checkbox-row">
                  <input type="checkbox" class="ov-enabled" ${enabled ? 'checked' : ''} data-playlist-id="${row.playlist_id}">
                  <span>On</span>
                </label>

                <div class="segmented ${disabledClass}" data-ov="out" data-playlist-id="${row.playlist_id}">
                  <button type="button" class="segmented-btn ${outPreset==='auto'?'is-active':''}" data-value="auto">Auto</button>
                  <button type="button" class="segmented-btn ${outPreset==='1080p'?'is-active':''}" data-value="1080p">1080p</button>
                  <button type="button" class="segmented-btn ${outPreset==='720p'?'is-active':''}" data-value="720p">720p</button>
                </div>

                <div class="segmented ${disabledClass}" data-ov="rotate" data-playlist-id="${row.playlist_id}">
                  <button type="button" class="segmented-btn ${rotate===0?'is-active':''}" data-value="0">0°</button>
                  <button type="button" class="segmented-btn ${rotate===90?'is-active':''}" data-value="90">90°</button>
                  <button type="button" class="segmented-btn ${rotate===180?'is-active':''}" data-value="180">180°</button>
                  <button type="button" class="segmented-btn ${rotate===270?'is-active':''}" data-value="270">270°</button>
                </div>

                <div class="segmented ${disabledClass}" data-ov="fit" data-playlist-id="${row.playlist_id}">
                  <button type="button" class="segmented-btn ${panscan<=0.01?'is-active':''}" data-value="0">Fit</button>
                  <button type="button" class="segmented-btn ${panscan>=0.99?'is-active':''}" data-value="1">Fill</button>
                </div>

                <div class="segmented ${disabledClass}" data-ov="pmute" data-playlist-id="${row.playlist_id}">
                  <button type="button" class="segmented-btn ${!mute?'is-active':''}" data-value="0">Sound</button>
                  <button type="button" class="segmented-btn ${mute?'is-active':''}" data-value="1">Mute</button>
                </div>

                <div class="playlist-ov-status">
                  ${isDefault ? '<span class="badge badge--default">Default</span>' : '<span class="badge">Override</span>'}
                </div>
            `;
            el.appendChild(wrapper);
            const nameEl = wrapper.querySelector('.playlist-name');
            if (nameEl) {
                const nm = String(row.playlist_name ?? '');
                nameEl.textContent = nm;
                nameEl.title = nm;
            }
        });

        el.querySelectorAll('.ov-enabled').forEach((cb) => {
            cb.addEventListener('change', () => {
                const pid = Number(cb.dataset.playlistId);
                if (pid) this._schedulePlaylistOverrideSave(pid);
            });
        });
    }

    _collectPlaylistOverridePayload(playlistId) {
        const row = document.querySelector(`.playlist-ov-row[data-playlist-id="${playlistId}"]`);
        if (!row) return null;
        const enabled = Boolean(row.querySelector('.ov-enabled')?.checked);
        const outVal = row.querySelector('.segmented[data-ov="out"] .segmented-btn.is-active')?.dataset?.value || 'auto';
        const rotate = Number(row.querySelector('.segmented[data-ov="rotate"] .segmented-btn.is-active')?.dataset?.value || 0);
        const fit = Number(row.querySelector('.segmented[data-ov="fit"] .segmented-btn.is-active')?.dataset?.value || 0);
        const pmute = row.querySelector('.segmented[data-ov="pmute"] .segmented-btn.is-active')?.dataset?.value || '0';
        const mute = pmute === '1';
        const outMap = {
            auto: { dwidth: null, dheight: null },
            '1080p': { dwidth: 1920, dheight: 1080 },
            '720p': { dwidth: 1280, dheight: 720 },
        };
        const out = outMap[outVal] || outMap.auto;
        return {
            playlist_id: playlistId,
            enabled,
            video_rotate: rotate,
            panscan: fit,
            mute,
            dwidth: out.dwidth,
            dheight: out.dheight,
        };
    }

    _schedulePlaylistOverrideSave(playlistId) {
        const timers = this.state._overrideSaveTimers;
        const prev = timers.get(playlistId);
        if (prev) clearTimeout(prev);
        timers.set(
            playlistId,
            setTimeout(() => {
                timers.delete(playlistId);
                this._postPlaylistOverride(playlistId).catch((err) => {
                    console.error(err);
                    showAlert(err?.message || 'Failed to save playlist overrides', 'error');
                });
            }, 450),
        );
    }

    async _postPlaylistOverride(playlistId) {
        const payload = this._collectPlaylistOverridePayload(playlistId);
        if (!payload) return;
        const resp = await fetch('/api/playlists/overrides', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
            },
            credentials: 'include',
            body: JSON.stringify(payload),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
        await this.loadPlaylistOverrides();
        this.renderPlaylistOverrides();
    }

    applyGlobalSettingsToControls() {
        // Display preset (global)
        const preset = this.state.currentSettings?.display?.hdmi_mode_preset || 'auto';
        if (this.elements.displayModeSelect) this.elements.displayModeSelect.value = preset;

        const interval = String(this.state.currentSettings?.display?.preview_auto_interval_sec ?? 0);
        if (this.elements.previewAutoSelect) this.elements.previewAutoSelect.value = interval;

        // Transcode settings (global)
        const display = this.state.currentSettings?.display || {};
        if (this.elements.transcodeEnabled) this.elements.transcodeEnabled.value = String(Boolean(display.auto_transcode_videos));
        if (this.elements.transcodeResolution) this.elements.transcodeResolution.value = String(display.transcode_target_resolution || '1920x1080');
        if (this.elements.transcodeFps) this.elements.transcodeFps.value = String(display.transcode_target_fps || 25);

        // Reflect hidden inputs into segmented UI
        document.querySelectorAll('.segmented[data-seg]').forEach((seg) => {
            const key = seg.dataset.seg;
            let v = null;
            if (key === 'preview_auto_interval_sec') v = this.elements.previewAutoSelect?.value;
            if (key === 'auto_transcode_videos') v = this.elements.transcodeEnabled?.value;
            if (key === 'transcode_target_resolution') v = this.elements.transcodeResolution?.value;
            if (key === 'transcode_target_fps') v = this.elements.transcodeFps?.value;
            if (key === 'hdmi_mode_preset') v = this.elements.displayModeSelect?.value;
            if (key === 'idle_logo_rotate') v = this.elements.idleLogoRotate?.value;
            if (v != null) this._setSegmentedValue(seg, v);
        });
    }

    renderSettingsForm() {
        const container = this.elements.mpvSettingsForm;
        const advBtn = this.elements.btnMpvAdvanced;
        if (!container) return;

        container.innerHTML = '';

        if (!this.state.settingsSchema || Object.keys(this.state.settingsSchema).length === 0) {
            if (advBtn) advBtn.hidden = true;
            return;
        }

        if (advBtn) advBtn.hidden = false;

        for (const [key, setting] of Object.entries(this.state.settingsSchema)) {
            if (!setting || typeof setting !== 'object') continue;

            const wrapper = document.createElement('div');
            wrapper.className = 'form-group';

            const label = document.createElement('label');
            label.textContent = setting.label || key;
            label.htmlFor = `setting-${key}`;
            wrapper.appendChild(label);

            let input;
            let currentValue = this.state.currentSettings[key] ?? setting.default;
            if (setting.type === 'number' && (currentValue === '' || currentValue === undefined)) {
                currentValue = setting.default ?? '';
            }

            switch (setting.type) {
                case 'select':
                    input = document.createElement('select');
                    if (Array.isArray(setting.options)) {
                        const labels = setting.option_labels && typeof setting.option_labels === 'object'
                            ? setting.option_labels
                            : {};
                        setting.options.forEach(opt => {
                            const option = document.createElement('option');
                            option.value = opt;
                            option.textContent = labels[opt] != null ? String(labels[opt]) : String(opt);
                            option.selected = opt === currentValue;
                            input.appendChild(option);
                        });
                    }
                    break;

                case 'boolean':
                    input = document.createElement('input');
                    input.type = 'checkbox';
                    input.checked = Boolean(currentValue);
                    break;

                case 'range': {
                    input = document.createElement('input');
                    input.type = 'range';
                    input.min = setting.min || 0;
                    input.max = setting.max || 100;
                    input.step = setting.step || 1;
                    const rv =
                        currentValue != null && currentValue !== ''
                            ? currentValue
                            : (setting.default ?? setting.min ?? 0);
                    input.value = rv;

                    const valueLabel = document.createElement('span');
                    valueLabel.className = 'range-value';
                    valueLabel.textContent = rv;
                    input.addEventListener('input', () => {
                        valueLabel.textContent = input.value;
                    });
                    wrapper.appendChild(valueLabel);
                    break;
                }

                default:
                    input = document.createElement('input');
                    input.type = 'text';
                    input.value =
                        currentValue != null && currentValue !== '' ? String(currentValue) : '';
            }

            input.id = `setting-${key}`;
            input.dataset.settingKey = key;
            if (setting.type === 'number') {
                input.type = 'number';
            }
            wrapper.appendChild(input);
            container.appendChild(wrapper);
        }
    }

    _closeMpvAdvancedModal() {
        const bd = this.elements.mpvAdvancedBackdrop;
        if (bd) bd.hidden = true;
        document.removeEventListener('keydown', this._mpvAdvancedEscHandler);
    }

    async _openMpvAdvancedModal() {
        await this.loadCurrentSettings().catch(() => {});
        this.applyGlobalSettingsToControls();
        this.renderSettingsForm();
        const bd = this.elements.mpvAdvancedBackdrop;
        if (bd) bd.hidden = false;
        this._mpvAdvancedEscHandler = (ev) => {
            if (ev.key === 'Escape') this._closeMpvAdvancedModal();
        };
        document.addEventListener('keydown', this._mpvAdvancedEscHandler);
    }

    // Event handlers
    setupEventListeners() {
        this.elements.mpvSettingsForm?.addEventListener('submit', (e) => e.preventDefault());

        document.addEventListener('dsign:language-changed', () => {
            applyI18n();
            try {
                this.refreshDashboardAfterLangChange();
            } catch (e) {
                console.warn(e);
            }
        });

        this.elements.btnMpvAdvanced?.addEventListener('click', () => {
            this._openMpvAdvancedModal().catch((err) => console.error(err));
        });
        this.elements.mpvAdvancedCancel?.addEventListener('click', () => this._closeMpvAdvancedModal());
        this.elements.mpvAdvancedClose?.addEventListener('click', () => this._closeMpvAdvancedModal());
        this.elements.mpvAdvancedSave?.addEventListener('click', () => this.handleMpvAdvancedSave());

        this.elements.mpvAdvancedBackdrop?.addEventListener('click', (e) => {
            if (e.target === this.elements.mpvAdvancedBackdrop) this._closeMpvAdvancedModal();
        });

        this.elements.applyDisplayModeBtn?.addEventListener('click', () => this.handleApplyDisplayMode());

        document.addEventListener('click', (e) => {
            const segBtn = e.target?.closest?.('.segmented-btn');
            if (segBtn) {
                const seg = segBtn.closest('.segmented');
                if (seg?.dataset?.seg) {
                    this._handleSegmentedGlobal(seg, segBtn);
                    return;
                }
                const plId = seg?.dataset?.playlistId;
                if (seg?.dataset?.ov && plId) {
                    this._setSegmentedValue(seg, segBtn.dataset.value);
                    this._schedulePlaylistOverrideSave(Number(plId));
                    return;
                }
            }

            if (e.target.classList.contains('btn-edit')) {
                this.handleEditProfile(e);
            }

            if (e.target.classList.contains('btn-delete')) {
                this.handleDeleteProfile(e);
            }
        });
    }

    _setSegmentedValue(segEl, value) {
        segEl.querySelectorAll('.segmented-btn').forEach((b) => {
            b.classList.toggle('is-active', String(b.dataset.value) === String(value));
        });
    }

    _handleSegmentedGlobal(segEl, btn) {
        const key = segEl.dataset.seg;
        const value = btn.dataset.value;
        this._setSegmentedValue(segEl, value);

        // write into hidden inputs to keep existing handlers working
        if (key === 'preview_auto_interval_sec' && this.elements.previewAutoSelect) this.elements.previewAutoSelect.value = String(value);
        if (key === 'auto_transcode_videos' && this.elements.transcodeEnabled) this.elements.transcodeEnabled.value = String(value);
        if (key === 'transcode_target_resolution' && this.elements.transcodeResolution) this.elements.transcodeResolution.value = String(value);
        if (key === 'transcode_target_fps' && this.elements.transcodeFps) this.elements.transcodeFps.value = String(value);
        if (key === 'hdmi_mode_preset' && this.elements.displayModeSelect) this.elements.displayModeSelect.value = String(value);
        if (key === 'idle_logo_rotate' && this.elements.idleLogoRotate) this.elements.idleLogoRotate.value = String(value);

        if (key === 'preview_auto_interval_sec') this._debounceAutosavePreview();
        if (key === 'auto_transcode_videos' || key === 'transcode_target_resolution' || key === 'transcode_target_fps') {
            this._debounceAutosaveTranscode();
        }
        if (key === 'idle_logo_rotate') this._debounceAutosaveIdle();
    }

    _debounceAutosavePreview() {
        clearTimeout(this._autosavePreviewT);
        this._autosavePreviewT = setTimeout(() => this._savePreviewAutoSilent(), 650);
    }

    _debounceAutosaveTranscode() {
        clearTimeout(this._autosaveTranscodeT);
        this._autosaveTranscodeT = setTimeout(() => this._saveTranscodeSilent(), 650);
    }

    _debounceAutosaveIdle() {
        clearTimeout(this._autosaveIdleT);
        this._autosaveIdleT = setTimeout(() => this._saveIdleRotationSilent(), 650);
    }

    async _savePreviewAutoSilent() {
        const intervalSec = Number(this.elements.previewAutoSelect?.value || 0);
        try {
            const response = await fetch('/api/settings/preview/auto', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                credentials: 'include',
                body: JSON.stringify({ interval_sec: intervalSec }),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) throw new Error(result.error || 'Failed');
            await this.loadCurrentSettings();
            this.applyGlobalSettingsToControls();
        } catch (e) {
            console.error(e);
            showAlert(e.message || 'Preview auto-save failed', 'error');
        }
    }

    async _saveTranscodeSilent() {
        const enabledRaw = this.elements.transcodeEnabled?.value;
        const enabled = enabledRaw === 'true' || enabledRaw === true;
        const resolution = String(this.elements.transcodeResolution?.value || '1920x1080');
        const fps = Number(this.elements.transcodeFps?.value || 25);
        try {
            const response = await fetch('/api/settings/transcode/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                credentials: 'include',
                body: JSON.stringify({ enabled, resolution, fps }),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) throw new Error(result.error || 'Failed');
            await this.loadCurrentSettings();
            this.applyGlobalSettingsToControls();
        } catch (e) {
            console.error(e);
            showAlert(e.message || 'Transcode auto-save failed', 'error');
        }
    }

    async _saveIdleRotationSilent() {
        const val = Number(this.elements.idleLogoRotate?.value || 0);
        try {
            const resp = await fetch('/api/media/idle_logo_rotation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                credentials: 'include',
                body: JSON.stringify({ rotate: val }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error(data.error || 'Failed');
            await this.loadCurrentSettings();
            this.applyGlobalSettingsToControls();
        } catch (e) {
            console.error(e);
            showAlert(e.message || 'Idle rotation save failed', 'error');
        }
    }

    async handleApplyIdleLogoRotate() {
        const val = Number(this.elements.idleLogoRotate?.value || 0);
        try {
            const resp = await fetch('/api/media/idle_logo_rotation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                credentials: 'include',
                body: JSON.stringify({ rotate: val })
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
            showAlert('Idle rotation applied', 'success');
        } catch (err) {
            console.error('Idle rotation failed:', err);
            showAlert(err.message || 'Failed to apply idle rotation', 'error');
        }
    }

    async handleApplyIdleProfile() {
        const profileId = this.elements.idleProfileSelect?.value;
        if (!profileId) return;

        try {
            const response = await fetch(`/api/profiles/apply/${profileId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                }
            });
            
            const result = await response.json();
            if (result.success) {
                showAlert('Idle profile applied successfully', 'success');
                await this.loadCurrentSettings();
                this.applyGlobalSettingsToControls();
            } else {
                throw new Error(result.error || 'Failed to apply profile');
            }
        } catch (error) {
            console.error('Error applying profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async handleAssignProfile() {
        const playlistId = this.elements.playlistSelect?.value;
        const profileId = this.elements.profileSelect?.value || null;

        if (!playlistId) {
            return showAlert('Please select a playlist', 'warning');
        }

        try {
            const response = await fetch('/api/profiles/assign', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ playlist_id: playlistId, profile_id: profileId })
            });
            
            const result = await response.json();
            if (result.success) {
                showAlert('Profile assigned successfully', 'success');
                await this.loadAssignments();
                this.renderPlaylistAssignments();
            } else {
                throw new Error(result.error || 'Failed to assign profile');
            }
        } catch (error) {
            console.error('Error assigning profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async handleSaveAssignment(e) {
        const playlistId = e.target.dataset.playlistId;
        const select = document.querySelector(`.profile-select[data-playlist-id="${playlistId}"]`);
        const profileId = select?.value;

        try {
            const response = await fetch('/api/profiles/assign', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ playlist_id: playlistId, profile_id: profileId })
            });
            
            const result = await response.json();
            if (result.success) {
                showAlert('Assignment saved successfully', 'success');
                await this.loadAssignments();
            } else {
                throw new Error(result.error || 'Failed to save assignment');
            }
        } catch (error) {
            console.error('Error saving assignment:', error);
            showAlert(error.message, 'error');
        }
    }

    async handleSaveProfile() {
        const name = this.elements.profileNameInput?.value.trim();
        const type = this.elements.profileTypeSelect?.value;

        if (!name) {
            return showAlert('Please enter profile name', 'warning');
        }

        try {
            const settings = this.collectSettingsFromForm();
            const response = await fetch('/api/profiles', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ name, type, settings })
            });
            
            const result = await response.json();
            if (result.success) {
                showAlert('Profile saved successfully', 'success');
                this.elements.profileNameInput.value = '';
                await this.loadProfiles();
                this.renderProfileSelects();
                this.renderProfileGrid();
            } else {
                throw new Error(result.error || 'Failed to save profile');
            }
        } catch (error) {
            console.error('Error saving profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async handleEditProfile(e) {
        const profileId = e.target.dataset.id;
        const profile = this.state.profiles.find(p => p && p.id == profileId);
        
        if (profile) {
            this.elements.profileNameInput.value = profile.name;
            this.elements.profileTypeSelect.value = profile.profile_type;
            showAlert(`Editing profile: ${profile.name}`, 'info');
        }
    }

    async handleDeleteProfile(e) {
        const profileId = e.target.dataset.id;
        if (!confirm('Are you sure you want to delete this profile?')) return;

        try {
            const response = await fetch(`/api/profiles/${profileId}`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': getCSRFToken()
                }
            });
            
            const result = await response.json();
            if (result.success) {
                showAlert('Profile deleted successfully', 'success');
                await this.loadProfiles();
                this.renderProfileSelects();
                this.renderProfileGrid();
            } else {
                throw new Error(result.error || 'Failed to delete profile');
            }
        } catch (error) {
            console.error('Error deleting profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async handleMpvAdvancedSave() {
        try {
            const settings = this.collectSettingsFromForm();
            const response = await fetch('/api/settings/mpv/global', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                },
                credentials: 'include',
                body: JSON.stringify(settings),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }
            showAlert('Global MPV options saved', 'success');
            await this.loadCurrentSettings();
            this.applyGlobalSettingsToControls();
            this.renderSettingsForm();
            this._closeMpvAdvancedModal();
        } catch (error) {
            console.error('Error saving global MPV settings:', error);
            showAlert(error.message || 'Failed to save', 'error');
        }
    }

    collectSettingsFromForm() {
        const settings = {};
        const inputs = this.elements.mpvSettingsForm?.querySelectorAll('[data-setting-key]') || [];

        inputs.forEach(input => {
            const key = input.dataset.settingKey;
            if (key) {
                settings[key] = input.type === 'checkbox' ? input.checked : input.value;
            }
        });

        return settings;
    }

    async handleApplyDisplayMode() {
        const preset = this.elements.displayModeSelect?.value || 'auto';
        if (!confirm(`Apply display mode "${preset}" and reboot now?`)) return;

        try {
            this.elements.applyDisplayModeBtn.disabled = true;
            this.elements.applyDisplayModeBtn.textContent = 'Applying…';

            const response = await fetch('/api/settings/display/apply', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ preset, reboot: true })
            });

            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }

            showAlert('Applied. Rebooting…', 'success');
        } catch (error) {
            console.error('Error applying display mode:', error);
            showAlert(error.message || 'Failed to apply display mode', 'error');
        } finally {
            if (this.elements.applyDisplayModeBtn) {
                this.elements.applyDisplayModeBtn.disabled = false;
                this.elements.applyDisplayModeBtn.textContent = 'Apply & reboot';
            }
        }
    }

    async handleApplyPreviewAuto() {
        const intervalSec = Number(this.elements.previewAutoSelect?.value || 0);
        const label = `${intervalSec}s`;
        if (!confirm(`Apply auto preview: ${label}?`)) return;

        try {
            this.elements.applyPreviewAutoBtn.disabled = true;
            this.elements.applyPreviewAutoBtn.textContent = 'Applying…';

            const response = await fetch('/api/settings/preview/auto', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ interval_sec: intervalSec })
            });

            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) {
                throw new Error(result.error || `HTTP ${response.status}`);
            }

            showAlert('Auto preview updated', 'success');
            await this.loadCurrentSettings();
            this.applyGlobalSettingsToControls();
        } catch (error) {
            console.error('Error applying preview auto:', error);
            showAlert(error.message || 'Failed to update auto preview', 'error');
        } finally {
            if (this.elements.applyPreviewAutoBtn) {
                this.elements.applyPreviewAutoBtn.disabled = false;
                this.elements.applyPreviewAutoBtn.textContent = 'Apply';
            }
        }
    }

    async handleApplyTranscode() {
        try {
            if (!this.elements.applyTranscodeBtn) return;
            this.elements.applyTranscodeBtn.disabled = true;
            this.elements.applyTranscodeBtn.textContent = 'Applying…';

            const enabledRaw = this.elements.transcodeEnabled?.value;
            const enabled = enabledRaw === 'true' || enabledRaw === true;
            const resolution = String(this.elements.transcodeResolution?.value || '1920x1080');
            const fps = Number(this.elements.transcodeFps?.value || 25);

            const response = await fetch('/api/settings/transcode/apply', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ enabled, resolution, fps })
            });

            const result = await response.json().catch(() => ({}));
            if (!response.ok || !result.success) {
                throw new Error(result.error || `Failed (${response.status})`);
            }

            showAlert('Transcode settings applied', 'success');
            await this.loadCurrentSettings();
            this.applyGlobalSettingsToControls();
        } catch (error) {
            console.error('Transcode apply failed:', error);
            showAlert(`Failed to apply transcode settings: ${error.message}`, 'error');
        } finally {
            if (this.elements.applyTranscodeBtn) {
                this.elements.applyTranscodeBtn.disabled = false;
                this.elements.applyTranscodeBtn.textContent = 'Apply';
            }
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.App = window.App || {};
    if (window.App.SettingsManagerInstance) return;
    const manager = new SettingsManager();
    window.App.SettingsManagerInstance = manager;
    window.addEventListener('beforeunload', () => manager.cleanup(), { once: true });
}, { once: true });
