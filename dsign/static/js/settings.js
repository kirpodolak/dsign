document.addEventListener('DOMContentLoaded', () => {
    const elements = {
        idleProfileSelect: document.getElementById('idle-profile-select'),
        applyIdleBtn: document.getElementById('apply-idle-profile'),
        playlistSelect: document.getElementById('playlist-select'),
        profileSelect: document.getElementById('playlist-profile-select'),
        assignBtn: document.getElementById('assign-profile'),
        profileNameInput: document.getElementById('profile-name'),
        profileTypeSelect: document.getElementById('profile-type'),
        saveProfileBtn: document.getElementById('save-profile'),
        settingsEditor: document.getElementById('profile-settings-editor'),
        currentSettingsPanel: document.getElementById('current-settings-panel'),
        mpvSettingsForm: document.getElementById('mpv-settings-form')
    };

    const state = {
        currentProfile: null,
        currentSettings: {},
        profiles: [],
        playlists: [],
        settingsSchema: {}
    };

    async function init() {
        try {
            await loadData();
            setupEventListeners();
            startAutoRefresh();
        } catch (error) {
            console.error('Initialization error:', error);
            showAlert('Failed to initialize application', 'error');
        }
    }

    async function loadData() {
        try {
            const [profilesRes, playlistsRes, schemaRes, settingsRes] = await Promise.all([
                fetch('/api/profiles'),
                fetch('/api/playlists'),
                fetch('/api/settings/schema'),
                fetch('/api/settings/current')
            ]);

            if (!profilesRes.ok) throw new Error('Failed to load profiles');
            if (!playlistsRes.ok) throw new Error('Failed to load playlists');
            if (!schemaRes.ok) throw new Error('Failed to load settings schema');
            if (!settingsRes.ok) throw new Error('Failed to load current settings');

            const profilesData = await profilesRes.json();
            const playlistsData = await playlistsRes.json();
            const schemaData = await schemaRes.json();
            const settingsData = await settingsRes.json();

            state.profiles = profilesData.success ? profilesData.profiles : [];
            state.playlists = playlistsData.success ? playlistsData.playlists : [];
            state.settingsSchema = schemaData.success ? schemaData.schema : {};
            state.currentSettings = settingsData.success ? settingsData.settings : {};

            updateUI();
        } catch (error) {
            console.error('Error loading data:', error);
            showAlert(error.message, 'error');
            throw error;
        }
    }

    function updateUI() {
        populateSelects();
        renderSettingsForm();
        updateCurrentSettingsDisplay();
    }

    function populateSelects() {
        elements.idleProfileSelect.innerHTML = '<option value="">Default</option>';
        elements.profileSelect.innerHTML = '<option value="">Default</option>';
        elements.playlistSelect.innerHTML = '';

        state.profiles.forEach(profile => {
            const option = document.createElement('option');
            option.value = profile.id;
            option.textContent = profile.name;

            if (profile.profile_type === 'idle') {
                elements.idleProfileSelect.appendChild(option.cloneNode(true));
            } else if (profile.profile_type === 'playlist') {
                elements.profileSelect.appendChild(option.cloneNode(true));
            }
        });

        state.playlists.forEach(playlist => {
            const option = document.createElement('option');
            option.value = playlist.id;
            option.textContent = playlist.name;
            elements.playlistSelect.appendChild(option);
        });
    }

    function renderSettingsForm() {
        elements.settingsEditor.innerHTML = '';

        if (!state.settingsSchema || Object.keys(state.settingsSchema).length === 0) {
            elements.settingsEditor.innerHTML = '<p class="text-muted">No settings schema available</p>';
            return;
        }

        for (const [key, setting] of Object.entries(state.settingsSchema)) {
            const wrapper = document.createElement('div');
            wrapper.className = 'form-group';

            const label = document.createElement('label');
            label.textContent = setting.label;
            label.htmlFor = `setting-${key}`;
            wrapper.appendChild(label);

            let input;
            const currentValue = state.currentSettings[key] ?? setting.default;

            switch (setting.type) {
                case 'select':
                    input = document.createElement('select');
                    setting.options.forEach(opt => {
                        const option = document.createElement('option');
                        option.value = opt;
                        option.textContent = opt;
                        option.selected = opt === currentValue;
                        input.appendChild(option);
                    });
                    break;

                case 'boolean':
                    input = document.createElement('input');
                    input.type = 'checkbox';
                    input.checked = currentValue;
                    break;

                case 'range':
                    input = document.createElement('input');
                    input.type = 'range';
                    input.min = setting.min;
                    input.max = setting.max;
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
                    input.value = currentValue;
            }

            input.id = `setting-${key}`;
            input.dataset.settingKey = key;
            wrapper.appendChild(input);
            elements.settingsEditor.appendChild(wrapper);
        }
    }

    function updateCurrentSettingsDisplay() {
        if (!elements.currentSettingsPanel) return;

        const settings = state.currentSettings || {};
        const isIdle = state.currentProfile?.profile_type === 'idle';

        elements.currentSettingsPanel.innerHTML = `
            <h3>Current ${isIdle ? 'Idle' : 'Playlist'} Settings</h3>
            <div class="settings-grid">
                <div><strong>Resolution:</strong> ${settings.resolution || 'N/A'}</div>
                <div><strong>Aspect Ratio:</strong> ${settings.aspect_ratio || 'N/A'}</div>
                <div><strong>Rotation:</strong> ${settings.rotation || '0'}°</div>
                <div><strong>Overscan:</strong> ${settings.overscan ? 'On' : 'Off'}</div>
                <div><strong>Volume:</strong> ${settings.volume || '100'}%</div>
                <div><strong>Mute:</strong> ${settings.mute ? 'On' : 'Off'}</div>
            </div>
        `;
    }

    function collectSettingsFromForm() {
        const settings = {};
        const inputs = elements.settingsEditor.querySelectorAll('[data-setting-key]');

        inputs.forEach(input => {
            const key = input.dataset.settingKey;
            settings[key] = input.type === 'checkbox' ? input.checked : input.value;
        });

        return settings;
    }

    function setupEventListeners() {
        elements.applyIdleBtn?.addEventListener('click', async () => {
            const profileId = elements.idleProfileSelect.value;
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
                    showAlert('Idle profile applied', 'success');
                    await loadData();
                } else {
                    showAlert(result.error || 'Failed to apply profile', 'error');
                }
            } catch (error) {
                console.error('Error applying profile:', error);
                showAlert('Error applying profile', 'error');
            }
        });

        elements.assignBtn?.addEventListener('click', async () => {
            const playlistId = elements.playlistSelect.value;
            const profileId = elements.profileSelect.value || null;

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
                } else {
                    showAlert(result.error || 'Failed to assign profile', 'error');
                }
            } catch (error) {
                console.error('Error assigning profile:', error);
                showAlert('Error assigning profile', 'error');
            }
        });

        elements.saveProfileBtn?.addEventListener('click', async () => {
            const name = elements.profileNameInput.value.trim();
            const type = elements.profileTypeSelect.value;

            if (!name) {
                return showAlert('Please enter profile name', 'warning');
            }

            try {
                const settings = collectSettingsFromForm();
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
                    elements.profileNameInput.value = '';
                    await loadData();
                } else {
                    showAlert(result.error || 'Failed to save profile', 'error');
                }
            } catch (error) {
                console.error('Error saving profile:', error);
                showAlert('Error saving profile', 'error');
            }
        });

        elements.mpvSettingsForm?.addEventListener('submit', async (e) => {
            e.preventDefault();
            try {
                const formData = new FormData(elements.mpvSettingsForm);
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
                    await loadData();
                } else {
                    showAlert(result.error || 'Failed to update settings', 'error');
                }
            } catch (error) {
                console.error('Error updating settings:', error);
                showAlert('Error updating settings', 'error');
            }
        });
    }

    function startAutoRefresh() {
        setInterval(async () => {
            try {
                const response = await fetch('/api/settings/current');
                if (response.ok) {
                    const newSettings = await response.json();
                    if (JSON.stringify(state.currentSettings) !== JSON.stringify(newSettings.settings)) {
                        state.currentSettings = newSettings.settings;
                        updateCurrentSettingsDisplay();
                    }
                }
            } catch (error) {
                console.error('Auto-refresh error:', error);
            }
        }, 10000);
    }

    function showAlert(message, type = 'info') {
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

    function getCSRFToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content || '';
    }

    init();
});