document.addEventListener('DOMContentLoaded', () => {
    // Application configuration
    const CONFIG = {
        api: {
            baseUrl: '',
            endpoints: {
                settings: '/api/settings/current',
                playlists: '/api/playlists',
                playback: '/api/playback',
                uploadLogo: '/api/media/upload_logo', // Updated endpoint
                media: '/api/media/files',
                mediaUpload: '/api/media/upload' // Added media upload endpoint
            },
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.cookie.match(/csrf_token=([^;]+)/)?.[1] || ''
            }
        },
        selectors: {
            playlistTable: '#playlist-table tbody',
            createPlaylistBtn: '#create-playlist-btn',
            modal: '#create-playlist-modal',
            modalClose: '.modal .close',
            playlistForm: '#create-playlist-form',
            statusIndicator: '#playlist-status',
            uploadLogoBtn: '#upload-logo-btn',
            logoForm: '#logo-upload-form',
            settingsPanel: '.info-panel .info-card',
            logoImage: '#idle-logo',
            currentSettings: '#current-settings',
            loadingIndicator: '#loading-indicator' // Added loading indicator
        },
        defaultLogo: '/static/default-logo.jpg',
        refreshInterval: 5000 // Auto-refresh interval in ms
    };

    // Initialize DOM elements
    const elements = Object.fromEntries(
        Object.entries(CONFIG.selectors).map(([key, selector]) => 
            [key, document.querySelector(selector)]
        )
    );

    // Application state
    const state = {
        playlists: [],
        currentSettings: {},
        playbackStatus: null,
        refreshIntervalId: null
    };

    // API functions
    const api = {
        async request(url, options = {}) {
            try {
                // Show loading indicator
                if (elements.loadingIndicator) {
                    elements.loadingIndicator.style.display = 'block';
                }

                const response = await fetch(`${CONFIG.api.baseUrl}${url}`, {
                    ...options,
                    headers: {
                        ...CONFIG.api.headers,
                        ...options.headers
                    }
                });

                if (!response.ok) {
                    const error = new Error(`HTTP error! status: ${response.status}`);
                    error.status = response.status;
                    throw error;
                }

                return await response.json();
            } catch (error) {
                console.error(`API request failed: ${url}`, error);
                ui.showAlert(`Error: ${error.message}`, 'error');
                throw error;
            } finally {
                // Hide loading indicator
                if (elements.loadingIndicator) {
                    elements.loadingIndicator.style.display = 'none';
                }
            }
        },

        async getSettings() {
            return this.request(CONFIG.api.endpoints.settings);
        },

        async getPlaylists() {
            return this.request(CONFIG.api.endpoints.playlists);
        },

        async createPlaylist(data) {
            return this.request(CONFIG.api.endpoints.playlists, {
                method: 'POST',
                body: JSON.stringify(data)
            });
        },

        async deletePlaylist(id) {
            return this.request(`${CONFIG.api.endpoints.playlists}/${id}`, {
                method: 'DELETE'
            });
        },

        async startPlayback(playlistId) {
            return this.request(`${CONFIG.api.endpoints.playback}/start/${playlistId}`, {
                method: 'POST'
            });
        },

        async stopPlayback() {
            return this.request(`${CONFIG.api.endpoints.playback}/stop`, {
                method: 'POST'
            });
        },

        async uploadLogo(formData) {
            try {
                const response = await fetch(`${CONFIG.api.baseUrl}${CONFIG.api.endpoints.uploadLogo}`, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': CONFIG.api.headers['X-CSRFToken']
                    },
                    body: formData
                });

                if (!response.ok) {
                    throw new Error('Logo upload failed');
                }

                return await response.json();
            } catch (error) {
                console.error('Logo upload error:', error);
                throw error;
            }
        },

        async uploadMediaFiles(formData) {
            try {
                const response = await fetch(`${CONFIG.api.baseUrl}${CONFIG.api.endpoints.mediaUpload}`, {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    throw new Error('Media upload failed');
                }

                return await response.json();
            } catch (error) {
                console.error('Media upload error:', error);
                throw error;
            }
        }
    };

    // UI functions
    const ui = {
        showAlert(message, type = 'info', duration = 3000) {
            // Remove existing alerts of the same type
            document.querySelectorAll(`.alert-${type}`).forEach(alert => alert.remove());

            const alert = document.createElement('div');
            alert.className = `alert alert-${type}`;
            alert.textContent = message;
            alert.style.cssText = `
                position: fixed; 
                top: 20px; 
                right: 20px;
                padding: 12px 24px; 
                border-radius: 4px;
                background: ${this.getAlertColor(type)};
                color: white; 
                z-index: 1000;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                animation: fadeIn 0.3s ease-out;
            `;
            document.body.appendChild(alert);

            setTimeout(() => {
                alert.style.opacity = '0';
                setTimeout(() => alert.remove(), 300);
            }, duration);
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

        renderSettings(settings) {
            if (!elements.settingsPanel) return;

            const html = `
                <div class="settings-section">
                    <h3>Current Settings</h3>
                    <p><strong>Resolution:</strong> ${settings.resolution || 'N/A'}</p>
                    <p><strong>Aspect Ratio:</strong> ${settings.aspect_ratio || 'N/A'}</p>
                    <p><strong>Rotation:</strong> ${settings.rotation || 0}°</p>
                    <p><strong>Overscan:</strong> ${settings.overscan ? 'Enabled' : 'Disabled'}</p>
                    <p><strong>Volume:</strong> ${settings.volume || 100}%</p>
                    <p><strong>Mute:</strong> ${settings.mute ? 'On' : 'Off'}</p>
                    ${settings.display?.logo ? `<p><strong>Current Logo:</strong> ${settings.display.logo}</p>` : ''}
                </div>
            `;
            elements.settingsPanel.innerHTML = html;
        },

        renderPlaylists(playlists) {
            if (!elements.playlistTable) return;

            elements.playlistTable.innerHTML = playlists.map(playlist => `
                <tr data-id="${playlist.id}">
                    <td>${this.escapeHtml(playlist.name || 'Unnamed')}</td>
                    <td>${this.escapeHtml(playlist.customer || 'No customer')}</td>
                    <td class="actions">
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
                        <span class="status-badge"></span>
                    </td>
                </tr>
            `).join('');
        },

        escapeHtml(unsafe) {
            if (!unsafe) return '';
            return unsafe.toString()
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        },

        togglePlaybackButtons(playlistId, isPlaying) {
            const rows = document.querySelectorAll(`tr[data-id="${playlistId}"]`);
            if (!rows.length) return;

            rows.forEach(row => {
                const playBtn = row.querySelector('.play');
                const stopBtn = row.querySelector('.stop');
                const statusBadge = row.querySelector('.status-badge');

                if (isPlaying) {
                    playBtn.disabled = true;
                    stopBtn.disabled = false;
                    statusBadge.textContent = 'Playing';
                    statusBadge.className = 'status-badge active';
                } else {
                    playBtn.disabled = false;
                    stopBtn.disabled = true;
                    statusBadge.textContent = '';
                    statusBadge.className = 'status-badge';
                }
            });
        },

        updateLogo(logoPath) {
            if (elements.logoImage) {
                elements.logoImage.src = logoPath ? 
                    `${CONFIG.api.baseUrl}/media/${logoPath}?t=${Date.now()}` : 
                    CONFIG.defaultLogo;
            }
        },

        toggleModal(show = true) {
            if (elements.modal) {
                elements.modal.style.display = show ? 'block' : 'none';
            }
        }
    };

    // Event handlers
    const handlers = {
        async init() {
            try {
                // Load initial data
                const [settings, playlists] = await Promise.all([
                    api.getSettings(),
                    api.getPlaylists()
                ]);

                state.currentSettings = settings;
                state.playlists = playlists;

                // Render UI
                ui.renderSettings(settings);
                ui.renderPlaylists(playlists);
                ui.updateLogo(settings.display?.logo);

                // Setup event listeners
                this.setupEventListeners();
                this.startAutoRefresh();

            } catch (error) {
                console.error('Initialization failed:', error);
                ui.showAlert('Failed to initialize application', 'error');
            }
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
                    const playlist = await api.createPlaylist({
                        name: formData.get('name'),
                        customer: formData.get('customer')
                    });

                    state.playlists.push(playlist);
                    ui.renderPlaylists(state.playlists);
                    ui.toggleModal(false);
                    elements.playlistForm.reset();
                    ui.showAlert('Playlist created successfully', 'success');
                } catch (error) {
                    ui.showAlert('Failed to create playlist: ' + error.message, 'error');
                }
            });

            // Logo upload
            elements.uploadLogoBtn?.addEventListener('click', async () => {
                const formData = new FormData(elements.logoForm);
                
                try {
                    const result = await api.uploadLogo(formData);
                    ui.updateLogo(result.filename);
                    ui.showAlert('Logo updated successfully', 'success');
                    
                    // Refresh settings to get updated logo info
                    const settings = await api.getSettings();
                    state.currentSettings = settings;
                    ui.renderSettings(settings);
                } catch (error) {
                    ui.showAlert('Failed to upload logo: ' + error.message, 'error');
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
                        ui.togglePlaybackButtons(playlistId, true);
                        ui.showAlert('Playback started', 'success');
                        
                    } else if (btn.classList.contains('stop')) {
                        await api.stopPlayback();
                        ui.togglePlaybackButtons(playlistId, false);
                        ui.showAlert('Playback stopped', 'info');
                        
                    } else if (btn.classList.contains('edit')) {
                        window.location.href = `/playlists/${playlistId}`;
                        
                    } else if (btn.classList.contains('delete')) {
                        if (confirm('Are you sure you want to delete this playlist?')) {
                            await api.deletePlaylist(playlistId);
                            state.playlists = state.playlists.filter(p => p.id !== playlistId);
                            ui.renderPlaylists(state.playlists);
                            ui.showAlert('Playlist deleted', 'info');
                        }
                    }
                } catch (error) {
                    console.error('Action failed:', error);
                    ui.showAlert(error.status === 403 ? 
                        'Permission denied' : 'Action failed: ' + error.message, 'error');
                }
            });
        },

        startAutoRefresh() {
            // Clear existing interval if any
            if (state.refreshIntervalId) {
                clearInterval(state.refreshIntervalId);
            }

            // Update settings periodically
            state.refreshIntervalId = setInterval(async () => {
                try {
                    const settings = await api.getSettings();
                    if (JSON.stringify(state.currentSettings) !== JSON.stringify(settings)) {
                        state.currentSettings = settings;
                        ui.renderSettings(settings);
                        ui.updateLogo(settings.display?.logo);
                    }
                } catch (error) {
                    console.error('Auto-refresh failed:', error);
                }
            }, CONFIG.refreshInterval);
        },

        cleanup() {
            if (state.refreshIntervalId) {
                clearInterval(state.refreshIntervalId);
            }
        }
    };

    // Initialize the application
    handlers.init();

    // Cleanup when page unloads
    window.addEventListener('beforeunload', () => {
        handlers.cleanup();
    });
});