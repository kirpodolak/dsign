/**
 * WebSocket Manager Module
 * @module SocketManager
 * @description Enhanced WebSocket manager for real-time communication with authentication and event management
 */

// Configuration constants with enhanced security settings
const CONFIG = {
    MAX_RETRIES: 5,
    INITIAL_RETRY_DELAY: 1000,
    MAX_RETRY_DELAY: 30000,
    PING_INTERVAL: 25000,
    AUTH_TIMEOUT: 30000,
    TOKEN_CHECK_INTERVAL: 500,
    TOKEN_MAX_ATTEMPTS: 10,
    TOKEN_REFRESH_THRESHOLD: 300000, // 5 minutes before expiration
    MAX_EVENT_QUEUE: 50,
    CONNECTION_TIMEOUT: 10000,
    // Avoid hammering /auth/socket-token on reconnect storms.
    // Token is short-lived; keep cache conservative and per-tab.
    SOCKET_TOKEN_CACHE_MS: 30000
};

/**
 * WebSocket Manager Class
 */
export class SocketManager {
    /**
     * Create a new SocketManager instance
     * @param {object} [options={}] - Configuration options
     * @param {function} [options.onError] - Custom error handler
     * @param {function} [options.onTokenRefresh] - Custom token refresh handler
     * @param {function} [options.onReconnect] - Custom reconnect handler
     * @param {number} [options.reconnectDelay] - Custom initial reconnect delay
     */
    constructor(options = {}) {
        // Connection state
        this.socket = null;
        this.isConnected = false;
        this.isAuthenticated = false;
        this.connectionEstablished = false;
        
        // Reconnection settings
        this.reconnectAttempts = 0;
        this.reconnectDelay = options.reconnectDelay || CONFIG.INITIAL_RETRY_DELAY;
        
        // Event management
        this.pendingEvents = [];
        this.eventHandlers = new Map();
        
        // Timers
        this.pingInterval = null;
        this.authTimeout = null;
        this.connectionTimeout = null;
        this.tokenRefreshTimer = null;
        
        // Security
        this.lastActivity = Date.now();
        this.ipAddress = null;
        
        // Callbacks
        this.onError = options.onError || this.defaultErrorHandler;
        this.onTokenRefresh = options.onTokenRefresh || this.defaultTokenRefreshHandler;
        this.onReconnect = options.onReconnect || null;

        // Token cache (per JS context / tab).
        this._cachedSocketToken = null;
        this._cachedSocketTokenTs = 0;
        this._socketTokenPromise = null;

        // Prevent multiple overlapping init flows.
        this._initInFlight = false;
        
        // Initialize connection
        this.initWithTokenCheck();
    }

    /**
     * Subscribe to an application event coming from the socket layer.
     * Supported events include:
     * - connect, disconnect (socket lifecycle)
     * - playback_update, playlist_update, system_notification (application)
     * @param {string} event
     * @param {(data:any)=>void} handler
     */
    on(event, handler) {
        if (!event || typeof handler !== 'function') return;
        const set = this.eventHandlers.get(event) || new Set();
        set.add(handler);
        this.eventHandlers.set(event, set);
    }

    /**
     * Unsubscribe from an application event.
     * @param {string} event
     * @param {(data:any)=>void} handler
     */
    off(event, handler) {
        const set = this.eventHandlers.get(event);
        if (!set) return;
        set.delete(handler);
        if (set.size === 0) this.eventHandlers.delete(event);
    }

    _dispatch(event, data) {
        const set = this.eventHandlers.get(event);
        if (!set || set.size === 0) return;
        for (const fn of set) {
            try {
                fn(data);
            } catch (e) {
                console.warn(`[Socket] Handler for ${event} failed`, e);
            }
        }
    }

    /**
     * Default error handler
     * @param {Error} error - Error object
     */
    defaultErrorHandler(error) {
        console.error('[Socket] Error:', error);
        if (typeof window !== 'undefined' && window.App?.Alerts?.showError) {
            window.App.Alerts.showError('Connection Error', error.message);
        }
    }

    /**
     * Default token refresh handler
     * @param {string} newToken - New authentication token
     */
    defaultTokenRefreshHandler(newToken) {
        console.debug('[Socket] Token refreshed');
        // Do not persist tokens in JS-accessible storage.
        void newToken;
    }

    /**
     * Initialize connection with token check
     * @private
     */
    async initWithTokenCheck() {
        if (this._initInFlight) return;
        this._initInFlight = true;
        try {
            console.debug('[Socket] Starting connection with token check');
            
            // Wait for API service to be available
            await this.waitForAPI();

            // Session cookie auth is the source of truth; fetch a short-lived socket token.
            await this.getSocketToken();
            this.init();
        } catch (error) {
            console.error('[Socket] Initialization error:', error);
            this.handleRetry(error);
        } finally {
            this._initInFlight = false;
        }
    }

