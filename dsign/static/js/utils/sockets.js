(function() {
    // Проверяем доступность зависимостей
    function checkDependencies() {
        if (typeof io === 'undefined') {
            throw new Error('Socket.IO library not loaded');
        }
        if (!window.App) {
            throw new Error('App object not initialized');
        }
        return true;
    }

    // Пытаемся инициализировать сокеты с повторными попытками
    function initializeSocketManager(retryCount = 0) {
        try {
            checkDependencies();
            
            const { showAlert, showError } = window.App.Alerts || {};
            const { setCachedData } = window.App.Helpers || {};

            class SocketManager {
                constructor() {
                    this.socket = null;
                    this.isConnected = false;
                    this.reconnectAttempts = 0;
                    this.maxReconnectAttempts = 5;
                    this.connect();
                }

                connect() {
                    try {
                        if (this.isConnected) return;

                        this.socket = io({
                            reconnection: true,
                            reconnectionAttempts: this.maxReconnectAttempts,
                            reconnectionDelay: 1000,
                            transports: ['websocket', 'polling']
                        });

                        this.initEventHandlers();
                    } catch (error) {
                        console.error('Socket connection error:', error);
                        this.handleConnectionError(error);
                    }
                }

                initEventHandlers() {
                    this.socket.on('connect', () => {
                        this.isConnected = true;
                        this.reconnectAttempts = 0;
                        console.log('WebSocket connected');
                        if (showAlert) {
                            showAlert('success', 'Connected', 'Real-time updates enabled');
                        }
                    });

                    this.socket.on('disconnect', (reason) => {
                        this.isConnected = false;
                        console.log('WebSocket disconnected:', reason);
                        if (showAlert) {
                            showAlert('warning', 'Disconnected', 'Real-time updates paused');
                        }
                    });

                    this.socket.on('connect_error', (error) => {
                        console.error('WebSocket connection error:', error);
                        this.handleConnectionError(error);
                    });

                    // Custom event handlers
                    this.socket.on('playback_update', (data) => this.handlePlaybackUpdate(data));
                    this.socket.on('playlist_update', (data) => this.handlePlaylistUpdate(data));
                    this.socket.on('system_notification', (data) => this.handleSystemNotification(data));
                }

                handleConnectionError(error) {
                    this.reconnectAttempts++;
                    if (showError && this.reconnectAttempts >= this.maxReconnectAttempts) {
                        showError('Connection Error', 'Failed to establish real-time connection');
                    }
                }

                handlePlaybackUpdate(data) {
                    if (setCachedData) {
                        setCachedData('playback_state', data);
                    }
                    document.dispatchEvent(new CustomEvent('playback-state-changed', { detail: data }));
                }

                handlePlaylistUpdate(data) {
                    if (setCachedData) {
                        setCachedData('playlist_update', data);
                    }
                    document.dispatchEvent(new CustomEvent('playlist-updated', { detail: data }));
                    
                    if (data.action === 'delete') {
                        const element = document.querySelector(`.playlist-item[data-id="${data.playlist_id}"]`);
                        if (element) element.remove();
                    }
                }

                handleSystemNotification(data) {
                    if (showAlert) {
                        showAlert(data.level || 'info', data.title || 'Notification', data.message);
                    }
                }

                emit(event, data) {
                    return new Promise((resolve, reject) => {
                        if (!this.isConnected || !this.socket) {
                            reject(new Error('Socket not connected'));
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

                disconnect() {
                    if (this.socket) {
                        this.socket.disconnect();
                        this.isConnected = false;
                    }
                }
            }

            window.App = window.App || {};
            window.App.Sockets = new SocketManager();

        } catch (error) {
            console.error('Socket initialization error:', error);
            
            // Повторяем попытку через 1 секунду (максимум 5 попыток)
            if (retryCount < 5) {
                console.log(`Retrying socket connection (attempt ${retryCount + 1})...`);
                setTimeout(() => initializeSocketManager(retryCount + 1), 1000);
            } else {
                console.error('Max socket initialization attempts reached');
                if (window.App.Alerts?.showError) {
                    window.App.Alerts.showError('Connection Error', 'Failed to initialize real-time connection');
                }
            }
        }
    }

    // Запускаем инициализацию после загрузки DOM
    if (document.readyState === 'complete') {
        initializeSocketManager();
    } else {
        document.addEventListener('DOMContentLoaded', initializeSocketManager);
    }
})();