(function() {
    // Кэш для превью медиафайлов
    const previewCache = new Map();
    
    // Получаем ID плейлиста из URL
    function getPlaylistId() {
        const params = new URLSearchParams(window.location.search);
        let id = params.get('id') || window.location.pathname.split('/').pop();
        return id && !isNaN(id) ? id : null;
    }

    const playlistId = getPlaylistId();
    const fileListEl = document.getElementById('file-list');
    const saveBtn = document.getElementById('save-playlist');

    // Улучшенная реализация showAlert
    const showAlert = window.App?.Alerts?.show || function(type, title, message) {
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type} fade show`;
        alertDiv.innerHTML = `
            <strong>${title}</strong> ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        `;
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

    // Функция для получения CSRF токена
    function getCSRFToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content || '';
    }

    // Обработчик изменений чекбоксов
    function setupCheckboxHandlers() {
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('include-checkbox')) {
                const filename = e.target.dataset.filename;
                console.log(`File ${filename} ${e.target.checked ? 'added to' : 'removed from'} playlist`);
            }
        });
    }

    // Загрузка медиафайлов с кэшированием
    async function loadMediaFiles() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        try {
            const cacheKey = `media-files-${playlistId}`;
            const cachedData = sessionStorage.getItem(cacheKey);
            
            if (cachedData) {
                const cache = JSON.parse(cachedData);
                if (Date.now() - cache.timestamp < 60000) {
                    renderFileTable(cache.data.files);
                    return;
                }
            }

            const response = await fetch(`/api/media/files?playlist_id=${playlistId}`, {
                headers: { 'Accept': 'application/json' },
                credentials: 'include'
            });
        
            if (!response.ok) throw new Error(`Ошибка сервера: ${response.status}`);
            
            const data = await response.json();
            if (!data?.success) throw new Error(data?.error || 'Неверный формат ответа');
            
            sessionStorage.setItem(cacheKey, JSON.stringify({ 
                timestamp: Date.now(), 
                data: data 
            }));
            renderFileTable(data.files);
        } catch (error) {
            console.error('Ошибка загрузки файлов:', error);
            showAlert('error', 'Ошибка', `Не удалось загрузить медиафайлы: ${error.message}`);
        }
    }

    // Предпросмотр изображений
    async function loadPreview(file) {
        const cacheKey = `preview-${file.filename}`;
    
        // Check memory cache first
        if (previewCache.has(cacheKey)) {
            return previewCache.get(cacheKey);
        }

        // For video files, use default preview if we've tried before
        if (file.is_video && sessionStorage.getItem(`video-fallback-${file.filename}`)) {
            return '/static/images/default-preview.jpg';
        }

        const previewUrl = `/api/media/thumbnail/${encodeURIComponent(file.filename)}`;
        const fallbackUrl = '/static/images/default-preview.jpg';

        try {
            const response = await fetch(previewUrl, {
                credentials: 'include'
            });
        
            // If we got a valid image response
            if (response.ok && response.headers.get('Content-Type')?.startsWith('image/')) {
                previewCache.set(cacheKey, previewUrl);
                return previewUrl;
            }
        
            throw new Error('Invalid thumbnail response');
        
        } catch (error) {
            console.warn(`Preview load failed for ${file.filename}:`, error);
        
            // Mark video files to use fallback in future
            if (file.is_video) {
                sessionStorage.setItem(`video-fallback-${file.filename}`, 'true');
            }
        
            return fallbackUrl;
        }
    }

    // Рендеринг таблицы файлов
    function renderFileTable(files) {
        if (!fileListEl) return;

        const emptyMessage = document.getElementById('empty-playlist-message');
        if (!files || files.length === 0) {
            fileListEl.innerHTML = '';
            if (emptyMessage) emptyMessage.style.display = 'block';
            return;
        }

        if (emptyMessage) emptyMessage.style.display = 'none';
        fileListEl.innerHTML = '';

        files.forEach((file, index) => {
            const row = document.createElement('tr');
            const img = document.createElement('img');
            img.src = '/static/images/default-preview.jpg';  // Prevents flickering
            img.alt = 'Preview';
            img.className = `file-preview ${file.is_video ? 'video-thumbnail' : ''}`;
            img.dataset.filename = file.filename;
        
            row.innerHTML = `
                <td>${index + 1}</td>
                <td><input type="checkbox" class="include-checkbox" data-filename="${file.filename}" ${file.included ? 'checked' : ''}></td>
                <td></td>
                <td>${file.filename}</td>
                <td>
                    <input type="number" class="duration-input" data-filename="${file.filename}" 
                          value="${file.duration || 10}" min="1" ${file.is_video ? 'readonly' : ''}>
                </td>
            `;
        
            // Insert the img element we created
            row.querySelector('td:nth-child(3)').appendChild(img);
            fileListEl.appendChild(row);
        
            // Load the preview async
            loadPreview(file).then(url => {
                img.src = url;
            });
        });
    }

    // Сохранение плейлиста
    async function savePlaylist() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        toggleButtonState(saveBtn, true);
    
        try {
            const selectedFiles = Array.from(document.querySelectorAll('.include-checkbox:checked'))
                .map(checkbox => ({
                    filename: checkbox.dataset.filename,
                    duration: parseInt(document.querySelector(`.duration-input[data-filename="${checkbox.dataset.filename}"]`)?.value || 10)
                }));

            const response = await fetch(`/api/playlists/${playlistId}/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ files: selectedFiles })
            });

            if (!response.ok) throw new Error((await response.json())?.error || 'Ошибка сервера');
            
            showAlert('success', 'Успех', 'Плейлист сохранен');
            sessionStorage.removeItem(`media-files-${playlistId}`);
            await loadMediaFiles();
            
            if (window.App?.Sockets) {
                window.App.Sockets.emit('playlist_updated', { playlist_id: playlistId });
            }
        } catch (error) {
            console.error('Ошибка сохранения:', error);
            showAlert('error', 'Ошибка', error.message);
        } finally {
            toggleButtonState(saveBtn, false);
        }
    }

    // Инициализация
    document.addEventListener('DOMContentLoaded', () => {
        try {
            if (fileListEl && saveBtn) {
                saveBtn.addEventListener('click', savePlaylist);
                loadMediaFiles();
                setupCheckboxHandlers();
                
                if (window.App?.Sockets?.socket) {
                    window.App.Sockets.socket.on('playlist_updated', (data) => {
                        if (data.playlist_id == playlistId) {
                            sessionStorage.removeItem(`media-files-${playlistId}`);
                            loadMediaFiles();
                        }
                    });
                }
            } else {
                console.error('Не найдены необходимые элементы DOM');
            }
        } catch (error) {
            console.error('Ошибка инициализации:', error);
            showAlert('error', 'Ошибка', 'Не удалось загрузить плейлист');
        }
    });
})();
