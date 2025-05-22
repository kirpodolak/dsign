// Кэш для превью медиафайлов
const previewCache = new Map();

// Утилитные функции
function getPlaylistId() {
    const params = new URLSearchParams(window.location.search);
    let id = params.get('id') || window.location.pathname.split('/').pop();
    return id && !isNaN(id) ? id : null;
}

function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function toggleButtonState(button, isLoading) {
    if (!button) return;
    button.disabled = isLoading;
    button.innerHTML = isLoading ? 
        '<i class="fas fa-spinner fa-spin"></i> Сохранение...' : 
        '<i class="fas fa-save"></i> Сохранить плейлист';
}

// UI компонент для уведомлений
class PlaylistUI {
    constructor() {
        this.setupStyles();
    }

    setupStyles() {
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes fadeOut {
                to { opacity: 0; transform: translateX(100%); }
            }
        `;
        document.head.appendChild(style);
    }

    showAlert(message, type = 'info', duration = 5000) {
        // Создаем контейнер для уведомлений, если его еще нет
        let alertsContainer = document.getElementById('alerts-container');
        if (!alertsContainer) {
            alertsContainer = document.createElement('div');
            alertsContainer.id = 'alerts-container';
            alertsContainer.style.position = 'fixed';
            alertsContainer.style.top = '20px';
            alertsContainer.style.right = '20px';
            alertsContainer.style.zIndex = '10000';
            alertsContainer.style.maxWidth = '350px';
            alertsContainer.style.width = '100%';
            document.body.appendChild(alertsContainer);
        }

        // Создаем элемент уведомления
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type}`;
        alertDiv.style.cssText = `
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 8px;
            background: ${this.getAlertColor(type)};
            color: white;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            animation: slideIn 0.3s ease-out forwards;
            display: flex;
            align-items: center;
            justify-content: space-between;
        `;

        // Добавляем иконку в зависимости от типа
        const icons = {
            success: 'fa-check-circle',
            error: 'fa-times-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };

        alertDiv.innerHTML = `
            <div style="display: flex; align-items: center; gap: 10px;">
                <i class="fas ${icons[type] || 'fa-info-circle'}" style="font-size: 1.5rem;"></i>
                <div>
                    <div style="font-weight: bold; margin-bottom: 5px;">${type === 'error' ? 'Ошибка' : 
                        type === 'success' ? 'Успех' : 
                        type === 'warning' ? 'Внимание' : 'Информация'}</div>
                    <div>${message}</div>
                </div>
            </div>
            <button class="alert-close-btn" style="background: none; border: none; color: white; cursor: pointer;">
                <i class="fas fa-times"></i>
            </button>
        `;

        // Добавляем уведомление в контейнер
        alertsContainer.prepend(alertDiv);

        // Настраиваем закрытие по клику
        const closeBtn = alertDiv.querySelector('.alert-close-btn');
        closeBtn.addEventListener('click', () => {
            this.closeAlert(alertDiv);
        });

        // Автоматическое закрытие
        setTimeout(() => {
            this.closeAlert(alertDiv);
        }, duration);

        return {
            element: alertDiv,
            close: () => this.closeAlert(alertDiv)
        };
    }

    closeAlert(alertDiv) {
        if (alertDiv.parentNode) {
            alertDiv.style.animation = 'fadeOut 0.3s ease-in forwards';
            setTimeout(() => alertDiv.remove(), 300);
        }
    }

    getAlertColor(type) {
        const colors = {
            success: '#28a745',
            error: '#dc3545',
            warning: '#ffc107',
            info: '#17a2b8'
        };
        return colors[type] || colors.info;
    }
}

// Основной класс плейлиста
export class PlaylistManager {
    constructor() {
        this.playlistId = getPlaylistId();
        this.fileListEl = document.getElementById('file-list');
        this.saveBtn = document.getElementById('save-playlist');
        this.exportBtn = document.getElementById('export-m3u');
        this.emptyMessage = document.getElementById('empty-playlist-message');
        this.ui = new PlaylistUI();

        this.init();
    }

    init() {
        if (!this.fileListEl || !this.saveBtn) {
            console.error('Не найдены необходимые элементы DOM');
            return;
        }

        this.saveBtn.addEventListener('click', () => this.savePlaylist());
        
        if (this.exportBtn) {
            this.exportBtn.addEventListener('click', () => this.exportM3U());
        }
        
        this.loadMediaFiles();
        this.setupCheckboxHandlers();
        
        if (window.App?.Sockets?.socket) {
            window.App.Sockets.socket.on('playlist_updated', (data) => {
                if (data.playlist_id == this.playlistId) {
                    sessionStorage.removeItem(`media-files-${this.playlistId}`);
                    this.loadMediaFiles();
                    
                    if (data.m3u_generated) {
                        this.ui.showAlert('M3U файл был автоматически обновлен', 'info');
                    }
                }
            });
        }
    }