    /**
     * Get WebSocket authentication token
     * @private
     * @returns {Promise<string|null>} Authentication token or null
     */
    async getSocketToken() {
        const now = Date.now();
        if (this._cachedSocketToken && (now - this._cachedSocketTokenTs) < CONFIG.SOCKET_TOKEN_CACHE_MS) {
            return this._cachedSocketToken;
        }

        if (this._socketTokenPromise) {
            return this._socketTokenPromise;
        }

        this._socketTokenPromise = (async () => {
            try {
                const data = await window.App.API.fetch('/auth/socket-token', { credentials: 'include' });
                if (!data?.token) {
                    throw new Error('No token in response');
                }
                this._cachedSocketToken = data.token;
                this._cachedSocketTokenTs = Date.now();
                return data.token;
            } catch (error) {
                console.error('[Socket] Token fetch error:', error);
                // Don't keep a bad promise around.
                this._cachedSocketToken = null;
                this._cachedSocketTokenTs = 0;
                throw error;
            } finally {
                this._socketTokenPromise = null;
            }
        })();

        return this._socketTokenPromise;
    }
	
    async waitForAPI(maxAttempts = 5, delay = 500) {
        let attempts = 0;
        return new Promise((resolve, reject) => {
            const check = () => {
                attempts++;
                if (window.App?.API?.fetch) {
                    resolve();
                } else if (attempts >= maxAttempts) {
                    reject(new Error('API service not available'));
                } else {
                    setTimeout(check, delay);
                }
            };
            check();
        });
    }	

    /**
     * Validate token structure
     * @private
     * @param {string} token - JWT token
     * @returns {boolean} True if token is valid
     */
    validateTokenStructure(token) {
        try {
            const parts = token.split('.');
            if (parts.length !== 3) {
                console.warn('[Socket] Invalid token format');
                return false;
            }
            
            // Basic payload validation
            const payload = JSON.parse(atob(parts[1]));
            if (!payload.exp || !payload.sub) {
                console.warn('[Socket] Token missing required claims');
                return false;
            }
            
            // Check if token is about to expire
            const now = Date.now() / 1000;
            if (payload.exp - now < CONFIG.TOKEN_REFRESH_THRESHOLD / 1000) {
                console.debug('[Socket] Token needs refresh');
                this.scheduleTokenRefresh();
            }
            
            return true;
        } catch (e) {
            console.warn('[Socket] Token validation failed:', e);
            return false;
        }
    }

    /**
     * Schedule token refresh
     * @private
     */
    scheduleTokenRefresh() {
        if (this.tokenRefreshTimer) {
            clearTimeout(this.tokenRefreshTimer);
        }
        
        this.tokenRefreshTimer = setTimeout(() => {
            this.refreshToken();
        }, CONFIG.TOKEN_REFRESH_THRESHOLD - 60000); // 1 minute before expiration
    }

    /**
     * Refresh authentication token
     * @private
     * @returns {Promise<boolean>} True if refresh was successful
     */
    async refreshToken() {
        try {
            console.debug('[Socket] Refreshing token...');
            
            // Use AuthService if available
            if (typeof window !== 'undefined' && window.App?.Auth?.refreshToken) {
                const success = await window.App.Auth.refreshToken();
                return success;
            }
            
            // Fallback to direct API call
            const response = await fetch('/api/auth/refresh-token', {
                method: 'POST',
                credentials: 'include'
            });
            
            if (!response.ok) {
                throw new Error('Failed to refresh token');
            }
            
            const { token } = await response.json();
            if (this.onTokenRefresh) {
                this.onTokenRefresh(token);
            }
            
            return true;
        } catch (error) {
            console.error('[Socket] Token refresh failed:', error);
            throw error;
        }
    }

    /**
     * Initialize socket connection
     * @private
     */
    init() {
        try {
            console.debug('[Socket] Initializing connection...');
            
            if (typeof io === 'undefined') {
                throw new Error('Socket.IO library not loaded');
            }

            this.cleanup();

            this.socket = io({
                reconnection: true,
                reconnectionAttempts: CONFIG.MAX_RETRIES,
                reconnectionDelay: this.reconnectDelay,
                transports: ['websocket'],
                upgrade: false,
                timeout: CONFIG.CONNECTION_TIMEOUT,
                auth: (cb) => {
                    try {
                        this.getSocketToken().then(token => {
                            if (!token) {
                                throw new Error('No authentication token available');
                            }
                            
                            cb({ 
                                token,
                                userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : ''
                            });
                        }).catch(error => {
                            this.onError(new Error(`Authentication error: ${error.message}`));
                            cb({ error: error.message });
                        });
                    } catch (authError) {
                        this.onError(new Error(`Authentication error: ${authError.message}`));
                        cb({ error: authError.message });
                    }
                }
            });

            this.setupEventHandlers();
        } catch (error) {
            console.error('[Socket] Initialization error:', error);
            this.handleRetry(error);
        }
    }

