document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
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
        mpvSettingsForm: document.getElementById('mpv-settings-form'),
        profilesGrid: document.getElementById('profiles-grid'),
        playlistAssignments: document.getElementById('playlist-assignments'),
        currentSettingsDisplay: document.getElementById('current-settings-display'),
        currentProfileIndicator: document.getElementById('current-profile-indicator')
    };

    // Application State
    const state = {
        currentProfile: null,
        currentSettings: {},
        profiles: [],
        playlists: [],
        assignments: {},
        settingsSchema: {}
    };

    // Main Initialization
    async function init() {
        try {
            // Проверка существования элементов перед работой с ними
            if (!elements.idleProfileSelect || !elements.profileSelect) {
                throw new Error('Required DOM elements not found');
            }

            // Загрузка данных с обработкой ошибок
            const [profiles, playlists, assignments] = await Promise.all([
                loadProfiles().catch(e => {
                    console.error('Profile load error:', e);
                    return [];
                }),
                loadPlaylists().catch(e => {
                    console.error('Playlist load error:', e);
                    return [];
                }),
                loadAssignments().catch(e => {
                    console.error('Assignment load error:', e);
                    return {};
                })
            ]);

            // Обновление состояния
            state.profiles = profiles;
            state.playlists = playlists;
            state.assignments = assignments;

            // Рендеринг только если есть данные
            if (profiles.length > 0) {
                renderProfileSelects();
            }
        
            if (playlists.length > 0) {
                renderPlaylistAssignments();
            }

        } catch (error) {
            console.error('Initialization error:', error);
            showAlert('Failed to initialize settings. Please try again.', 'error');
        }
    }

    // Data Loading Functions
    async function loadProfiles() {
        try {
            const response = await fetch('/api/profiles');
            if (!response.ok) throw new Error('Failed to load profiles');
            
            const data = await response.json();
            if (data.success) {
                state.profiles = Array.isArray(data.profiles) ? data.profiles : [];
            } else {
                throw new Error(data.error || 'Invalid profiles data');
            }
        } catch (error) {
            console.error('Error loading profiles:', error);
            showAlert('Failed to load profiles. ' + error.message, 'error');
            state.profiles = [];
        }
    }

    async function loadPlaylists() {
        try {
            const response = await fetch('/api/playlists');
            if (!response.ok) throw new Error('Failed to load playlists');
            
            const data = await response.json();
            if (data.success) {
                state.playlists = Array.isArray(data.playlists) ? data.playlists : [];
            } else {
                throw new Error(data.error || 'Invalid playlists data');
            }
        } catch (error) {
            console.error('Error loading playlists:', error);
            showAlert('Failed to load playlists. ' + error.message, 'error');
            state.playlists = [];
        }
    }

    async function loadAssignments() {
        try {
            const response = await fetch('/api/profiles/assignments');
            if (!response.ok) throw new Error('Failed to load assignments');
            
            const data = await response.json();
            if (data.success) {
                state.assignments = data.assignments || {};
            } else {
                throw new Error(data.error || 'Invalid assignments data');
            }
        } catch (error) {
            console.error('Error loading assignments:', error);
            showAlert('Failed to load profile assignments', 'error');
            state.assignments = {};
        }
    }

    async function loadCurrentSettings() {
        try {
            const response = await fetch('/api/settings/current');
            if (!response.ok) throw new Error('Failed to load current settings');
            
            const data = await response.json();
            if (data.success) {
                state.currentSettings = data.settings || {};
                state.currentProfile = data.profile || null;
            } else {
                throw new Error(data.error || 'Invalid settings data');
            }
        } catch (error) {
            console.error('Error loading current settings:', error);
            showAlert('Failed to load current settings', 'error');
            state.currentSettings = {};
            state.currentProfile = null;
        }
    }

    async function loadSettingsSchema() {
        try {
            const response = await fetch('/api/settings/schema');
            if (!response.ok) throw new Error('Failed to load settings schema');
            
            const data = await response.json();
            if (data.success) {
                state.settingsSchema = data.schema || {};
            } else {
                throw new Error(data.error || 'Invalid schema data');
            }
        } catch (error) {
            console.error('Error loading settings schema:', error);
            showAlert('Failed to load settings schema', 'error');
            state.settingsSchema = {};
        }
    }

    // UI Rendering Functions
    function renderProfileSelects() {
        // Clear existing options
        elements.idleProfileSelect.innerHTML = '<option value="">Default</option>';
        elements.profileSelect.innerHTML = '<option value="">Default</option>';

        // Add idle profiles
        state.profiles
            .filter(profile => profile.profile_type === 'idle')
            .forEach(profile => {
                const option = document.createElement('option');
                option.value = profile.id;
                option.textContent = profile.name;
                elements.idleProfileSelect.appendChild(option);
            });

        // Add playlist profiles
        state.profiles
            .filter(profile => profile.profile_type === 'playlist')
            .forEach(profile => {
                const option = document.createElement('option');
                option.value = profile.id;
                option.textContent = profile.name;
                elements.profileSelect.appendChild(option);
            });
    }

    function renderProfileGrid() {
        elements.profilesGrid.innerHTML = '';
        
        state.profiles.forEach(profile => {
            const card = document.createElement('div');
            card.className = 'profile-card';
            card.innerHTML = `
                <div class="profile-header">
                    <span class="profile-name">${profile.name}</span>
                    <span class="profile-type">${profile.profile_type}</span>
                </div>
                <div class="profile-actions">
                    <button class="btn-edit" data-id="${profile.id}">✏️</button>
                    <button class="btn-delete" data-id="${profile.id}">🗑️</button>
                </div>
            `;
            elements.profilesGrid.appendChild(card);
        });
    }

    function renderPlaylistAssignments() {
        elements.playlistAssignments.innerHTML = '';
        
        state.playlists.forEach(playlist => {
            const row = document.createElement('div');
            row.className = 'assignment-row';
            row.innerHTML = `
                <span class="playlist-name">${playlist.name}</span>
                <select class="profile-select" data-playlist-id="${playlist.id}">
                    <option value="">Default</option>
                    ${state.profiles
                        .filter(p => p.profile_type === 'playlist')
                        .map(p => `<option value="${p.id}" 
                            ${state.assignments[playlist.id] === p.id ? 'selected' : ''}>
                            ${p.name}
                        </option>`)
                        .join('')}
                </select>
                <button class="btn-save" data-playlist-id="${playlist.id}">Save</button>
            `;
            elements.playlistAssignments.appendChild(row);
        });
    }

    function renderCurrentSettings() {
        if (!elements.currentSettingsDisplay || !elements.currentProfileIndicator) return;

        // Render current profile info
        if (state.currentProfile) {
            elements.currentProfileIndicator.innerHTML = `
                <p>Current Profile: <strong>${state.currentProfile.name}</strong></p>
                <p>Type: <strong>${state.currentProfile.profile_type}</strong></p>
            `;
        } else {
            elements.currentProfileIndicator.innerHTML = '<p>Using default settings</p>';
        }

        // Render current settings
        elements.currentSettingsDisplay.innerHTML = `
            <div><strong>Resolution:</strong> ${state.currentSettings.resolution || 'N/A'}</div>
            <div><strong>Aspect Ratio:</strong> ${state.currentSettings.aspect_ratio || 'N/A'}</div>
            <div><strong>Rotation:</strong> ${state.currentSettings.rotation || '0'}°</div>
            <div><strong>Overscan:</strong> ${state.currentSettings.overscan ? 'On' : 'Off'}</div>
            <div><strong>Volume:</strong> ${state.currentSettings.volume || '100'}%</div>
            <div><strong>Mute:</strong> ${state.currentSettings.mute ? 'On' : 'Off'}</div>
        `;
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

    // Event Handlers
    function setupEventListeners() {
        // Apply idle profile
        elements.applyIdleBtn?.addEventListener('click', handleApplyIdleProfile);

        // Assign profile to playlist
        elements.assignBtn?.addEventListener('click', handleAssignProfile);

        // Save new profile
        elements.saveProfileBtn?.addEventListener('click', handleSaveProfile);

        // MPV settings form submission
        elements.mpvSettingsForm?.addEventListener('submit', handleMpvSettingsSubmit);

        // Delegated event listeners for dynamic content
        document.addEventListener('click', (e) => {
            // Save playlist assignment
            if (e.target.classList.contains('btn-save')) {
                handleSaveAssignment(e);
            }
            
            // Edit profile
            if (e.target.classList.contains('btn-edit')) {
                handleEditProfile(e);
            }
            
            // Delete profile
            if (e.target.classList.contains('btn-delete')) {
                handleDeleteProfile(e);
            }
        });
    }

    async function handleApplyIdleProfile() {
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
                showAlert('Idle profile applied successfully', 'success');
                await loadCurrentSettings();
                renderCurrentSettings();
            } else {
                throw new Error(result.error || 'Failed to apply profile');
            }
        } catch (error) {
            console.error('Error applying profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async function handleAssignProfile() {
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
                await loadAssignments();
                renderPlaylistAssignments();
            } else {
                throw new Error(result.error || 'Failed to assign profile');
            }
        } catch (error) {
            console.error('Error assigning profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async function handleSaveAssignment(e) {
        const playlistId = e.target.dataset.playlistId;
        const select = document.querySelector(`.profile-select[data-playlist-id="${playlistId}"]`);
        const profileId = select.value;

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
                await loadAssignments();
            } else {
                throw new Error(result.error || 'Failed to save assignment');
            }
        } catch (error) {
            console.error('Error saving assignment:', error);
            showAlert(error.message, 'error');
        }
    }

    async function handleSaveProfile() {
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
                await loadProfiles();
                renderProfileSelects();
                renderProfileGrid();
            } else {
                throw new Error(result.error || 'Failed to save profile');
            }
        } catch (error) {
            console.error('Error saving profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async function handleEditProfile(e) {
        const profileId = e.target.dataset.id;
        const profile = state.profiles.find(p => p.id == profileId);
        
        if (profile) {
            elements.profileNameInput.value = profile.name;
            elements.profileTypeSelect.value = profile.profile_type;
            showAlert(`Editing profile: ${profile.name}`, 'info');
        }
    }

    async function handleDeleteProfile(e) {
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
                await loadProfiles();
                renderProfileSelects();
                renderProfileGrid();
            } else {
                throw new Error(result.error || 'Failed to delete profile');
            }
        } catch (error) {
            console.error('Error deleting profile:', error);
            showAlert(error.message, 'error');
        }
    }

    async function handleMpvSettingsSubmit(e) {
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
                await loadCurrentSettings();
                renderCurrentSettings();
            } else {
                throw new Error(result.error || 'Failed to update settings');
            }
        } catch (error) {
            console.error('Error updating settings:', error);
            showAlert(error.message, 'error');
        }
    }

    // Utility Functions
    function collectSettingsFromForm() {
        const settings = {};
        const inputs = elements.settingsEditor.querySelectorAll('[data-setting-key]');

        inputs.forEach(input => {
            const key = input.dataset.settingKey;
            settings[key] = input.type === 'checkbox' ? input.checked : input.value;
        });

        return settings;
    }

    function startAutoRefresh() {
        setInterval(async () => {
            try {
                await loadCurrentSettings();
                renderCurrentSettings();
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

    // Initialize the application
    init();
});
