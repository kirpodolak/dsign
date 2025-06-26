/**
 * WebSocket Manager Module
 * @module SocketManager
 * @description Enhanced WebSocket manager with robust JWT auth handling and Circuit Breaker pattern
 */

const CONFIG = {
    MAX_RETRIES: 5,
    INITIAL_RETRY_DELAY: 2000,
    MAX_RETRY_DELAY: 60000,
    PING_INTERVAL: 25000,
    AUTH_TIMEOUT: 30000,
    TOKEN_CHECK_INTERVAL: 1000,
    TOKEN_MAX_ATTEMPTS: 30,
    MAX_EVENT_QUEUE: 50,
    CONNECTION_TIMEOUT: 10000,
    SERVER_CHECK_INTERVAL: 5000,
    SERVER_CHECK_TIMEOUT: 2000,
    AUTH_RETRY_DELAY: 5000,
    SOCKET_TOKEN_EXPIRY_BUFFER: 300000, // 5 minutes buffer for token refresh
    CIRCUIT_BREAKER_THRESHOLD: 3,       // Max failures before opening circuit
    CIRCUIT_BREAKER_TIMEOUT: 30000      // Time to wait before half-open state
};

export class SocketManager {
    constructor(options = {}) {
        // Validate required dependencies
        if (typeof io === 'undefined') {
            throw new Error('Socket.IO library is required');
        }

        // Connection state
        this.endpoint = options.endpoint || 
                       window.location.origin.replace(/^http/, 'ws') || 
                       '/socket.io';
        this.socket = null;
        this.token = null;
        this.authSocket = null;
        this.isConnected = false;
        this.isAuthenticated = false;
        
        // Reconnection settings
        this.reconnectAttempts = 0;
        this.reconnectDelay = options.reconnectDelay || CONFIG.INITIAL_RETRY_DELAY;
        this.maxReconnectAttempts = options.maxRetries || CONFIG.MAX_RETRIES;
        
        // Circuit Breaker state
        this.circuitBreaker = {
            isOpen: false,
            failureCount: 0,
            lastFailureTime: null,
            threshold: options.circuitBreakerThreshold || CONFIG.CIRCUIT_BREAKER_THRESHOLD,
            timeout: options.circuitBreakerTimeout || CONFIG.CIRCUIT_BREAKER_TIMEOUT
        };
        
        // Event management
        this.pendingEvents = [];
        this.eventHandlers = new Map();
        
        // Timers
        this.pingInterval = null;
        this.authTimeout = null;
        this.connectionTimeout = null;
        this.tokenRefreshTimer = null;
        this.serverCheckTimer = null;
        this.authRetryTimer = null;
        this.circuitBreakerTimer = null;
        
        // Dependencies
        this.authService = options.authService || this._createDefaultAuthService();
        this.logger = options.logger || this._createDefaultLogger();
        
        // Callbacks
        this.onError = options.onError || this.defaultErrorHandler;
        this.onTokenRefresh = options.onTokenRefresh || this.defaultTokenRefreshHandler;
        this.onReconnect = options.onReconnect || null;
        this.onCircuitBreakerOpen = options.onCircuitBreakerOpen || null;
        this.onCircuitBreakerClose = options.onCircuitBreakerClose || null;
    }

    initAfterAuth = () => {
        if (!this.authService.getToken()) {
            this.logger.debug('[Socket] Skipping init - no auth token');
            return;
        }
        this._initAuthSocket();
    }

    _createDefaultAuthService() {
        return {
            getSocketToken: async () => {
                const response = await fetch('/api/auth/socket-token', {
                    headers: {
                        'Authorization': `Bearer ${this.getToken()}`
                    }
                });
                
                // Check for HTML response
                const contentType = response.headers.get('content-type');
                if (contentType && contentType.includes('text/html')) {
                    throw new Error('Server returned HTML instead of JSON');
                }
                
                if (!response.ok) throw new Error('Failed to get socket token');
                return await response.json();
            },
            getToken: () => {
                if (typeof window !== 'undefined' && window.App?.Auth?.getToken) {
                    return window.App.Auth.getToken();
                }
                return localStorage?.getItem('authToken');
            },
            updateAuthStatus: (status) => {
                if (typeof window !== 'undefined' && window.App?.Auth?.updateAuthStatus) {
                    window.App.Auth.updateAuthStatus(status);
                }
            },
            checkAuth: async () => {
                if (typeof window !== 'undefined' && window.App?.Auth?.checkAuth) {
                    return await window.App.Auth.checkAuth();
                }
                return false;
            }
        };
    }

