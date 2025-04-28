(function() {
    // Получаем ID плейлиста из URL
    function getPlaylistId() {
        const params = new URLSearchParams(window.location.search);
        let id = params.get('id');
        
        // Альтернативный вариант для Flask-роута /playlist/<int:playlist_id>
        if (!id) {
            const pathParts = window.location.pathname.split('/');
            id = pathParts[pathParts.length - 1];
        }
        
        if (!id || isNaN(id)) {
            console.error('Invalid playlist ID');
            return null;
        }
        return id;
    }

    const playlistId = getPlaylistId();
    const fileListEl = document.getElementById('file-list');
    const saveBtn = document.getElementById('save-playlist');

    // Простая реализация showAlert если глобальная не доступна
    const showAlert = window.App?.Alerts?.show || function(type, title, message) {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.innerHTML = `<strong>${title}</strong> ${message}`;
        document.body.prepend(alertDiv);
        setTimeout(() => alertDiv.remove(), 5000);
    };

    function toggleButtonState(button, isLoading) {
        if (!button) return;
        button.disabled = isLoading;
        button.innerHTML = isLoading ? 
            '<i class="fas fa-spinner fa-spin"></i> Сохранение...' : 
            '<i class="fas fa-save"></i> Сохранить плейлист';
    }

    async function loadMediaFiles() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        try {
            const response = await fetch(`/api/media/files?playlist_id=${playlistId}`, {
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                credentials: 'include'
            });
            
            if (!response.ok) throw new Error(`Ошибка сервера: ${response.status}`);
            
            const data = await response.json();
            
            if (data?.success) {
                renderFileTable(data.files);
            } else {
                throw new Error(data?.error || 'Неверный формат ответа');
            }
        } catch (error) {
            console.error('Ошибка загрузки файлов:', error);
            showAlert('error', 'Ошибка', `Не удалось загрузить медиафайлы: ${error.message}`);
        }
    }

    function renderFileTable(files) {
        if (!files || !Array.isArray(files)) {
            showAlert('warning', 'Внимание', 'Нет файлов для отображения');
            return;
        }

        fileListEl.innerHTML = files.map((file, index) => `
            <tr>
                <td>${index + 1}</td>
                <td><input type="checkbox" class="include-checkbox" data-id="${file.id}" ${file.included ? 'checked' : ''}></td>
                <td>
                    ${file.is_video ? 
                        `<img src="/media/${file.filename}?thumb=1" alt="Preview" class="file-preview" 
                              onerror="this.src='/static/images/default-preview.jpg'">` :
                        `<div class="file-icon">📄</div>`
                    }
                </td>
                <td>${file.filename}</td>
                <td>
                    <input type="number" class="duration-input" data-id="${file.id}" 
                           value="${file.duration || 10}" min="1" ${file.is_video ? 'readonly' : ''}>
                </td>
            </tr>
        `).join('');
    }

    async function savePlaylist() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        toggleButtonState(saveBtn, true);
        
        try {
            const includedFiles = Array.from(document.querySelectorAll('.include-checkbox:checked'))
                .map(checkbox => checkbox.dataset.id);
                
            const durations = {};
            document.querySelectorAll('.duration-input').forEach(input => {
                durations[input.dataset.id] = parseInt(input.value) || 10;
            });

            const csrfToken = document.querySelector('input[name="csrf_token"]')?.value;
            const response = await fetch(`/api/playlists/${playlistId}/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({ 
                    included_files: includedFiles, 
                    durations 
                })
            });

            if (!response.ok) throw new Error(`Ошибка сервера: ${response.status}`);
            
            const result = await response.json();
            if (result.success) {
                showAlert('success', 'Успех', 'Плейлист успешно сохранен');
            } else {
                throw new Error(result.error || 'Не удалось сохранить плейлист');
            }
        } catch (error) {
            console.error('Ошибка сохранения:', error);
            showAlert('error', 'Ошибка', `Не удалось сохранить плейлист: ${error.message}`);
        } finally {
            toggleButtonState(saveBtn, false);
        }
    }

    // Инициализация
    if (fileListEl && saveBtn) {
        saveBtn.addEventListener('click', savePlaylist);
        document.addEventListener('DOMContentLoaded', loadMediaFiles);
    } else {
        console.error('Не найдены необходимые элементы DOM');
    }
})();
