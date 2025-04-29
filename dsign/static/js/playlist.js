(function() {
    // Кэш для превью медиафайлов
    const previewCache = new Map();
    const imageCache = new Map();
	
	function getCachedImage(url) {
        if (imageCache.has(url)) {
            return imageCache.get(url);
        }
        const img = new Image();
        img.src = url;
        imageCache.set(url, img);
        return img;
    }
	
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

    // Оптимизированная загрузка медиафайлов с кэшированием
    async function loadMediaFiles() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        try {
            const cacheKey = `media-files-${playlistId}`;
            const cachedData = sessionStorage.getItem(cacheKey);
            
            // Если есть кэшированные данные и они не старше 1 минуты
            if (cachedData) {
                const { timestamp, data } = JSON.parse(cachedData);
                if (Date.now() - timestamp < 60000) {
                    renderFileTable(data.files);
                    return;
                }
            }

            const response = await fetch(`/api/media/files?playlist_id=${playlistId}`, {
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Cache-Control': 'no-cache'
                },
                credentials: 'include'
            });
        
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.error || `Ошибка сервера: ${response.status}`);
            }
        
            const data = await response.json();
        
            if (data?.success) {
                // Кэшируем полученные данные
                sessionStorage.setItem(cacheKey, JSON.stringify({
                    timestamp: Date.now(),
                    data
                }));
                renderFileTable(data.files);
            } else {
                throw new Error(data?.error || 'Неверный формат ответа');
            }
        } catch (error) {
            console.error('Ошибка загрузки файлов:', error);
            showAlert('error', 'Ошибка', `Не удалось загрузить медиафайлы: ${error.message}`);
        }
    }

    // Функция для загрузки превью с кэшированием
    async function loadPreview(file) {
        const cacheKey = `preview-${file.id}`;
        
        if (previewCache.has(cacheKey)) {
            return previewCache.get(cacheKey);
        }

        try {
            const previewUrl = file.is_video ? 
                `/media/${file.filename}?thumb=1` : 
                '/static/images/default-preview.jpg';
            
            // Для изображений сразу возвращаем URL, для видео создаем объект Image
            if (!file.is_video) {
                previewCache.set(cacheKey, previewUrl);
                return previewUrl;
            }

            return new Promise((resolve) => {
                const img = new Image();
                img.src = previewUrl;
                img.onload = () => {
                    previewCache.set(cacheKey, previewUrl);
                    resolve(previewUrl);
                };
                img.onerror = () => {
                    const fallback = '/static/images/default-preview.jpg';
                    previewCache.set(cacheKey, fallback);
                    resolve(fallback);
                };
            });
        } catch (error) {
            console.error('Ошибка загрузки превью:', error);
            return '/static/images/default-preview.jpg';
        }
    }

    // Оптимизированный рендеринг таблицы файлов
    async function renderFileTable(files) {
        if (!fileListEl) return;
    
        const emptyMessage = document.getElementById('empty-playlist-message');

        if (!files || files.length === 0) {
            fileListEl.innerHTML = '';
            emptyMessage.style.display = 'block';
            return;
        }

        emptyMessage.style.display = 'none';

        // Создаем временный элемент для вставки HTML
        const fragment = document.createDocumentFragment();
    
        for (const [index, file] of files.entries()) {
            const row = document.createElement('tr');
        
            // Для видео используем эндпоинт API для превью
            const previewUrl = file.is_video ? 
                `/api/media/thumbnail/${encodeURIComponent(file.filename)}` : 
                '/static/images/default-file-icon.png';

            row.innerHTML = `
                <td>${index + 1}</td>
                <td><input type="checkbox" class="include-checkbox" data-id="${file.id}" ${file.included ? 'checked' : ''}></td>
                <td>
                    <img src="${previewUrl}" 
                         alt="Preview" 
                         class="file-preview ${file.is_video ? 'video-thumbnail' : 'file-icon'}"
                         onerror="this.onerror=null;this.src='/static/images/default-preview.jpg?v='+Date.now();this.style.display='block'">
                </td>
                <td>${file.filename}</td>
                <td>
                     <input type="number" class="duration-input" data-id="${file.id}" 
                           value="${file.duration || 10}" min="1" ${file.is_video ? 'readonly' : ''}>
                </td>
            `;
        
            fragment.appendChild(row);
        }

        fileListEl.innerHTML = '';
        fileListEl.appendChild(fragment);
    }

    // Оптимизированное сохранение плейлиста
    async function savePlaylist() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        toggleButtonState(saveBtn, true);
    
        try {
            // Собираем данные более эффективно
            const selectedFiles = Array.from(document.querySelectorAll('.include-checkbox:checked'))
                .map(checkbox => {
                    const fileId = checkbox.dataset.id;
                    const durationInput = document.querySelector(`.duration-input[data-id="${fileId}"]`);
                    return {
                        id: fileId,
                        duration: durationInput ? parseInt(durationInput.value) || 10 : 10
                    };
                });

            const response = await fetch(`/api/playlists/${playlistId}/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify({ files: selectedFiles })
            });

            const result = await response.json();
            
            if (!response.ok) {
                throw new Error(result.error || 'Ошибка сервера');
            }

            showAlert('success', 'Успех', 'Плейлист сохранен');
            
            // Очищаем кэш и обновляем данные
            sessionStorage.removeItem(`media-files-${playlistId}`);
            await loadMediaFiles();
            
            // Отправляем событие обновления через сокеты, если доступно
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

    // Инициализация с обработкой ошибок
    document.addEventListener('DOMContentLoaded', () => {
        try {
            if (fileListEl && saveBtn) {
                saveBtn.addEventListener('click', savePlaylist);
                loadMediaFiles();
                
                // Добавляем обработчик для инвалидации кэша при изменении файлов
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
