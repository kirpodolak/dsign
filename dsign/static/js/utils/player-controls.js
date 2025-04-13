(function() {
    const { fetch: fetchAPI } = window.App.API || {};
    const { showAlert, showError } = window.App.Alerts || {};
    const { toggleButtonState } = window.App.Helpers || {};

    class PlayerControls {
        constructor() {
            this.currentUserId = null;
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
            if (!playlistId) return showError('Error', 'No playlist selected');

            try {
                toggleButtonState(button, true);
                const response = await fetchAPI('playback/play', {
                    method: 'POST',
                    body: JSON.stringify({ 
                        playlist_id: playlistId,
                        user_id: this.currentUserId 
                    })
                });

                if (response?.success) {
                    showAlert('success', 'Playing', `Started playlist: ${response.playlist_name || playlistId}`);
                } else {
                    throw new Error(response?.error || 'Failed to start playback');
                }
            } catch (error) {
                showError('Playback Error', error.message);
            } finally {
                toggleButtonState(button, false);
            }
        }

        async handleStop(button) {
            try {
                toggleButtonState(button, true);
                const response = await fetchAPI('playback/stop', {
                    method: 'POST',
                    body: JSON.stringify({ user_id: this.currentUserId })
                });

                if (response?.success) {
                    showAlert('success', 'Stopped', 'Playback stopped');
                } else {
                    throw new Error(response?.error || 'Failed to stop playback');
                }
            } catch (error) {
                showError('Stop Error', error.message);
            } finally {
                toggleButtonState(button, false);
            }
        }

        async handleEdit(button) {
            const playlistId = button?.dataset?.playlistId;
            if (!playlistId) return showError('Error', 'No playlist selected');

            try {
                toggleButtonState(button, true);
                window.location.href = `/playlists/edit/${playlistId}`;
            } catch (error) {
                showError('Navigation Error', error.message);
            } finally {
                toggleButtonState(button, false);
            }
        }

        async handleDelete(button) {
            const playlistId = button?.dataset?.playlistId;
            if (!playlistId) return showError('Error', 'No playlist selected');

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
                    toggleButtonState(button, true);
                    const response = await fetchAPI(`playlists/${playlistId}`, {
                        method: 'DELETE',
                        body: JSON.stringify({ user_id: this.currentUserId })
                    });

                    if (response?.success) {
                        showAlert('success', 'Deleted', 'Playlist removed successfully');
                        button.closest('.playlist-item')?.remove();
                    } else {
                        throw new Error(response?.error || 'Failed to delete playlist');
                    }
                }
            } catch (error) {
                showError('Delete Error', error.message);
            } finally {
                toggleButtonState(button, false);
            }
        }
    }

    window.App = window.App || {};
    window.App.PlayerControls = new PlayerControls();
})();