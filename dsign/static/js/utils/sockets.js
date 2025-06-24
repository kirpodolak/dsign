/**
 * WebSocket Manager Module
 * @module SocketManager
 * @description Enhanced WebSocket manager with robust JWT auth handling
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
    SOCKET_TOKEN_EXPIRY_BUFFER: 300000 // 5 minutes buffer for token refresh
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
        
        // Dependencies
        this.authService = options.authService || this._createDefaultAuthService();
        this.logger = options.logger || this._createDefaultLogger();
        
        // Callbacks
        this.onError = options.onError || this.defaultErrorHandler;
        this.onTokenRefresh = options.onTokenRefresh || this.defaultTokenRefreshHandler;
        this.onReconnect = options.onReconnect || null;

        // Initialize auth socket
        this._initAuthSocket();

        // Bind methods
        this._handleRetry = this._handleRetry.bind(this);
        this.connect = this.connect.bind(this);
        this._init = this._init.bind(this);
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
        try {
            if (this.isConnected) {
                this.logger.debug('[Socket] Already connected');
                return;
            }

            // Get JWT socket token from server
            let token;
            try {
                const result = await this.authService.getSocketToken();
                token = result.token;
            } catch (error) {
                this.logger.error('[Socket] Failed to get socket token:', error);
                
                // Check for HTML response
                if (error.message.includes('Server returned HTML')) {
                    this._showAlert(
                        'error',
                        'Server Error',
                        'Authentication service is unavailable. Please try again later.'
                    );
                    return;
                }
                
                // Add delay before retry
                await new Promise(resolve => setTimeout(resolve, 2000));
                throw error;
            }

            this.token = token;
            this._init(token);
            
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

    _setupAuthSocket() {
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
                timeout: CONFIG.CONNECTION_TIMEOUT
            });

            this._setupAuthSocketEventHandlers();
        } catch (error) {
            this.logger.error('[AuthSocket] Initialization error:', error);
            this._scheduleAuthSocketRetry();
        }
    }

    _setupAuthSocketEventHandlers() {
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
    }

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
                auth: (cb) => {
                    cb({ token: this._getFormattedToken() });
                },
                timeout: CONFIG.CONNECTION_TIMEOUT,
                pingTimeout: 5000,
                pingInterval: CONFIG.PING_INTERVAL,
                upgrade: false,
                rememberUpgrade: false,
                rejectUnauthorized: false
            });
            
            this._setupEventHandlers();
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
        this.socket.on('connect', () => this._handleConnect());
        this.socket.on('disconnect', (reason) => this._handleDisconnect(reason));
        this.socket.on('connect_error', (error) => this._handleConnectError(error));
        this.socket.on('error', (error) => this._handleSocketError(error));
        this.socket.on('authenticated', () => this._handleAuthenticated());
        this.socket.on('unauthorized', (error) => this._handleUnauthorized(error));
        this.socket.on('token_expired', () => this._handleTokenExpired());
        this.socket.on('token_refresh', (newToken) => this._handleTokenRefresh(newToken));
        this.socket.onAny((event, ...args) => this._handleCustomEvent(event, ...args));
    }

    _handleConnect() {
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

    _handleDisconnect(reason) {
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

    _handleConnectError(error) {
        if (error.message === 'Authentication error') {
            this._handleAuthFailure(error);
        }
        this.logger.error('[Socket] Connection error:', error);
        this.isConnected = false;
        this._handleError(error);
    }

    _handleSocketError(error) {
        this.logger.error('[Socket] Error:', error);
        this._handleError(error);
    }

    _handleAuthenticated() {
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

    _handleUnauthorized(error) {
        this.logger.error('[Socket] Unauthorized:', error);
        this.isAuthenticated = false;
        this._initAuthSocket();
        this._handleError(new Error('Authentication failed'));
    }

    async _handleTokenExpired() {
        this.logger.debug('[Socket] Token expired, reconnecting...');
        try {
            await this.connect();
        } catch (err) {
            this.logger.error('Reconnect failed:', err);
        }
    }

    _handleTokenRefresh(newToken) {
        this.logger.debug('[Socket] Received token refresh');
        this.onTokenRefresh(newToken);
    }

    _handleCustomEvent(event, ...args) {
        const handlers = this.eventHandlers.get(event);
        if (handlers) {
            handlers.forEach(handler => handler(...args));
        }
    }

    _handleRetry(error) {
        this.logger.error('[Socket] Handling retry for error:', error);
        this.reconnectAttempts++;
        
        this.reconnectDelay = Math.min(
            Math.max(this.reconnectDelay * 2, CONFIG.INITIAL_RETRY_DELAY) + Math.random() * 2000,
            CONFIG.MAX_RETRY_DELAY
        );
        
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this._handleMaxRetriesReached();
        } else {
            this.logger.log(`[Socket] Will retry in ${Math.round(this.reconnectDelay/1000)} sec...`);
            setTimeout(() => {
                this.connect();
            }, this.reconnectDelay);
        }
    }

    _handleError = (error) => {
        this.logger.error('[Socket] Connection error:', error);
        this._updateReconnectState();
        
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this._handleMaxRetriesReached();
        } else {
            this._scheduleReconnect();
        }
    }

    _updateReconnectState() {
        this.reconnectAttempts++;
        this.reconnectDelay = Math.min(
            Math.max(this.reconnectDelay * 2, CONFIG.INITIAL_RETRY_DELAY) + Math.random() * 2000,
            CONFIG.MAX_RETRY_DELAY
        );
    }

    _handleMaxRetriesReached() {
        this.logger.error('[Socket] Max retry attempts reached');
        this._showConnectionErrorAlert();
        this._resetRetryAfterDelay();
    }

    _showConnectionErrorAlert() {
        if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
            window.App.Alerts.showError(
                'Connection Error', 
                'Real-time updates disabled. Please check your network connection.'
            );
        }
    }

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
            this.authRetryTimer
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
            socketManagerInstance = new SocketManager({
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
            });
        } catch (error) {
            console.error('Failed to initialize SocketManager:', error);
            throw error;
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
            off: () => console.warn('SocketManager not initialized')
        };
    }
}
