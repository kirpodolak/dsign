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
    const exportBtn = document.getElementById('export-m3u'); // Новая кнопка экспорта

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

        const previewUrl = `/api/media/thumbnail/${encodeURIComponent(file.filename)}`;
        const fallbackUrl = '/static/images/default-preview.jpg';

        try {
            const response = await fetch(previewUrl, {
                credentials: 'include'
            });
        
            if (response.ok && response.headers.get('Content-Type')?.startsWith('image/')) {
                const blob = await response.blob();
            
                // Verify the image is valid and has reasonable size
                if (blob.size > 1024) {
                    const url = URL.createObjectURL(blob);
                    previewCache.set(cacheKey, url);
                
                    // Add video indicator if needed
                    if (file.is_video) {
                        setTimeout(() => {
                            const img = document.querySelector(`img[data-filename="${file.filename}"]`);
                            if (img) {
                                img.classList.add('video-thumbnail');
                            }
                        }, 100);
                    }
                
                    return url;
                }
            }
        
            throw new Error('Invalid thumbnail response');
        
        } catch (error) {
            console.warn(`Preview load failed for ${file.filename}:`, error);
        
            // For video files, mark to use fallback in future
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
            img.src = '/static/images/default-preview.jpg'; // Заглушка для предотвращения мерцания
            img.alt = 'Preview';
            img.className = `file-preview ${file.is_video ? 'video-thumbnail' : ''}`;
            img.dataset.filename = file.filename;
        
            // Определяем, является ли файл видео (по расширению или флагу is_video)
            const isVideo = file.is_video || ['.mp4', '.avi', '.mov', '.mkv'].some(ext => file.filename.toLowerCase().endsWith(ext));
        
            row.innerHTML = `
                <td>${index + 1}</td>
                <td><input type="checkbox" class="include-checkbox" data-filename="${file.filename}" ${file.included ? 'checked' : ''}></td>
                <td></td>
                <td>${file.filename}</td>
                <td>
                    ${isVideo ? 
                        '<span class="video-duration">Полное видео</span>' : 
                        `<input type="number" class="duration-input" data-filename="${file.filename}" 
                          value="${file.duration || 10}" min="1">`
                    }
                </td>
            `;
        
            // Вставляем элемент изображения
            row.querySelector('td:nth-child(3)').appendChild(img);
            fileListEl.appendChild(row);
        
            // Загружаем превью асинхронно
            loadPreview(file).then(url => {
                img.src = url;
            });
        });
    }

    // Сохранение плейлиста с генерацией M3U
    async function savePlaylist() {
        if (!playlistId) {
            showAlert('error', 'Ошибка', 'Неверный ID плейлиста');
            return;
        }

        toggleButtonState(saveBtn, true);

        try {
            // Собираем файлы с учетом порядка и валидацией
            const rows = Array.from(document.querySelectorAll('#file-list tr'));
            const selectedFiles = [];
            let hasErrors = false;

            for (const [index, row] of rows.entries()) {
                try {
                    const checkbox = row.querySelector('.include-checkbox');
                    if (!checkbox?.checked) continue;

                    const filename = checkbox.dataset.filename;
                    if (!filename || typeof filename !== 'string') {
                        throw new Error(`Некорректное имя файла в строке ${index + 1}`);
                    }

                    // Определяем тип файла по расширению
                    const fileExt = filename.toLowerCase().split('.').pop();
                    const isVideo = ['mp4', 'avi', 'mov', 'mkv'].includes(fileExt);
                    const isImage = ['jpg', 'jpeg', 'png'].includes(fileExt);

                    // Валидация длительности
                    let duration = 10;
                    if (!isVideo) {
                        const durationInput = row.querySelector('.duration-input');
                        duration = Math.max(1, parseInt(durationInput?.value || 10));
                        
                        if (isNaN(duration)) {
                            throw new Error(`Некорректная длительность для файла ${filename}`);
                        }
                    }

                    selectedFiles.push({
                        file_name: filename,
                        duration: isVideo ? 0 : duration,
                        order: index + 1
                    });

                } catch (error) {
                    console.error(`Ошибка обработки файла: ${error.message}`);
                    showAlert('warning', 'Внимание', error.message);
                    hasErrors = true;
                }
            }

            if (hasErrors) {
                throw new Error('Обнаружены ошибки в данных файлов');
            }

            if (selectedFiles.length === 0) {
                throw new Error('Не выбрано ни одного файла');
            }

            // Отправка данных на сервер
            const response = await fetch(`/api/playlists/${playlistId}/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({
                    files: selectedFiles,
                    meta: {
                        generate_m3u: true // Явно указываем, что нужно сгенерировать M3U
                    }
                })
            });

            const result = await response.json();
            
            if (!response.ok || !result.success) {
                const errorMsg = result.error || `HTTP error ${response.status}`;
                
                // Специальная обработка ошибок валидации
                if (errorMsg.includes('file_name') || errorMsg.includes('invalid')) {
                    throw new Error('Ошибка данных. Пожалуйста, обновите страницу и попробуйте снова.');
                }
                throw new Error(errorMsg);
            }

            // Успешное сохранение
            showAlert('success', 'Успех', 'Плейлист сохранен. M3U файл обновлен.');
            sessionStorage.removeItem(`media-files-${playlistId}`);
            await loadMediaFiles();

            // Отправка события обновления через сокеты
            if (window.App?.Sockets) {
                window.App.Sockets.emit('playlist_updated', {
                    playlist_id: playlistId,
                    updated_files: selectedFiles.length,
                    m3u_generated: true
                });
            }

        } catch (error) {
            console.error('Ошибка сохранения:', error);
            
            // Улучшенные сообщения об ошибках
            let errorMessage = error.message;
            if (error.message.includes('недостаточно места')) {
                errorMessage = 'Недостаточно места на сервере';
            } else if (error.message.includes('validation')) {
                errorMessage = 'Ошибка валидации данных';
            }
            
            showAlert('error', 'Ошибка', errorMessage || 'Не удалось сохранить плейлист');

        } finally {
            toggleButtonState(saveBtn, false);
        }
    }

    // Экспорт M3U (дополнительная функция)
    async function exportM3U() {
        if (!playlistId) return;
        
        try {
            const response = await fetch(`/api/playlists/${playlistId}/export-m3u`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCSRFToken()
                }
            });
            
            const result = await response.json();
            
            if (result.success) {
                showAlert('success', 'Успех', `M3U файл успешно экспортирован: ${result.filename}`);
            } else {
                throw new Error(result.error || 'Ошибка экспорта');
            }
        } catch (error) {
            console.error('Ошибка экспорта:', error);
            showAlert('error', 'Ошибка', error.message || 'Не удалось экспортировать M3U');
        }
    }

    // Инициализация
    document.addEventListener('DOMContentLoaded', () => {
        try {
            if (fileListEl && saveBtn) {
                saveBtn.addEventListener('click', savePlaylist);
                
                // Добавляем обработчик для кнопки экспорта, если она есть
                if (exportBtn) {
                    exportBtn.addEventListener('click', exportM3U);
                }
                
                loadMediaFiles();
                setupCheckboxHandlers();
                
                if (window.App?.Sockets?.socket) {
                    window.App.Sockets.socket.on('playlist_updated', (data) => {
                        if (data.playlist_id == playlistId) {
                            sessionStorage.removeItem(`media-files-${playlistId}`);
                            loadMediaFiles();
                            
                            if (data.m3u_generated) {
                                showAlert('info', 'Обновление', 'M3U файл был автоматически обновлен');
                            }
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
