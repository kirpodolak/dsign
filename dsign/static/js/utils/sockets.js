(function() {
    // Конфигурация
    const CONFIG = {
        MAX_RETRIES: 5,
        RETRY_DELAY: 1000,
        RECONNECT_ATTEMPTS: 5,
        RECONNECT_DELAY: 1000
    };

    // Основной класс для управления сокетами
    class SocketManager {
        constructor() {
            this.socket = null;
            this.isConnected = false;
            this.reconnectAttempts = 0;
            this.pendingEvents = [];
            this.init();
        }

        init() {
            try {
                // Проверяем доступность Socket.IO
                if (typeof io === 'undefined') {
                    throw new Error('Socket.IO library not loaded');
                }

                this.socket = io({
                    reconnection: true,
                    reconnectionAttempts: CONFIG.RECONNECT_ATTEMPTS,
                    reconnectionDelay: CONFIG.RECONNECT_DELAY,
                    transports: ['websocket', 'polling']
                });

                this.setupEventHandlers();
            } catch (error) {
                console.error('Socket initialization error:', error);
                this.handleRetry();
            }
        }

        setupEventHandlers() {
            this.socket.on('connect', () => {
                this.handleConnect();
            });

            this.socket.on('disconnect', (reason) => {
                this.handleDisconnect(reason);
            });

            this.socket.on('connect_error', (error) => {
                this.handleError(error);
            });

            // Обработчики кастомных событий
            this.socket.on('playback_update', (data) => {
                this.handlePlaybackUpdate(data);
            });

            this.socket.on('playlist_update', (data) => {
                this.handlePlaylistUpdate(data);
            });

            this.socket.on('system_notification', (data) => {
                this.handleSystemNotification(data);
            });
        }

        handleConnect() {
            this.isConnected = true;
            this.reconnectAttempts = 0;
            console.log('WebSocket connected');
            
            // Обрабатываем события, накопленные пока не было соединения
            this.processPendingEvents();
            
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert('success', 'Connected', 'Real-time updates enabled');
            }
        }

        handleDisconnect(reason) {
            this.isConnected = false;
            console.log('WebSocket disconnected:', reason);
            
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert('warning', 'Disconnected', 'Real-time updates paused');
            }
        }

        handleError(error) {
            console.error('WebSocket connection error:', error);
            this.reconnectAttempts++;
    
            if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
                if (window.App.Alerts?.showError) {
                    window.App.Alerts.showError('Connection Error', 'Real-time updates disabled. Page will refresh.');
                }
                setTimeout(() => location.reload(), 5000);
            }
        }

        handlePlaybackUpdate(data) {
            if (window.App.Helpers?.setCachedData) {
                window.App.Helpers.setCachedData('playback_state', data);
            }
            document.dispatchEvent(new CustomEvent('playback-state-changed', { detail: data }));
        }

        handlePlaylistUpdate(data) {
            if (window.App.Helpers?.setCachedData) {
                window.App.Helpers.setCachedData('playlist_update', data);
            }
            
            document.dispatchEvent(new CustomEvent('playlist-updated', { detail: data }));
            
            if (data.action === 'delete') {
                const element = document.querySelector(`.playlist-item[data-id="${data.playlist_id}"]`);
                if (element) element.remove();
            }
        }

        handleSystemNotification(data) {
            if (window.App.Alerts?.showAlert) {
                showAlert(data.level || 'info', data.title || 'Notification', data.message);
            }
        }

        emit(event, data) {
            return new Promise((resolve, reject) => {
                if (!this.isConnected) {
                    this.pendingEvents.push({ event, data, resolve, reject });
                    return;
                }

                this.socket.emit(event, data, (response) => {
                    if (response?.error) {
                        reject(response.error);
                    } else {
                        resolve(response);
                    }
                });
            });
        }

        processPendingEvents() {
            while (this.pendingEvents.length > 0) {
                const { event, data, resolve, reject } = this.pendingEvents.shift();
                this.emit(event, data).then(resolve).catch(reject);
            }
        }

        disconnect() {
            if (this.socket) {
                this.socket.disconnect();
                this.isConnected = false;
            }
        }

        handleRetry() {
            if (this.reconnectAttempts < CONFIG.MAX_RETRIES) {
                console.log(`Retrying socket connection (attempt ${this.reconnectAttempts + 1})...`);
                setTimeout(() => this.init(), CONFIG.RETRY_DELAY);
                this.reconnectAttempts++;
            } else {
                console.error('Max socket initialization attempts reached');
            }
        }
    }

    // Инициализация после готовности App и DOM
    function initialize() {
        if (!window.App) {
            console.warn('App not initialized, waiting...');
            setTimeout(initialize, 100);
            return;
        }

        window.App.Sockets = new SocketManager();
    }

    // Запуск инициализации
    if (document.readyState === 'complete') {
        initialize();
    } else {
        document.addEventListener('DOMContentLoaded', initialize);
    }
})();