    /**
     * Setup socket event handlers
     * @private
     */
    setupEventHandlers() {
        // Connection events
        this.socket.on('connect', () => this.handleConnect());
        this.socket.on('disconnect', (reason) => this.handleDisconnect(reason));
        this.socket.on('connect_error', (error) => this.handleError(error));
        
        // Authentication events
        this.socket.on('authentication_result', (data) => this.handleAuthenticationResult(data));
        this.socket.on('auth_error', (error) => this.handleAuthError(error));
        this.socket.on('token_refresh', (newToken) => this.handleTokenRefresh(newToken));
        
        // Application events
        this.socket.on('playback_update', (data) => this.handlePlaybackUpdate(data));
        this.socket.on('playlist_update', (data) => this.handlePlaylistUpdate(data));
        this.socket.on('system_notification', (data) => this.handleSystemNotification(data));
        
        // System events
        this.socket.on('inactivity_timeout', () => this.handleInactivityTimeout());
        this.socket.on('auth_timeout', () => this.handleAuthTimeout());
        this.socket.on('pong', (latency) => this.handlePong(latency));
        this.socket.on('reconnect_failed', () => this.handleReconnectFailed());
        this.socket.on('reconnect_attempt', (attempt) => this.handleReconnectAttempt(attempt));
    }

    handlePlaybackUpdate(data) {
        this.lastActivity = Date.now();
        this._dispatch('playback_update', data);
    }

    handlePlaylistUpdate(data) {
        this.lastActivity = Date.now();
        this._dispatch('playlist_update', data);
    }

    handleSystemNotification(data) {
        this.lastActivity = Date.now();
        this._dispatch('system_notification', data);
    }

    /**
     * Handle connection established
     * @private
     */
    handleConnect() {
        console.debug('[Socket] Connection established');
        this.connectionEstablished = true;
        clearTimeout(this.connectionTimeout);
        
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;

        // Let pages switch from polling to push-driven updates.
        this._dispatch('connect', { ts: Date.now() });
        
        this.startPingInterval();
        this.scheduleTokenRefresh();
        
        // Set authentication timeout
        this.authTimeout = setTimeout(() => {
            if (!this.isAuthenticated) {
                console.warn('[Socket] Authentication timeout');
                this.socket.emit('auth_timeout');
                this.onError(new Error('Authentication timeout'));
            }
        }, CONFIG.AUTH_TIMEOUT);
        
        this.processPendingEvents();
        
        // Track activity
        this.lastActivity = Date.now();
    }

    /**
     * Handle authentication result
     * @private
     * @param {object} data - Authentication result
     */
    handleAuthenticationResult(data) {
        clearTimeout(this.authTimeout);
        
        if (data.success) {
            this.isAuthenticated = true;
            console.debug('[Socket] Authentication successful');
        } else {
            this.isAuthenticated = false;
            console.error('[Socket] Authentication failed:', data.error);
            this.showAlert('error', 'Authentication Failed', data.error);
            this.disconnect();
        }
    }

    /**
     * Handle token refresh
     * @private
     * @param {string} newToken - New authentication token
     */
    handleTokenRefresh(newToken) {
        console.debug('[Socket] Received token refresh');
        if (this.onTokenRefresh) {
            this.onTokenRefresh(newToken);
        }
    }

    /**
     * Handle authentication error
     * @private
     * @param {Error} error - Error object
     */
    handleAuthError(error) {
        this.onError(new Error(`Authentication error: ${error.message}`));
        if (typeof window !== 'undefined' && window.App?.Base?.handleUnauthorized) {
            window.App.Base.handleUnauthorized();
        }
    }

    /**
     * Handle disconnection
     * @private
     * @param {string} reason - Disconnection reason
     */
    handleDisconnect(reason) {
        console.log('[Socket] Disconnected:', reason);
        this.isConnected = false;
        this.isAuthenticated = false;
        this.connectionEstablished = false;
        this.cleanupTimers();

        // Let pages fall back to polling when socket is down.
        this._dispatch('disconnect', { reason, ts: Date.now() });
        
        if (reason !== 'io client disconnect') {
            const message = reason === 'io server disconnect' 
                ? 'Disconnected by server' 
                : 'Connection lost - attempting to reconnect';
            this.showAlert('warning', 'Disconnected', message);
        }
    }