    _createDefaultLogger() {
        return {
            debug: console.debug.bind(console),
            log: console.log.bind(console),
            warn: console.warn.bind(console),
            error: console.error.bind(console)
        };
    }

    defaultErrorHandler = (error) => {
        this.logger.error('[Socket] Error:', error);
        if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
            window.App.Alerts.showError('Connection Error', error.message);
        }
    }

    defaultTokenRefreshHandler = (newToken) => {
        this.logger.debug('[Socket] Token refreshed');
        if (typeof window !== 'undefined' && window.App?.Helpers?.setToken) {
            window.App.Helpers.setToken(newToken);
        } else if (typeof localStorage !== 'undefined') {
            localStorage.setItem('authToken', newToken);
        }
    }

    async connect() {
        // Don't attempt if circuit is open
        if (this.circuitBreaker.isOpen) {
            this.logger.debug('[Socket] Connection blocked: circuit is open');
            return;
        }

        try {
            if (this.isConnected) {
                this.logger.debug('[Socket] Already connected');
                return;
            }

            // Get JWT socket token from server
            let token;
            try {
                const result = await this.authService.getSocketToken();
                if (!result?.token) {
                    throw new Error('Invalid token response from server');
                }
                token = result.token;
                
                // Добавляем проверку формата токена
                if (typeof token !== 'string' || token.split('.').length !== 3) {
                    throw new Error('Invalid token format');
                }
            } catch (error) {
                this.logger.error('[Socket] Failed to get socket token:', error);
                
                if (error.message.includes('Server returned HTML')) {
                    this._showAlert(
                        'error',
                        'Server Error',
                        'Authentication service is unavailable. Please try again later.'
                    );
                    return;
                }
                
                // Добавляем задержку перед повторной попыткой
                await new Promise(resolve => setTimeout(resolve, 2000));
                
                // Проверяем, нужно ли открывать circuit breaker
                if (this.circuitBreaker.failureCount >= this.circuitBreaker.threshold - 1) {
                    this._openCircuit();
                }
                
                throw error;
            }

            this.token = token;
            await this._init(token);
            
        } catch (error) {
            this.logger.error('Socket connection error:', error);
            this._handleRetry(error);
            throw error;
        }
    }

    _initAuthSocket = () => {
        this._clearAuthSocketTimers();

        if (!this._isSocketIOLoaded()) {
            this.logger.error('[Socket] Socket.IO library not loaded');
            this._scheduleAuthSocketRetry();
            return;
        }

        this._setupAuthSocket();
    }

    _clearAuthSocketTimers() {
        if (this.authRetryTimer) {
            clearTimeout(this.authRetryTimer);
            this.authRetryTimer = null;
        }
    }

    _isSocketIOLoaded() {
        return typeof io !== 'undefined';
    }

    _scheduleAuthSocketRetry() {
        this.authRetryTimer = setTimeout(
            () => this._initAuthSocket(), 
            CONFIG.AUTH_RETRY_DELAY
        );
    }

    _setupAuthSocket = () => {
        if (this.authSocket) {
            this.authSocket.disconnect();
        }

        try {
            this.authSocket = io('/auth', {
                reconnection: true,
                reconnectionAttempts: CONFIG.MAX_RETRIES,
                reconnectionDelay: this.reconnectDelay,
                reconnectionDelayMax: 10000,
                randomizationFactor: 0.5,
                transports: ['websocket'],
                upgrade: false,
                forceNew: true,
                timeout: CONFIG.CONNECTION_TIMEOUT,
                query: {
                    'client_type': 'browser',
                    'version': '1.0'
                }
            });

            this._setupAuthSocketEventHandlers();
            
            this.authSocket.on('connect_error', (error) => {
                this.logger.error('[AuthSocket] Connection error:', error);
                this._scheduleAuthSocketRetry();
            });
            
        } catch (error) {
            this.logger.error('[AuthSocket] Initialization error:', error);
            this._scheduleAuthSocketRetry();
        }
    };

    _setupAuthSocketEventHandlers = () => {
        if (!this.authSocket) {
            this.logger.error('[AuthSocket] Cannot setup handlers - authSocket is null');
            return;
        }

        this.authSocket.on('connect', () => {
            this.logger.debug('[AuthSocket] Connected');
        });

        this.authSocket.on('auth_ready', () => {
            this.logger.debug('[AuthSocket] Server ready');
            this._checkAuthViaWebSocket();
        });

        this.authSocket.on('disconnect', (reason) => {
            this.logger.log('[AuthSocket] Disconnected:', reason);
            if (reason === 'io server disconnect') {
                this._scheduleAuthSocketRetry();
            }
        });

        this.authSocket.on('connect_error', (error) => {
            this.logger.error('[AuthSocket] Connection error:', error);
            this._scheduleAuthSocketRetry();
        });

        this.authSocket.on('auth_status_response', (data) => {
            this._handleAuthStatusResponse(data);
        });

        this.authSocket.on('authentication_result', (data) => {
            this._handleAuthenticationResult(data);
        });

        this.authSocket.on('auth_error', (error) => {
            this._handleAuthFailure(error.message || 'Authentication error');
        });
    };

    _handleAuthStatusResponse(data) {
        this.logger.debug('[AuthSocket] Received auth status:', data);
        this.authService.updateAuthStatus(data?.authenticated ?? false);
        if (data?.authenticated) {
            this.connect();
        } else {
            this._handleAuthFailure('Not authenticated');
        }
    }

    _handleAuthenticationResult(data) {
        if (data.success) {
            this.logger.debug('[AuthSocket] Authentication successful');
            this.connect();
        } else {
            this._handleAuthFailure(data.error || 'Authentication failed');
        }
    }

    _handleAuthFailure = (error) => {
        this.logger.error('[AuthSocket] Authentication failed:', error);
        this.authService.updateAuthStatus(false);
        
        if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
            window.App.Alerts.showError('Authentication Error', error);
        }
        
        if (this.authSocket) {
            this.authSocket.disconnect();
        }
        this._scheduleAuthSocketRetry();
    }

    _init = (token) => {
        try {
            this.logger.debug('[Socket] Initializing connection...');
            
            if (!token) {
                throw new Error('No token provided for WebSocket connection');
            }

            this.cleanup();
            
            this.socket = io({
                reconnection: true,
                reconnectionAttempts: this.maxReconnectAttempts,
                reconnectionDelay: this.reconnectDelay,
                transports: ['websocket'],
                auth: {
                    token: this._getFormattedToken()
                },
                query: {
                    'socket_token': this._getFormattedToken()
                },
                timeout: CONFIG.CONNECTION_TIMEOUT,
                pingTimeout: 5000,
                pingInterval: CONFIG.PING_INTERVAL,
                upgrade: false,
                rememberUpgrade: false,
                rejectUnauthorized: false,
                forceNew: true
            });
            
            this._setupEventHandlers();
            
            this.connectionTimeout = setTimeout(() => {
                if (!this.isConnected) {
                    this.logger.warn('[Socket] Connection timeout');
                    this._handleError(new Error('Connection timeout'));
                }
            }, CONFIG.CONNECTION_TIMEOUT);
            
        } catch (error) {
            this.logger.error('[Socket] Initialization error:', error);
            this._handleRetry(error);
        }
    }

    _getFormattedToken() {
        if (this.token && typeof this.token === 'object') {
            return this.token.token;
        }
        return String(this.token);
    }

    _setupEventHandlers = () => {
        if (!this.socket) {
            this.logger.error('[Socket] Cannot setup handlers - socket is null');
            return;
        }

        this.socket.on('connect', this._handleConnect);
        this.socket.on('disconnect', this._handleDisconnect);
        this.socket.on('connect_error', this._handleConnectError);
        this.socket.on('error', this._handleSocketError);
        this.socket.on('authenticated', this._handleAuthenticated);
        this.socket.on('unauthorized', this._handleUnauthorized);
        this.socket.on('token_expired', this._handleTokenExpired);
        this.socket.on('token_refresh', this._handleTokenRefresh);
        this.socket.onAny(this._handleCustomEvent);
    };

    _handleConnect = () => {
        this.circuitBreaker.failureCount = 0;
        
        this.logger.debug('[Socket] Connected');
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
        
        this._processPendingEvents();
        this._startPingInterval();
        
        if (this.onReconnect) {
            this.onReconnect(this.reconnectAttempts);
        }
    }

    _handleDisconnect = (reason) => {
        this.logger.log('[Socket] Disconnected:', reason);
        this.isConnected = false;
        this._handleDisconnectReason(reason);
        
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    _handleDisconnectReason(reason) {
        if (reason === 'io server disconnect') {
            this.logger.warn('[Socket] Server forced disconnect, will attempt reconnection');
            setTimeout(() => this.connect(), 5000);
        } else {
            this._handleRetry(new Error(reason));
        }
    }

    _handleConnectError = (error) => {
        this.logger.error('[Socket] Connection error:', error);
        
        if (error.message.includes('Authentication error')) {
            this._handleAuthFailure(error);
        } else if (error.message.includes('timeout')) {
            this._showAlert(
                'warning',
                'Connection Timeout',
                'Server is not responding. Trying to reconnect...'
            );
        } else if (error.message.includes('xhr poll error')) {
            this._showAlert(
                'error',
                'Network Error',
                'Connection problem detected. Check your network.'
            );
        }
        
        this.isConnected = false;
        this._handleError(error);
        
        if (this.socket) {
            this.socket.disconnect();
        }
    };

    _handleSocketError = (error) => {
        this.logger.error('[Socket] Error:', error);
        this._handleError(error);
    }

    _handleAuthenticated = () => {
        this.logger.debug('[Socket] Authenticated');
        this.isAuthenticated = true;
        this._clearAuthTimeout();
    }

    _clearAuthTimeout() {
        if (this.authTimeout) {
            clearTimeout(this.authTimeout);
            this.authTimeout = null;
        }
    }

    _handleUnauthorized = (error) => {
        this.logger.error('[Socket] Unauthorized:', error);
        this.isAuthenticated = false;
        this._initAuthSocket();
        this._handleError(new Error('Authentication failed'));
    }

    _handleTokenExpired = async () => {
        this.logger.debug('[Socket] Token expired, reconnecting...');
        try {
            await this.connect();
        } catch (err) {
            this.logger.error('Reconnect failed:', err);
        }
    }

    _handleTokenRefresh = (newToken) => {
        this.logger.debug('[Socket] Received token refresh');
        this.onTokenRefresh(newToken);
    }

    _handleCustomEvent = (event, ...args) => {
        const handlers = this.eventHandlers.get(event);
        if (handlers) {
            handlers.forEach(handler => handler(...args));
        }
    }

    _shouldOpenCircuit() {
        const { failureCount, threshold, lastFailureTime, timeout } = this.circuitBreaker;
        return failureCount >= threshold && Date.now() - lastFailureTime < timeout;
    }

    _openCircuit = () => {
        this.circuitBreaker.isOpen = true;
        this.circuitBreaker.lastFailureTime = Date.now();
        this.logger.error('[Circuit Breaker] Circuit opened due to repeated failures');
        
        if (this.onCircuitBreakerOpen) {
            this.onCircuitBreakerOpen();
        }

        this._showAlert(
            'error',
            'Connection Error',
            'Server is temporarily unavailable. Trying to reconnect...'
        );

        this.circuitBreakerTimer = setTimeout(() => {
            this._tryCloseCircuit();
        }, this.circuitBreaker.timeout);
    }

    _tryCloseCircuit = () => {
        this.circuitBreaker.isOpen = false;
        this.circuitBreaker.failureCount = 0;
        this.logger.log('[Circuit Breaker] Circuit reset to half-open state');
        
        if (this.onCircuitBreakerClose) {
            this.onCircuitBreakerClose();
        }

        this.connect().catch(err => {
            this.logger.error('[Circuit Breaker] Half-open test failed:', err);
            this._openCircuit();
        });
    }

    _handleRetry = (error) => {
        this.circuitBreaker.failureCount++;
        this.circuitBreaker.lastFailureTime = Date.now();

        if (this._shouldOpenCircuit() && !this.circuitBreaker.isOpen) {
            this._openCircuit();
            return;
        }

        if (this.circuitBreaker.isOpen) {
            this.logger.debug('[Socket] Connection blocked: circuit is open');
            return;
        }

        this.logger.error('[Socket] Handling retry for error:', error);
        this.reconnectAttempts++;
        
        const jitter = Math.random() * 2000;
        this.reconnectDelay = Math.min(
            Math.pow(2, this.reconnectAttempts) * 1000 + jitter,
            CONFIG.MAX_RETRY_DELAY
        );
        
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this._handleMaxRetriesReached();
        } else {
            this.logger.log(`[Socket] Will retry in ${Math.round(this.reconnectDelay/1000)} sec...`);
            setTimeout(() => {
                this.authService.checkAuth().then(authenticated => {
                    if (authenticated) {
                        this.connect();
                    } else {
                        this._handleAuthFailure('Authentication required');
                    }
                });
            }, this.reconnectDelay);
        }
    };

    _resetRetryAfterDelay() {
        setTimeout(() => {
            this.reconnectAttempts = 0;
            this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
        }, 120000);
    }

    _scheduleReconnect() {
        this.logger.log(`[Socket] Will retry in ${Math.round(this.reconnectDelay/1000)} sec...`);
        setTimeout(() => {
            this.connect();
        }, this.reconnectDelay);
    }

    _startPingInterval = () => {
        this._clearPingInterval();
        
        this.pingInterval = setInterval(() => {
            if (this.socket && this.isConnected) {
                this.socket.emit('ping', { timestamp: Date.now() });
            }
        }, CONFIG.PING_INTERVAL);
    }

    _clearPingInterval() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    _processPendingEvents = () => {
        while (this.pendingEvents.length > 0 && this.isConnected) {
            const event = this.pendingEvents.shift();
            this.emit(event.name, ...event.args);
        }
    }

    _checkAuthViaWebSocket = () => {
        if (this.authSocket && this.authSocket.connected) {
            this.authSocket.emit('request_auth_status');
        }
    }

    cleanup = () => {
        this._disconnectSockets();
        this._clearAllTimers();
        this._resetConnectionState();
    }

    _disconnectSockets() {
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
        if (this.authSocket) {
            this.authSocket.disconnect();
            this.authSocket = null;
        }
    }

    _clearAllTimers() {
        this._clearPingInterval();
        
        const timers = [
            this.authTimeout,
            this.connectionTimeout,
            this.tokenRefreshTimer,
            this.serverCheckTimer,
            this.authRetryTimer,
            this.circuitBreakerTimer
        ];
        
        timers.forEach(timer => {
            if (timer) {
                clearTimeout(timer);
            }
        });
    }

    _resetConnectionState() {
        this.isConnected = false;
        this.isAuthenticated = false;
    }

    emit = (event, ...args) => {
        if (this.socket && this.isConnected) {
            this.socket.emit(event, ...args);
        } else {
            this._queueEvent(event, args);
        }
    }

    _queueEvent(event, args) {
        if (this.pendingEvents.length >= CONFIG.MAX_EVENT_QUEUE) {
            this.pendingEvents.shift();
        }
        this.pendingEvents.push({ name: event, args });
        this.logger.debug(`[Socket] Queued event (${event}), waiting for connection`);
    }

    on = (event, handler) => {
        if (!this.eventHandlers.has(event)) {
            this.eventHandlers.set(event, new Set());
        }
        this.eventHandlers.get(event).add(handler);
        
        if (this.socket && this.isConnected) {
            this.socket.on(event, handler);
        }
    }

    off = (event, handler) => {
        if (this.eventHandlers.has(event)) {
            const handlers = this.eventHandlers.get(event);
            handlers.delete(handler);
            
            if (handlers.size === 0) {
                this.eventHandlers.delete(event);
            }
        }
        
        if (this.socket && this.isConnected) {
            this.socket.off(event, handler);
        }
    }

    disconnect = () => {
        this.cleanup();
    }

    _showAlert = (type, title, message) => {
        this.logger.log(`[Socket] Alert: ${title} - ${message}`);
        if (typeof window !== 'undefined' && window.App?.Alerts?.showAlert) {
            window.App.Alerts.showAlert(type, title, message);
        }
    }
}

