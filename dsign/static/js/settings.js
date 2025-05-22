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

        this.state = {
            currentProfile: null,
            currentSettings: {},
            profiles: [],
            playlists: [],
            assignments: {},
            settingsSchema: {}
        };

        this.init();
    }

    async init() {
        try {
            if (!this.elements.idleProfileSelect || !this.elements.profileSelect) {
                throw new Error('Required DOM elements not found');
            }

            const [profiles, playlists, assignments] = await Promise.all([
                this.loadProfiles().catch(e => {
                    console.error('Profile load error:', e);
                    return [];
                }),
                this.loadPlaylists().catch(e => {
                    console.error('Playlist load error:', e);
                    return [];
                }),
                this.loadAssignments().catch(e => {
                    console.error('Assignment load error:', e);
                    return {};
                })
            ]);

            this.state.profiles = Array.isArray(profiles) ? profiles : [];
            this.state.playlists = Array.isArray(playlists) ? playlists : [];
            this.state.assignments = assignments && typeof assignments === 'object' ? assignments : {};

            this.renderProfileSelects();
            this.renderPlaylistAssignments();

            await Promise.all([
                this.loadCurrentSettings(),
                this.loadSettingsSchema()
            ]);

            this.renderCurrentSettings();
            this.renderSettingsForm();
            this.renderProfileGrid();
            this.setupEventListeners();

        } catch (error) {
            console.error('Initialization error:', error);
            showAlert('Failed to initialize settings. Please try again.', 'error');
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

    // Rendering methods
    renderProfileSelects() {
        if (!this.elements.idleProfileSelect || !this.elements.profileSelect) return;

        this.elements.idleProfileSelect.innerHTML = '<option value="">Default</option>';
        this.elements.profileSelect.innerHTML = '<option value="">Default</option>';

        this.state.profiles
            .filter(profile => profile && profile.profile_type === 'idle')
            .forEach(profile => {
                const option = document.createElement('option');
                option.value = profile.id;
                option.textContent = profile.name;
                this.elements.idleProfileSelect.appendChild(option);
            });

        this.state.profiles
            .filter(profile => profile && profile.profile_type === 'playlist')
            .forEach(profile => {
                const option = document.createElement('option');
                option.value = profile.id;
                option.textContent = profile.name;
                this.elements.profileSelect.appendChild(option);
            });
    }

    renderProfileGrid() {
        if (!this.elements.profilesGrid) return;
        
        this.elements.profilesGrid.innerHTML = '';
        
        this.state.profiles.forEach(profile => {
            if (!profile) return;
            
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
            this.elements.profilesGrid.appendChild(card);
        });
    }

    renderPlaylistAssignments() {
        if (!this.elements.playlistAssignments) return;
        
        this.elements.playlistAssignments.innerHTML = '';
        
        this.state.playlists.forEach(playlist => {
            if (!playlist) return;
            
            const row = document.createElement('div');
            row.className = 'assignment-row';
            row.innerHTML = `
                <span class="playlist-name">${playlist.name}</span>
                <select class="profile-select" data-playlist-id="${playlist.id}">
                    <option value="">Default</option>
                    ${this.state.profiles
                        .filter(p => p && p.profile_type === 'playlist')
                        .map(p => `<option value="${p.id}" 
                            ${this.state.assignments[playlist.id] === p.id ? 'selected' : ''}>
                            ${p.name}
                        </option>`)
                        .join('')}
                </select>
                <button class="btn-save" data-playlist-id="${playlist.id}">Save</button>
            `;
            this.elements.playlistAssignments.appendChild(row);
        });
    }

    renderCurrentSettings() {
        if (!this.elements.currentSettingsDisplay || !this.elements.currentProfileIndicator) return;

        if (this.state.currentProfile) {
            this.elements.currentProfileIndicator.innerHTML = `
                <p>Current Profile: <strong>${this.state.currentProfile.name}</strong></p>
                <p>Type: <strong>${this.state.currentProfile.profile_type}</strong></p>
            `;
        } else {
            this.elements.currentProfileIndicator.innerHTML = '<p>Using default settings</p>';
        }

        this.elements.currentSettingsDisplay.innerHTML = `
            <div><strong>Resolution:</strong> ${this.state.currentSettings.resolution || 'N/A'}</div>
            <div><strong>Aspect Ratio:</strong> ${this.state.currentSettings.aspect_ratio || 'N/A'}</div>
            <div><strong>Rotation:</strong> ${this.state.currentSettings.rotation || '0'}°</div>
            <div><strong>Overscan:</strong> ${this.state.currentSettings.overscan ? 'On' : 'Off'}</div>
            <div><strong>Volume:</strong> ${this.state.currentSettings.volume || '100'}%</div>
            <div><strong>Mute:</strong> ${this.state.currentSettings.mute ? 'On' : 'Off'}</div>
        `;
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
        this.elements.applyIdleBtn?.addEventListener('click', () => this.handleApplyIdleProfile());
        this.elements.assignBtn?.addEventListener('click', () => this.handleAssignProfile());
        this.elements.saveProfileBtn?.addEventListener('click', () => this.handleSaveProfile());
        this.elements.mpvSettingsForm?.addEventListener('submit', (e) => this.handleMpvSettingsSubmit(e));

        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('btn-save')) {
                this.handleSaveAssignment(e);
            }
            
            if (e.target.classList.contains('btn-edit')) {
                this.handleEditProfile(e);
            }
            
            if (e.target.classList.contains('btn-delete')) {
                this.handleDeleteProfile(e);
            }
        });
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
                this.renderCurrentSettings();
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
                this.renderCurrentSettings();
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
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new SettingsManager();
});
