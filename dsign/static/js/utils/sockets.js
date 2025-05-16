(function() {
    // Конфигурация
    const CONFIG = {
        MAX_RETRIES: 5,
        INITIAL_RETRY_DELAY: 1000,
        MAX_RETRY_DELAY: 30000, // 30 секунд максимальная задержка
        PING_INTERVAL: 25000, // 25 секунд между ping-запросами
        AUTH_TIMEOUT: 30000 // 30 секунд на аутентификацию
    };

    // Основной класс для управления сокетами
    class SocketManager {
        constructor() {
            this.socket = null;
            this.isConnected = false;
            this.isAuthenticated = false;
            this.reconnectAttempts = 0;
            this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            this.pendingEvents = [];
            this.pingInterval = null;
            this.authTimeout = null;
            this.init();
        }

        init() {
            try {
                console.debug('[Socket] Initializing connection...');
                
                // Проверяем доступность Socket.IO
                if (typeof io === 'undefined') {
                    throw new Error('Socket.IO library not loaded');
                }

                // Очищаем предыдущее соединение
                this.cleanup();

                this.socket = io({
                    reconnection: true,
                    reconnectionAttempts: CONFIG.MAX_RETRIES,
                    reconnectionDelay: this.reconnectDelay,
                    transports: ['websocket', 'polling'],
                    auth: (cb) => {
                        // Автоматическая отправка токена при подключении
                        const token = window.App.Helpers?.getToken();
                        cb({ token });
                    }
                });

                this.setupEventHandlers();
            } catch (error) {
                console.error('[Socket] Initialization error:', error);
                this.handleRetry(error);
            }
        }

        setupEventHandlers() {
            // Базовые обработчики соединения
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
            this.socket.on('authentication_result', (data) => {
                this.handleAuthenticationResult(data);
            });

            this.socket.on('playback_update', (data) => {
                this.handlePlaybackUpdate(data);
            });

            this.socket.on('playlist_update', (data) => {
                this.handlePlaylistUpdate(data);
            });

            this.socket.on('system_notification', (data) => {
                this.handleSystemNotification(data);
            });

            this.socket.on('inactivity_timeout', (data) => {
                this.handleInactivityTimeout(data);
            });

            this.socket.on('auth_timeout', (data) => {
                this.handleAuthTimeout(data);
            });

            this.socket.on('pong', (latency) => {
                console.debug(`[Socket] Ping latency: ${latency}ms`);
            });
        }

        handleConnect() {
            console.debug('[Socket] Connection established');
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            
            // Запускаем ping-интервал
            this.startPingInterval();
            
            // Устанавливаем таймаут аутентификации
            this.authTimeout = setTimeout(() => {
                if (!this.isAuthenticated) {
                    console.warn('[Socket] Authentication timeout');
                    this.socket.emit('auth_timeout');
                }
            }, CONFIG.AUTH_TIMEOUT);
            
            // Обрабатываем события, накопленные пока не было соединения
            this.processPendingEvents();
            
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert('success', 'Connected', 'Real-time updates enabled');
            }
        }

        handleAuthenticationResult(data) {
            clearTimeout(this.authTimeout);
            
            if (data.success) {
                this.isAuthenticated = true;
                console.debug('[Socket] Authentication successful');
            } else {
                this.isAuthenticated = false;
                console.error('[Socket] Authentication failed:', data.error);
                if (window.App.Alerts?.showError) {
                    window.App.Alerts.showError('Authentication Failed', data.error);
                }
                this.disconnect();
            }
        }

        handleDisconnect(reason) {
            console.log('[Socket] Disconnected:', reason);
            this.isConnected = false;
            this.isAuthenticated = false;
            this.cleanupTimers();
            
            // Не показываем уведомление при преднамеренном отключении
            if (reason !== 'io client disconnect' && window.App.Alerts?.showAlert) {
                const message = reason === 'io server disconnect' 
                    ? 'Disconnected by server' 
                    : 'Real-time updates paused';
                
                window.App.Alerts.showAlert('warning', 'Disconnected', message);
            }
        }

        handleError(error) {
            console.error('[Socket] Connection error:', error);
            this.reconnectAttempts++;
            
            // Экспоненциальная задержка
            this.reconnectDelay = Math.min(
                this.reconnectDelay * 2,
                CONFIG.MAX_RETRY_DELAY
            );
            
            if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
                if (window.App.Alerts?.showError) {
                    window.App.Alerts.showError(
                        'Connection Error', 
                        'Real-time updates disabled. Page will refresh.'
                    );
                }
                setTimeout(() => location.reload(), 5000);
            } else {
                console.log(`[Socket] Retrying in ${this.reconnectDelay/1000} sec...`);
                setTimeout(() => this.init(), this.reconnectDelay);
            }
        }

        handleInactivityTimeout(data) {
            console.warn('[Socket] Disconnected due to inactivity');
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert('warning', 'Session Expired', data.message);
            }
            this.disconnect();
        }

        handleAuthTimeout(data) {
            console.warn('[Socket] Authentication timeout');
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert('error', 'Authentication Timeout', 'Please refresh the page');
            }
            this.disconnect();
        }

        startPingInterval() {
            this.cleanupTimers();
            this.pingInterval = setInterval(() => {
                if (this.isConnected) {
                    const start = Date.now();
                    this.socket.emit('ping', {}, () => {
                        const latency = Date.now() - start;
                        this.socket.emit('pong', latency);
                    });
                }
            }, CONFIG.PING_INTERVAL);
        }

        cleanupTimers() {
            if (this.pingInterval) {
                clearInterval(this.pingInterval);
                this.pingInterval = null;
            }
            if (this.authTimeout) {
                clearTimeout(this.authTimeout);
                this.authTimeout = null;
            }
        }

        handlePlaybackUpdate(data) {
            console.debug('[Socket] Playback update:', data);
            if (window.App.Helpers?.setCachedData) {
                window.App.Helpers.setCachedData('playback_state', data);
            }
            document.dispatchEvent(new CustomEvent('playback-state-changed', { detail: data }));
        }

        handlePlaylistUpdate(data) {
            console.debug('[Socket] Playlist update:', data);
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
            console.debug('[Socket] System notification:', data);
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert(
                    data.level || 'info', 
                    data.title || 'Notification', 
                    data.message
                );
            }
        }

        emit(event, data) {
            return new Promise((resolve, reject) => {
                if (!this.isConnected || !this.isAuthenticated) {
                    console.debug(`[Socket] Queueing event (${event}) while offline`);
                    this.pendingEvents.push({ event, data, resolve, reject });
                    return;
                }

                console.debug(`[Socket] Emitting event: ${event}`, data);
                this.socket.emit(event, data, (response) => {
                    if (response?.error) {
                        console.error(`[Socket] Event ${event} failed:`, response.error);
                        reject(response.error);
                    } else {
                        console.debug(`[Socket] Event ${event} successful`, response);
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

        cleanup() {
            console.debug('[Socket] Cleaning up resources');
            this.cleanupTimers();
            
            if (this.socket) {
                this.socket.off(); // Удаляем все обработчики событий
                this.socket.disconnect();
                this.socket = null;
            }
            
            this.isConnected = false;
            this.isAuthenticated = false;
        }

        disconnect() {
            console.debug('[Socket] Disconnecting...');
            this.cleanup();
        }

        handleRetry(error) {
            if (this.reconnectAttempts < CONFIG.MAX_RETRIES) {
                console.log(`[Socket] Retrying connection (attempt ${this.reconnectAttempts + 1})...`);
                setTimeout(() => this.init(), this.reconnectDelay);
                this.reconnectAttempts++;
            } else {
                console.error('[Socket] Max initialization attempts reached', error);
                if (window.App.Alerts?.showError) {
                    window.App.Alerts.showError(
                        'Connection Failed', 
                        'Cannot establish connection. Please refresh the page.'
                    );
                }
            }
        }
    }

    // Инициализация после готовности App и DOM
    function initialize() {
        if (!window.App) {
            console.warn('[Socket] App not initialized, waiting...');
            setTimeout(initialize, 100);
            return;
        }

        console.debug('[Socket] Initializing socket manager...');
        window.App.Sockets = new SocketManager();
        
        // Глобальный обработчик ошибок
        window.addEventListener('error', (event) => {
            if (window.App.Sockets?.isConnected) {
                window.App.Sockets.emit('client_error', {
                    message: event.message,
                    source: event.filename,
                    lineno: event.lineno,
                    colno: event.colno,
                    error: event.error?.stack
                });
            }
        });
    }

    // Запуск инициализации
    if (document.readyState === 'complete') {
        initialize();
    } else {
        document.addEventListener('DOMContentLoaded', initialize);
    }
})();