let socketManagerInstance = null;

export function initializeSocketManager(options = {}) {
    if (!socketManagerInstance) {
        try {
            const defaultOptions = {
                onError: (error) => {
                    console.error('[Socket] Global error handler:', error);
                    if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
                        window.App.Alerts.showError('Socket Error', error.message);
                    }
                },
                onTokenRefresh: (newToken) => {
                    console.debug('[Socket] Updating token from refresh');
                    if (typeof window !== 'undefined' && window.App?.Helpers?.setToken) {
                        window.App.Helpers.setToken(newToken);
                    }
                },
                onReconnect: (attempt) => {
                    console.debug(`[Socket] Reconnect attempt ${attempt}`);
                },
                ...options
            };

            socketManagerInstance = new SocketManager(defaultOptions);
        } catch (error) {
            console.error('Failed to initialize SocketManager:', error);
            
            return {
                emit: () => console.warn('SocketManager not initialized'),
                disconnect: () => {},
                on: () => console.warn('SocketManager not initialized'),
                off: () => console.warn('SocketManager not initialized'),
                connect: () => console.warn('SocketManager not initialized'),
                isConnected: () => false
            };
        }
    }
    return socketManagerInstance;
}

if (typeof window !== 'undefined') {
    window.App = window.App || {};
    try {
        window.App.Sockets = window.App.Sockets || initializeSocketManager();
    } catch (error) {
        console.error('Failed to initialize global App.Sockets:', error);
        window.App.Sockets = {
            emit: () => console.warn('SocketManager not initialized'),
            disconnect: () => {},
            on: () => console.warn('SocketManager not initialized'),
            off: () => console.warn('SocketManager not initialized'),
            connect: () => console.warn('SocketManager not initialized'),
            isConnected: () => false
        };
    }
}
