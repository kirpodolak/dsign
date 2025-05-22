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
    CONNECTION_TIMEOUT: 10000
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
        
        // Initialize connection
        this.initWithTokenCheck();
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
        if (typeof window !== 'undefined' && window.App?.Helpers?.setToken) {
            window.App.Helpers.setToken(newToken);
        } else if (typeof localStorage !== 'undefined') {
            localStorage.setItem('authToken', newToken);
        }
    }

    /**
     * Initialize connection with token check
     * @private
     */
    async initWithTokenCheck() {
        try {
            console.debug('[Socket] Starting connection with token check');
            
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = setTimeout(() => {
                if (!this.connectionEstablished) {
                    this.onError(new Error('Connection timeout'));
                    this.handleRetry();
                }
            }, CONFIG.CONNECTION_TIMEOUT);
            
            const token = await this.getSocketToken();
            if (!token) {
                throw new Error('No valid authentication token available');
            }
            
            this.init();
        } catch (error) {
            console.error('[Socket] Initialization error:', error);
            this.handleRetry(error);
        }
    }

    /**
     * Get WebSocket authentication token
     * @private
     * @returns {Promise<string|null>} Authentication token or null
     */
    async getSocketToken() {
        try {
            // Use AuthService if available
            if (typeof window !== 'undefined' && window.App?.Auth?.getSocketToken) {
                const { token } = await window.App.Auth.getSocketToken();
                return token;
            }
            
            // Fallback to direct API call
            const response = await fetch('/auth/socket-token', {
                credentials: 'include'
            });
            
            if (!response.ok) {
                throw new Error('Failed to get socket token');
            }
            
            const data = await response.json();
            return data.token;
        } catch (error) {
            console.error('[Socket] Token fetch error:', error);
            return null;
        }
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
            const response = await fetch('/auth/api/refresh-token', {
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
        this.reconnectAttempts++;
        
        // Exponential backoff with jitter
        this.reconnectDelay = Math.min(
            this.reconnectDelay * 2 + Math.random() * 1000,
            CONFIG.MAX_RETRY_DELAY
        );
        
        if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
            this.showAlert(
                'error', 
                'Connection Error', 
                'Real-time updates disabled. Please refresh the page.'
            );
        } else {
            console.log(`[Socket] Retrying in ${Math.round(this.reconnectDelay/1000)} sec...`);
            setTimeout(() => this.init(), this.reconnectDelay);
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
                this.socket.emit('ping', {}, () => {
                    const latency = Date.now() - start;
                    this.socket.emit('pong', latency);
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
        
        console.log(`[Socket] Retrying connection (attempt ${this.reconnectAttempts + 1}/${CONFIG.MAX_RETRIES})...`);
        setTimeout(() => this.initWithTokenCheck(), this.reconnectDelay);
        this.reconnectAttempts++;
    }

    /**
     * Show alert message
     * @private
     * @param {string} type - Alert type
     * @param {string} title - Alert title
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