    // Обработчик изменений чекбоксов
    setupCheckboxHandlers() {
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('include-checkbox')) {
                const filename = e.target.dataset.filename;
                console.log(`File ${filename} ${e.target.checked ? 'added to' : 'removed from'} playlist`);
            }
        });
    }

    // Загрузка медиафайлов с кэшированием
    async loadMediaFiles() {
        if (!this.playlistId) {
            this.ui.showAlert('Неверный ID плейлиста', 'error');
            return;
        }

        try {
            const cacheKey = `media-files-${this.playlistId}`;
            const cachedData = sessionStorage.getItem(cacheKey);
            
            if (cachedData) {
                const cache = JSON.parse(cachedData);
                if (Date.now() - cache.timestamp < 60000) {
                    this.renderFileTable(cache.data.files);
                    return;
                }
            }

            const response = await fetch(`/api/media/files?playlist_id=${this.playlistId}`, {
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
            this.renderFileTable(data.files);
        } catch (error) {
            console.error('Ошибка загрузки файлов:', error);
            this.ui.showAlert(`Не удалось загрузить медиафайлы: ${error.message}`, 'error');
        }
    }

    // Предпросмотр изображений
    async loadPreview(file) {
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
    renderFileTable(files) {
        if (!this.fileListEl) return;

        if (!files || files.length === 0) {
            this.fileListEl.innerHTML = '';
            if (this.emptyMessage) this.emptyMessage.style.display = 'block';
            return;
        }

        if (this.emptyMessage) this.emptyMessage.style.display = 'none';
        this.fileListEl.innerHTML = '';

        files.forEach((file, index) => {
            const row = document.createElement('tr');
            const img = document.createElement('img');
            img.src = '/static/images/default-preview.jpg';
            img.alt = 'Preview';
            img.className = `file-preview ${file.is_video ? 'video-thumbnail' : ''}`;
            img.dataset.filename = file.filename;
        
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
        
            row.querySelector('td:nth-child(3)').appendChild(img);
            this.fileListEl.appendChild(row);
        
            this.loadPreview(file).then(url => {
                img.src = url;
            });
        });
    }

    // Сохранение плейлиста с генерацией M3U
    async savePlaylist() {
        if (!this.playlistId) {
            this.ui.showAlert('Неверный ID плейлиста', 'error');
            return;
        }

        toggleButtonState(this.saveBtn, true);

        try {
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

                    const fileExt = filename.toLowerCase().split('.').pop();
                    const isVideo = ['mp4', 'avi', 'mov', 'mkv'].includes(fileExt);
                    const isImage = ['jpg', 'jpeg', 'png'].includes(fileExt);

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
                    this.ui.showAlert(error.message, 'warning');
                    hasErrors = true;
                }
            }

            if (hasErrors) {
                throw new Error('Обнаружены ошибки в данных файлов');
            }

            if (selectedFiles.length === 0) {
                throw new Error('Не выбрано ни одного файла');
            }

            const response = await fetch(`/api/playlists/${this.playlistId}/files`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({
                    files: selectedFiles,
                    meta: {
                        generate_m3u: true
                    }
                })
            });

            const result = await response.json();
            
            if (!response.ok || !result.success) {
                const errorMsg = result.error || `HTTP error ${response.status}`;
                
                if (errorMsg.includes('file_name') || errorMsg.includes('invalid')) {
                    throw new Error('Ошибка данных. Пожалуйста, обновите страницу и попробуйте снова.');
                }
                throw new Error(errorMsg);
            }

            this.ui.showAlert('Плейлист сохранен. M3U файл обновлен.', 'success');
            sessionStorage.removeItem(`media-files-${this.playlistId}`);
            await this.loadMediaFiles();

            if (window.App?.Sockets) {
                window.App.Sockets.emit('playlist_updated', {
                    playlist_id: this.playlistId,
                    updated_files: selectedFiles.length,
                    m3u_generated: true
                });
            }

        } catch (error) {
            console.error('Ошибка сохранения:', error);
            
            let errorMessage = error.message;
            if (error.message.includes('недостаточно места')) {
                errorMessage = 'Недостаточно места на сервере';
            } else if (error.message.includes('validation')) {
                errorMessage = 'Ошибка валидации данных';
            }
            
            this.ui.showAlert(errorMessage || 'Не удалось сохранить плейлист', 'error');

        } finally {
            toggleButtonState(this.saveBtn, false);
        }
    }

    // Экспорт M3U
    async exportM3U() {
        if (!this.playlistId) return;
        
        try {
            const response = await fetch(`/api/playlists/${this.playlistId}/export-m3u`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCSRFToken()
                }
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.ui.showAlert(`M3U файл успешно экспортирован: ${result.filename}`, 'success');
            } else {
                throw new Error(result.error || 'Ошибка экспорта');
            }
        } catch (error) {
            console.error('Ошибка экспорта:', error);
            this.ui.showAlert(error.message || 'Не удалось экспортировать M3U', 'error');
        }
    }
}

// Инициализация при загрузке DOM
document.addEventListener('DOMContentLoaded', () => {
    const playlistManager = new PlaylistManager();
    
    // Для обратной совместимости
    window.App = window.App || {};
    window.App.PlaylistManager = playlistManager;
});

export default PlaylistManager;
