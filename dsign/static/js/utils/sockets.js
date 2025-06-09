/**
 * WebSocket Manager Module
 * @module SocketManager
 * @description Enhanced WebSocket manager with robust auth handling
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
        // Connection state
		this.endpoint = options.endpoint || 
                       window.location.origin.replace(/^http/, 'ws') || 
                       '/socket.io';
        this.socket = null;
        this.authSocket = null;
        this.isConnected = false;
        this.isAuthenticated = false;
        
        // Reconnection settings
        this.reconnectAttempts = 0;
        this.reconnectDelay = options.reconnectDelay || CONFIG.INITIAL_RETRY_DELAY;
        this.maxReconnectAttempts = CONFIG.MAX_RETRIES;
        
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
        this.authService = options.authService || {
            getSocketToken: async () => {
                try {
                    const response = await fetch('/api/auth/socket-token', {
                        headers: {
                            'Authorization': `Bearer ${this.getToken()}`
                        }
                    });
                    if (!response.ok) throw new Error('Failed to get socket token');
                    return await response.json();
                } catch (error) {
                    console.error('Socket token fetch failed:', error);
                    throw error;
                }
            },
            waitForToken: async () => {
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
                try {
                    if (typeof window !== 'undefined' && window.App?.Auth?.checkAuth) {
                        return await window.App.Auth.checkAuth();
                    }
                    return false;
                } catch (error) {
                    console.error('Auth check failed:', error);
                    return false;
                }
            }
        };
        
        this.logger = options.logger || {
            debug: console.debug.bind(console),
            log: console.log.bind(console),
            warn: console.warn.bind(console),
            error: console.error.bind(console)
        };
        
        // Callbacks
        this.onError = options.onError || ((error) => this.defaultErrorHandler(error));
        this.onTokenRefresh = options.onTokenRefresh || ((token) => this.defaultTokenRefreshHandler(token));
        this.onReconnect = options.onReconnect || null;

        // Initialize auth socket first
        this._initAuthSocket();
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

    _checkServerAvailability = async () => {
        try {
            const response = await fetch('/api/settings/current', {
                method: 'GET',
                signal: AbortSignal.timeout(CONFIG.SERVER_CHECK_TIMEOUT)
            });
            return response.ok;
        } catch (error) {
            this.logger.debug('[Socket] Server check failed:', error);
            return false;
        }
    }

    _initAuthSocket = () => {
        if (this.authRetryTimer) {
            clearTimeout(this.authRetryTimer);
            this.authRetryTimer = null;
        }

        if (typeof io === 'undefined') {
            this.logger.error('[Socket] Socket.IO library not loaded');
            this.authRetryTimer = setTimeout(() => this._initAuthSocket(), CONFIG.AUTH_RETRY_DELAY);
            return;
        }

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

            this.authSocket.on('connect', () => {
                this.logger.debug('[AuthSocket] Connected');
                // Wait for server ready signal before auth check
            });

            this.authSocket.on('auth_ready', () => {
                this.logger.debug('[AuthSocket] Server ready');
                this._checkAuthViaWebSocket();
            });

            this.authSocket.on('disconnect', (reason) => {
                this.logger.log('[AuthSocket] Disconnected:', reason);
                if (reason === 'io server disconnect') {
                    this.authRetryTimer = setTimeout(() => this._initAuthSocket(), CONFIG.AUTH_RETRY_DELAY);
                }
            });

            this.authSocket.on('connect_error', (error) => {
                this.logger.error('[AuthSocket] Connection error:', error);
                this.authRetryTimer = setTimeout(() => this._initAuthSocket(), CONFIG.AUTH_RETRY_DELAY);
            });

            this.authSocket.on('auth_status_response', (data) => {
                this.logger.debug('[AuthSocket] Received auth status:', data);
                this.authService.updateAuthStatus(data?.authenticated ?? false);
                if (data?.authenticated) {
                    this._initWithTokenCheck();
                } else {
                    this._handleAuthFailure('Not authenticated');
                }
            });

            this.authSocket.on('authentication_result', (data) => {
                if (data.success) {
                    this.logger.debug('[AuthSocket] Authentication successful');
                    this._initWithTokenCheck();
                } else {
                    this._handleAuthFailure(data.error || 'Authentication failed');
                }
            });

            this.authSocket.on('auth_error', (error) => {
                this._handleAuthFailure(error.message || 'Authentication error');
            });

        } catch (error) {
            this.logger.error('[AuthSocket] Initialization error:', error);
            this.authRetryTimer = setTimeout(() => this._initAuthSocket(), CONFIG.AUTH_RETRY_DELAY);
        }
    }

    _handleAuthFailure = (error) => {
        this.logger.error('[AuthSocket] Authentication failed:', error);
        this.authService.updateAuthStatus(false);
        
        if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
            window.App.Alerts.showError('Authentication Error', error);
        }
        
        // Clean up and retry
        if (this.authSocket) {
            this.authSocket.disconnect();
        }
        this.authRetryTimer = setTimeout(() => this._initAuthSocket(), CONFIG.AUTH_RETRY_DELAY);
    }

    _initWithTokenCheck = async () => {
        try {
            if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                throw new Error('Max reconnect attempts reached');
            }

            this.logger.debug('[Socket] Starting connection with token check');

            // Check server availability first
            if (!(await this._checkServerAvailability())) {
                this.logger.debug('[Socket] Server not available, delaying connection attempt');
                await new Promise(resolve => setTimeout(resolve, 2000));
                return this._handleRetry(new Error('Server not available'));
            }

            // Get socket token specifically
            let socketToken;
            try {
                const tokenData = await this.authService.getSocketToken();
                if (!tokenData?.token) {
                    throw new Error('Invalid socket token response');
                }
                socketToken = tokenData.token;
                
                // Schedule token refresh before expiration
                if (tokenData.expiresIn) {
                    const refreshTime = (tokenData.expiresIn * 1000) - CONFIG.SOCKET_TOKEN_EXPIRY_BUFFER;
                    if (refreshTime > 0) {
                        this.tokenRefreshTimer = setTimeout(() => {
                            this.logger.debug('[Socket] Refreshing socket token before expiration');
                            this._initWithTokenCheck();
                        }, refreshTime);
                    }
                }
            } catch (error) {
                this.logger.error('[Socket] Failed to get socket token:', error);
                throw new Error('Failed to get socket token');
            }

            // Verify authentication status
            if (!(await this.authService.checkAuth())) {
                throw new Error('User not authenticated');
            }

            this._init(socketToken);
        } catch (error) {
            this.logger.error('[Socket] Initialization error:', error);
            this._handleRetry(error);
        }
    }

    _init = (token) => {
        try {
            this.logger.debug('[Socket] Initializing connection...');
            
            if (typeof io === 'undefined') {
                throw new Error('Socket.IO library not loaded');
            }

            this.cleanup();

            this.socket = io({
                reconnection: true,
                reconnectionAttempts: 5,
                reconnectionDelay: 1000,
                transports: ['websocket'],
                query: { token: options.token },
                // Отключаем проверку origin
                allowUpgrades: true,
                rejectUnauthorized: false
            });

            this._setupEventHandlers();
        } catch (error) {
            this.logger.error('[Socket] Initialization error:', error);
            this._handleRetry(error);
        }
    }

    _setupEventHandlers = () => {
        this.socket.on('connect', () => {
            this.logger.debug('[Socket] Connected');
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            
            this._processPendingEvents();
            this._startPingInterval();
            
            if (this.onReconnect) {
                this.onReconnect(this.reconnectAttempts);
            }
        });

        this.socket.on('disconnect', (reason) => {
            this.logger.log('[Socket] Disconnected:', reason);
            this.isConnected = false;
            this._handleDisconnect(reason);
        });

        this.socket.on('connect_error', (error) => {
            this.logger.error('[Socket] Connection error:', error);
            this.isConnected = false;
            this._handleError(error);
        });

        this.socket.on('error', (error) => {
            this.logger.error('[Socket] Error:', error);
            this._handleError(error);
        });

        this.socket.on('authenticated', () => {
            this.logger.debug('[Socket] Authenticated');
            this.isAuthenticated = true;
            if (this.authTimeout) {
                clearTimeout(this.authTimeout);
                this.authTimeout = null;
            }
        });

        this.socket.on('unauthorized', (error) => {
            this.logger.error('[Socket] Unauthorized:', error);
            this.isAuthenticated = false;
            this._initAuthSocket();
            this._handleError(new Error('Authentication failed'));
        });

        this.socket.on('token_refresh', (newToken) => {
            this.logger.debug('[Socket] Received token refresh');
            this.onTokenRefresh(newToken);
        });

        this.socket.onAny((event, ...args) => {
            const handlers = this.eventHandlers.get(event);
            if (handlers) {
                handlers.forEach(handler => handler(...args));
            }
        });
    }

    _handleError = (error) => {
        this.logger.error('[Socket] Connection error:', error);
        this.reconnectAttempts++;
        
        this.reconnectDelay = Math.min(
            Math.max(this.reconnectDelay * 2, CONFIG.INITIAL_RETRY_DELAY) + Math.random() * 2000,
            CONFIG.MAX_RETRY_DELAY
        );
        
        if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
            this._showAlert(
                'error', 
                'Connection Error', 
                'Real-time updates disabled. Please check your network connection.'
            );
            setTimeout(() => {
                this.reconnectAttempts = 0;
                this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            }, 120000);
        } else {
            this.logger.log(`[Socket] Will retry in ${Math.round(this.reconnectDelay/1000)} sec...`);
            setTimeout(() => {
                this._initWithTokenCheck();
            }, this.reconnectDelay);
        }
    }

    _handleDisconnect = (reason) => {
        this.logger.log('[Socket] Disconnected:', reason);
        this.isConnected = false;
        
        if (reason === 'io server disconnect') {
            this.logger.warn('[Socket] Server forced disconnect, will attempt reconnection');
            setTimeout(() => this._initWithTokenCheck(), 5000);
        } else {
            this._handleRetry(new Error(reason));
        }
        
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    _handleRetry = (error) => {
        this.logger.error('[Socket] Connection error:', error);
        this.reconnectAttempts++;
        
        this.reconnectDelay = Math.min(
            Math.max(this.reconnectDelay * 2, CONFIG.INITIAL_RETRY_DELAY) + Math.random() * 2000,
            CONFIG.MAX_RETRY_DELAY
        );
        
        if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
            this.logger.error('[Socket] Max retry attempts reached');
            if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
                window.App.Alerts.showError(
                    'Connection Error', 
                    'Real-time updates disabled. Please check your network connection.'
                );
            }
            setTimeout(() => {
                this.reconnectAttempts = 0;
                this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            }, 120000);
        } else {
            this.logger.debug(`[Socket] Will retry in ${Math.round(this.reconnectDelay/1000)} sec...`);
            setTimeout(() => {
                this._initWithTokenCheck();
            }, this.reconnectDelay);
        }
    }

    _startPingInterval = () => {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
        }
        
        this.pingInterval = setInterval(() => {
            if (this.socket && this.isConnected) {
                this.socket.emit('ping', { timestamp: Date.now() });
            }
        }, CONFIG.PING_INTERVAL);
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
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
        if (this.authSocket) {
            this.authSocket.disconnect();
            this.authSocket = null;
        }
        
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
        
        if (this.authTimeout) {
            clearTimeout(this.authTimeout);
            this.authTimeout = null;
        }
        
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        if (this.tokenRefreshTimer) {
            clearTimeout(this.tokenRefreshTimer);
            this.tokenRefreshTimer = null;
        }
        
        if (this.serverCheckTimer) {
            clearTimeout(this.serverCheckTimer);
            this.serverCheckTimer = null;
        }
        
        if (this.authRetryTimer) {
            clearTimeout(this.authRetryTimer);
            this.authRetryTimer = null;
        }
        
        this.isConnected = false;
        this.isAuthenticated = false;
    }

    emit = (event, ...args) => {
        if (this.socket && this.isConnected) {
            this.socket.emit(event, ...args);
        } else {
            if (this.pendingEvents.length >= CONFIG.MAX_EVENT_QUEUE) {
                this.pendingEvents.shift();
            }
            this.pendingEvents.push({ name: event, args });
            this.logger.debug(`[Socket] Queued event (${event}), waiting for connection`);
        }
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
