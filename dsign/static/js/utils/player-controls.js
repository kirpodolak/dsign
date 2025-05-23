import { showAlert, showError } from './alerts.js';
import { getToken, getCookie, deleteCookie } from './helpers.js';
import { fetchAPI } from './api.js';

export class PlayerControls {
    constructor({ API, Alerts, Helpers }) {
        this.currentUserId = null;
        this.fetchAPI = API?.fetch || null;
        this.showAlert = Alerts?.showAlert || null;
        this.showError = Alerts?.showError || null;
        this.toggleButtonState = Helpers?.toggleButtonState || null;
        this.initEventListeners();
    }

    initEventListeners() {
        document.addEventListener('click', (e) => {
            if (e.target.closest('.play-btn')) this.handlePlay(e.target.closest('.play-btn'));
            if (e.target.closest('.stop-btn')) this.handleStop(e.target.closest('.stop-btn'));
            if (e.target.closest('.edit-btn')) this.handleEdit(e.target.closest('.edit-btn'));
            if (e.target.closest('.delete-btn')) this.handleDelete(e.target.closest('.delete-btn'));
        });
    }

    setCurrentUser(userId) {
        this.currentUserId = userId;
    }

    async handlePlay(button) {
        const playlistId = button?.dataset?.playlistId;
        if (!playlistId) return this.showError('Error', 'No playlist selected');

        try {
            this.toggleButtonState(button, true);
            const response = await this.fetchAPI('playback/play', {
                method: 'POST',
                body: JSON.stringify({ 
                    playlist_id: playlistId,
                    user_id: this.currentUserId 
                })
            });

            if (response?.success) {
                this.showAlert('success', 'Playing', `Started playlist: ${response.playlist_name || playlistId}`);
            } else {
                throw new Error(response?.error || 'Failed to start playback');
            }
        } catch (error) {
            this.showError('Playback Error', error.message);
        } finally {
            this.toggleButtonState(button, false);
        }
    }

    async handleStop(button) {
        try {
            this.toggleButtonState(button, true);
            const response = await this.fetchAPI('playback/stop', {
                method: 'POST',
                body: JSON.stringify({ user_id: this.currentUserId })
            });

            if (response?.success) {
                this.showAlert('success', 'Stopped', 'Playback stopped');
            } else {
                throw new Error(response?.error || 'Failed to stop playback');
            }
        } catch (error) {
            this.showError('Stop Error', error.message);
        } finally {
            this.toggleButtonState(button, false);
        }
    }

    async handleEdit(button) {
        const playlistId = button?.dataset?.playlistId;
        if (!playlistId) return this.showError('Error', 'No playlist selected');

        try {
            this.toggleButtonState(button, true);
            window.location.href = `/playlists/edit/${playlistId}`;
        } catch (error) {
            this.showError('Navigation Error', error.message);
        } finally {
            this.toggleButtonState(button, false);
        }
    }

    async handleDelete(button) {
        const playlistId = button?.dataset?.playlistId;
        if (!playlistId) return this.showError('Error', 'No playlist selected');

        try {
            const confirm = await Swal.fire({
                title: 'Are you sure?',
                text: "You won't be able to revert this!",
                icon: 'warning',
                showCancelButton: true,
                confirmButtonColor: '#d33',
                cancelButtonColor: '#3085d6',
                confirmButtonText: 'Yes, delete it!'
            });

            if (confirm.isConfirmed) {
                this.toggleButtonState(button, true);
                const response = await this.fetchAPI(`playlists/${playlistId}`, {
                    method: 'DELETE',
                    body: JSON.stringify({ user_id: this.currentUserId })
                });

                if (response?.success) {
                    this.showAlert('success', 'Deleted', 'Playlist removed successfully');
                    button.closest('.playlist-item')?.remove();
                } else {
                    throw new Error(response?.error || 'Failed to delete playlist');
                }
            }
        } catch (error) {
            this.showError('Delete Error', error.message);
        } finally {
            this.toggleButtonState(button, false);
        }
    }
}

// Default export for the module
export default PlayerControls;