    /**
     * Handle connection error
     * @private
     * @param {Error} error - Error object
     */
    handleError(error) {
        console.error('[Socket] Connection error:', error);
        // IMPORTANT: Socket.IO already handles reconnection when `reconnection: true`.
        // Do NOT start a parallel reconnect loop here (it can create token-fetch storms
        // and elevated CPU usage on the server).
        this.reconnectAttempts++;
        if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
            this.showAlert('error', 'Connection Error', 'Real-time updates disabled. Please refresh the page.');
        }
    }

    /**
     * Handle inactivity timeout
     * @private
     */
    handleInactivityTimeout() {
        console.warn('[Socket] Disconnected due to inactivity');
        this.showAlert('warning', 'Session Expired', 'Your session has timed out due to inactivity');
        this.disconnect();
    }

    /**
     * Handle authentication timeout
     * @private
     */
    handleAuthTimeout() {
        console.warn('[Socket] Authentication timeout');
        this.showAlert('error', 'Authentication Timeout', 'Please refresh the page');
        this.disconnect();
    }

    /**
     * Emit event through socket
     * @param {string} event - Event name
     * @param {object} data - Event data
     * @returns {Promise} Promise that resolves with response or rejects with error
     */
    emit(event, data) {
        return new Promise((resolve, reject) => {
            if (!this.isConnected || !this.isAuthenticated) {
                console.debug(`[Socket] Queueing event (${event}) while offline`);
                
                // Prevent queue from growing too large
                if (this.pendingEvents.length >= CONFIG.MAX_EVENT_QUEUE) {
                    this.pendingEvents.shift();
                }
                
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

    /**
     * Process pending events
     * @private
     */
    processPendingEvents() {
        while (this.pendingEvents.length > 0) {
            const { event, data, resolve, reject } = this.pendingEvents.shift();
            this.emit(event, data).then(resolve).catch(reject);
        }
    }

    /**
     * Start ping interval
     * @private
     */
    startPingInterval() {
        this.cleanupTimers();
        this.pingInterval = setInterval(() => {
            if (this.isConnected) {
                const start = Date.now();
                // Use a dedicated heartbeat event for application-level liveness.
                // Avoid event name "ping" which may conflict with Socket.IO internals.
                this.socket.emit('heartbeat', { timestamp: start }, () => {
                    // Best-effort: do not spam server; this callback may not fire depending on server handler.
                });
            }
        }, CONFIG.PING_INTERVAL);
    }

    /**
     * Cleanup timers
     * @private
     */
    cleanupTimers() {
        clearInterval(this.pingInterval);
        clearTimeout(this.authTimeout);
        clearTimeout(this.connectionTimeout);
        clearTimeout(this.tokenRefreshTimer);
    }

    /**
     * Cleanup resources
     * @private
     */
    cleanup() {
        console.debug('[Socket] Cleaning up resources');
        this.cleanupTimers();
        
        if (this.socket) {
            this.socket.off();
            this.socket.disconnect();
            this.socket = null;
        }
        
        this.isConnected = false;
        this.isAuthenticated = false;
        this.connectionEstablished = false;
    }

    /**
     * Disconnect socket
     */
    disconnect() {
        console.debug('[Socket] Disconnecting...');
        this.cleanup();
    }

    /**
     * Handle connection retry
     * @private
     * @param {Error} [error] - Optional error that triggered retry
     */
    handleRetry(error) {
        if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
            this.onError(new Error('Max retry attempts reached'));
            return;
        }
        
        // Single retry path: initWithTokenCheck(). Avoid layering another reconnect loop
        // on top of Socket.IO's own reconnection mechanism.
        console.log(`[Socket] Retrying connection (attempt ${this.reconnectAttempts + 1}/${CONFIG.MAX_RETRIES})...`);
        this.reconnectAttempts++;
        const delay = Math.min(
            this.reconnectDelay * 2 + Math.random() * 1000,
            CONFIG.MAX_RETRY_DELAY
        );
        this.reconnectDelay = delay;
        setTimeout(() => this.initWithTokenCheck(), delay);
    }

    /**
     * Show alert message
     * @private
     * @param {string} type - Alert type
     // * @param {string} title - Alert title
     * @param {string} message - Alert message
     */
    showAlert(type, title, message) {
        if (typeof window !== 'undefined' && window.App?.Alerts?.showAlert) {
            window.App.Alerts.showAlert(type, title, message);
        } else {
            console.log(`[${type}] ${title}: ${message}`);
        }
    }
}

// Initialize and export singleton instance
let socketManagerInstance = null;

/**
 * Initialize socket manager
 * @returns {SocketManager} Initialized socket manager instance
 */
export function initializeSocketManager() {
    if (!socketManagerInstance) {
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
            }
        });
    }
    return socketManagerInstance;
}

// For backward compatibility with global App object
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Sockets = window.App.Sockets || initializeSocketManager();
}
