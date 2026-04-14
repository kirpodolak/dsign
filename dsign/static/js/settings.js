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
            applyPreviewAutoBtn: document.getElementById('apply-preview-auto'),
            transcodeEnabled: document.getElementById('transcode-enabled'),
            transcodeResolution: document.getElementById('transcode-resolution'),
            transcodeFps: document.getElementById('transcode-fps'),
            applyTranscodeBtn: document.getElementById('apply-transcode'),
            idleLogoRotate: document.getElementById('idle-logo-rotate'),
            applyIdleLogoRotateBtn: document.getElementById('apply-idle-logo-rotate'),
        };

        this.state = {
            currentProfile: null,
            currentSettings: {},
            playlists: [],
            playlistOverrides: [],
            settingsSchema: {},
            systemStatus: null,
            systemPollTimer: null,
        };

        this.init();
    }

    async init() {
        try {
            await this.loadPlaylistOverrides().catch(() => []);
            this.renderPlaylistOverrides();

            await Promise.all([
                this.loadCurrentSettings(),
                this.loadSettingsSchema(),
                this.loadIdleLogoRotation().catch(() => 0)
            ]);

            this.renderStatusDashboardSkeleton();
            await this.refreshSystemStatus({ startPolling: true }).catch(() => {});
            this.renderSettingsForm();
            this.setupEventListeners();

        } catch (error) {
            console.error('Initialization error:', error);
            showAlert('Failed to initialize settings. Please try again.', 'error');
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
        this.elements.systemDashboard.innerHTML = `
            <div class="status-tile status-tile--donut">
              <div class="donut" data-k="diskDonut" style="--p:0;"></div>
              <div class="status-tile__meta">
                <p class="status-tile__title">Storage (media)</p>
                <p class="status-tile__value" data-k="mediaFree">—</p>
                <p class="status-tile__sub" data-k="mediaSub"></p>
              </div>
            </div>
            <div class="status-tile status-tile--donut">
              <div class="donut" data-k="tempDonut" style="--p:0;"></div>
              <div class="status-tile__meta">
                <p class="status-tile__title">CPU temp</p>
                <p class="status-tile__value" data-k="cpuTemp">—</p>
                <p class="status-tile__sub" data-k="cpuTempSub"></p>
              </div>
            </div>
            <div class="status-tile status-tile--donut">
              <div class="donut" data-k="cpuDonut" style="--p:0;"></div>
              <div class="status-tile__meta">
                <p class="status-tile__title">CPU usage</p>
                <p class="status-tile__value" data-k="cpuUsage">—</p>
                <p class="status-tile__sub" data-k="cpuUsageSub"></p>
              </div>
            </div>
            <div class="status-tile">
              <p class="status-tile__title">Audio</p>
              <p class="status-tile__value" data-k="audioValue">—</p>
              <div class="status-audio-row">
                <input type="range" min="0" max="100" step="1" value="0" data-k="audioSlider" />
                <label class="checkbox-row">
                  <input type="checkbox" data-k="audioMute" />
                  <span>Mute</span>
                </label>
                <button class="submit-btn" type="button" data-k="audioApply">Apply</button>
              </div>
              <p class="status-tile__sub" data-k="audioSub"></p>
            </div>
        `;
    }

    _qsDashboard(k) {
        return this.elements.systemDashboard?.querySelector(`[data-k="${k}"]`);
    }

    async refreshSystemStatus({ startPolling = false } = {}) {
        const resp = await fetch('/api/system/status', { credentials: 'include' });
        if (!resp.ok) throw new Error('Failed to load system status');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Invalid system status data');
        this.state.systemStatus = data.status || null;
        this.applySystemStatusToDashboard();

        if (startPolling && !this.state.systemPollTimer) {
            this.state.systemPollTimer = setInterval(() => {
                this.refreshSystemStatus().catch(() => {});
            }, 2000);
        }
    }

    applySystemStatusToDashboard() {
        const st = this.state.systemStatus;
        if (!st || !this.elements.systemDashboard) return;

        const media = st.storage?.media || st.storage?.root;
        if (media) {
            const free = this._formatBytes(media.free);
            const total = this._formatBytes(media.total);
            const usedPct = media.used_percent != null ? `${media.used_percent}% used` : '';
            const pFree = this._qsDashboard('mediaFree');
            const pSub = this._qsDashboard('mediaSub');
            if (pFree) pFree.textContent = free;
            if (pSub) pSub.textContent = `${usedPct} • ${total} total`;

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
        if (pUsageSub) pUsageSub.textContent = usage != null ? 'from /proc/stat' : 'estimated (loadavg)';
        const cpuDonut = this._qsDashboard('cpuDonut');
        if (cpuDonut) {
            const val = usage != null ? Number(usage) : (loadFallback != null ? Number(loadFallback) : 0);
            cpuDonut.style.setProperty('--p', String(Math.max(0, Math.min(100, val))));
        }

        const audio = st.audio || {};
        const audioValue = this._qsDashboard('audioValue');
        const audioSub = this._qsDashboard('audioSub');
        const slider = this._qsDashboard('audioSlider');
        const mute = this._qsDashboard('audioMute');
        const available = Boolean(audio.available);
        if (!available) {
            if (audioValue) audioValue.textContent = 'N/A';
            if (audioSub) audioSub.textContent = 'amixer not available';
            if (slider) slider.disabled = true;
            if (mute) mute.disabled = true;
        } else {
            const vol = audio.volume_percent;
            const isMuted = Boolean(audio.muted);
            if (audioValue) audioValue.textContent = (vol != null ? `${vol}%` : '—');
            if (audioSub) audioSub.textContent = isMuted ? 'Muted' : 'On';
            if (slider && vol != null) slider.value = String(vol);
            if (mute) mute.checked = Boolean(isMuted);
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

        (this.state.playlistOverrides || []).forEach((row) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'assignment-row';

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
                <span class="playlist-name">
                  ${row.playlist_name}
                  ${isDefault ? '<span class="badge badge--default">Default</span>' : ''}
                </span>
                <label class="checkbox-row">
                  <input type="checkbox" class="ov-enabled" ${enabled ? 'checked' : ''} data-playlist-id="${row.playlist_id}">
                  <span>Override</span>
                </label>
                <div class="segmented ${disabledClass}" data-ov="out" data-playlist-id="${row.playlist_id}">
                  <button type="button" class="segmented-btn ${outPreset==='auto'?'is-active':''}" data-value="auto">Auto</button>
                  <button type="button" class="segmented-btn ${outPreset==='1080p'?'is-active':''}" data-value="1080p">1080p</button>
                  <button type="button" class="segmented-btn ${outPreset==='720p'?'is-active':''}" data-value="720p">720p</button>
                </div>
                <select class="ov-rotate form-control ${disabledClass}" data-playlist-id="${row.playlist_id}" ${enabled ? '' : 'disabled'}>
                  <option value="0" ${rotate===0?'selected':''}>0°</option>
                  <option value="90" ${rotate===90?'selected':''}>90°</option>
                  <option value="180" ${rotate===180?'selected':''}>180°</option>
                  <option value="270" ${rotate===270?'selected':''}>270°</option>
                </select>
                <select class="ov-fit form-control ${disabledClass}" data-playlist-id="${row.playlist_id}" ${enabled ? '' : 'disabled'}>
                  <option value="0" ${panscan<=0.01?'selected':''}>Fit</option>
                  <option value="1" ${panscan>=0.99?'selected':''}>Fill</option>
                </select>
                <label class="checkbox-row ${disabledClass}">
                  <input type="checkbox" class="ov-mute" ${mute ? 'checked' : ''} data-playlist-id="${row.playlist_id}" ${enabled ? '' : 'disabled'}>
                  <span>Mute</span>
                </label>
                <button class="btn-save submit-btn" data-playlist-id="${row.playlist_id}">Apply</button>
            `;
            el.appendChild(wrapper);
        });
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
        if (!this.elements.settingsEditor) return;

        this.elements.settingsEditor.innerHTML = '';

        if (!this.state.settingsSchema || Object.keys(this.state.settingsSchema).length === 0) {
            this.elements.settingsEditor.innerHTML = '<p class="text-muted">No settings schema available</p>';
            return;
        }

        for (const [key, setting] of Object.entries(this.state.settingsSchema)) {
            if (!setting || typeof setting !== 'object') continue;

            const wrapper = document.createElement('div');
            wrapper.className = 'form-group';

            const label = document.createElement('label');
            label.textContent = setting.label || key;
            label.htmlFor = `setting-${key}`;
            wrapper.appendChild(label);

            let input;
            const currentValue = this.state.currentSettings[key] ?? setting.default;

            switch (setting.type) {
                case 'select':
                    input = document.createElement('select');
                    if (Array.isArray(setting.options)) {
                        setting.options.forEach(opt => {
                            const option = document.createElement('option');
                            option.value = opt;
                            option.textContent = opt;
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

                case 'range':
                    input = document.createElement('input');
                    input.type = 'range';
                    input.min = setting.min || 0;
                    input.max = setting.max || 100;
                    input.step = setting.step || 1;
                    input.value = currentValue;

                    const valueLabel = document.createElement('span');
                    valueLabel.className = 'range-value';
                    valueLabel.textContent = currentValue;
                    input.addEventListener('input', () => {
                        valueLabel.textContent = input.value;
                    });
                    wrapper.appendChild(valueLabel);
                    break;

                default:
                    input = document.createElement('input');
                    input.type = 'text';
                    input.value = currentValue || '';
            }

            input.id = `setting-${key}`;
            input.dataset.settingKey = key;
            wrapper.appendChild(input);
            this.elements.settingsEditor.appendChild(wrapper);
        }
    }

    // Event handlers
    setupEventListeners() {
        this.elements.mpvSettingsForm?.addEventListener('submit', (e) => this.handleMpvSettingsSubmit(e));
        this.elements.applyDisplayModeBtn?.addEventListener('click', () => this.handleApplyDisplayMode());
        this.elements.applyPreviewAutoBtn?.addEventListener('click', () => this.handleApplyPreviewAuto());
        this.elements.applyTranscodeBtn?.addEventListener('click', () => this.handleApplyTranscode());
        this.elements.applyIdleLogoRotateBtn?.addEventListener('click', () => this.handleApplyIdleLogoRotate());

        document.addEventListener('click', (e) => {
            const segBtn = e.target?.closest?.('.segmented-btn');
            if (segBtn) {
                const seg = segBtn.closest('.segmented');
                if (seg?.dataset?.seg) {
                    this._handleSegmentedGlobal(seg, segBtn);
                    return;
                }
                if (seg?.dataset?.ov === 'out') {
                    this._handleSegmentedOverrideOut(seg, segBtn);
                    return;
                }
            }
            if (e.target?.dataset?.k === 'audioApply') {
                this.handleApplyAudioFromDashboard();
            }
            if (e.target.classList.contains('btn-save')) {
                this.handleSaveOverride(e);
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
    }

    _handleSegmentedOverrideOut(segEl, btn) {
        this._setSegmentedValue(segEl, btn.dataset.value);
    }

    async handleSaveOverride(e) {
        const btn = e.target;
        const playlistId = Number(btn?.dataset?.playlistId);
        if (!playlistId) return;
        try {
            btn.disabled = true;
            btn.textContent = 'Saving…';

            const enabled = Boolean(document.querySelector(`.ov-enabled[data-playlist-id="${playlistId}"]`)?.checked);
            const rotate = Number(document.querySelector(`.ov-rotate[data-playlist-id="${playlistId}"]`)?.value || 0);
            const panscan = Number(document.querySelector(`.ov-fit[data-playlist-id="${playlistId}"]`)?.value || 0);
            const mute = Boolean(document.querySelector(`.ov-mute[data-playlist-id="${playlistId}"]`)?.checked);
            const outSeg = document.querySelector(`.segmented[data-ov="out"][data-playlist-id="${playlistId}"]`);
            const outVal = outSeg?.querySelector('.segmented-btn.is-active')?.dataset?.value || 'auto';
            const outMap = {
                auto: { dwidth: null, dheight: null },
                '1080p': { dwidth: 1920, dheight: 1080 },
                '720p': { dwidth: 1280, dheight: 720 },
            };
            const out = outMap[outVal] || outMap.auto;

            const resp = await fetch('/api/playlists/overrides', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                credentials: 'include',
                body: JSON.stringify({
                    playlist_id: playlistId,
                    enabled,
                    video_rotate: rotate,
                    panscan,
                    mute,
                    dwidth: out.dwidth,
                    dheight: out.dheight,
                })
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);

            showAlert('Overrides saved', 'success');
            await this.loadPlaylistOverrides();
            this.renderPlaylistOverrides();
        } catch (err) {
            console.error('Override save failed:', err);
            showAlert(err.message || 'Failed to save overrides', 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Apply';
            }
        }
    }

    async handleApplyAudioFromDashboard() {
        try {
            const slider = this._qsDashboard('audioSlider');
            const mute = this._qsDashboard('audioMute');
            const volume = slider ? Number(slider.value) : null;
            const muted = mute ? Boolean(mute.checked) : null;
            const resp = await fetch('/api/system/audio', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                credentials: 'include',
                body: JSON.stringify({ volume_percent: volume, muted })
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || !data.success) throw new Error(data.error || `HTTP ${resp.status}`);
            this.state.systemStatus = this.state.systemStatus || {};
            this.state.systemStatus.audio = data.audio || data.audio?.audio || data.audio;
            await this.refreshSystemStatus().catch(() => {});
            showAlert('Audio updated', 'success');
        } catch (err) {
            console.error('Audio update failed:', err);
            showAlert(err.message || 'Failed to update audio', 'error');
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

    async handleMpvSettingsSubmit(e) {
        e.preventDefault();
        try {
            const formData = new FormData(this.elements.mpvSettingsForm);
            const settings = Object.fromEntries(formData.entries());
            
            const response = await fetch('/api/settings/update', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify(settings)
            });
            
            const result = await response.json();
            if (result.success) {
                showAlert('Settings updated successfully', 'success');
                await this.loadCurrentSettings();
                this.applyGlobalSettingsToControls();
            } else {
                throw new Error(result.error || 'Failed to update settings');
            }
        } catch (error) {
            console.error('Error updating settings:', error);
            showAlert(error.message, 'error');
        }
    }

    collectSettingsFromForm() {
        const settings = {};
        const inputs = this.elements.settingsEditor?.querySelectorAll('[data-setting-key]') || [];

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
        const label = this.elements.previewAutoSelect?.selectedOptions?.[0]?.textContent || `${intervalSec}s`;
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

            const enabled = Boolean(this.elements.transcodeEnabled?.checked);
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
    new SettingsManager();
});
