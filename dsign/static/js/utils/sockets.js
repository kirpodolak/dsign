/**
 * WebSocket Manager Module
 * @module SocketManager
 * @description Enhanced WebSocket manager for real-time communication with authentication and event management
 */

// Configuration constants with enhanced security settings
const CONFIG = {
    MAX_RETRIES: 5,
    INITIAL_RETRY_DELAY: 2000,  // Increased from 1000
    MAX_RETRY_DELAY: 60000,     // Increased from 30000
    PING_INTERVAL: 25000,
    AUTH_TIMEOUT: 30000,
    TOKEN_CHECK_INTERVAL: 500,
    TOKEN_MAX_ATTEMPTS: 10,
    TOKEN_REFRESH_THRESHOLD: 300000, // 5 minutes before expiration
    MAX_EVENT_QUEUE: 50,
    CONNECTION_TIMEOUT: 10000,
    SERVER_CHECK_INTERVAL: 5000,
    SERVER_CHECK_TIMEOUT: 2000
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
        this.authSocket = null;
        this.isConnected = false;
        this.isAuthenticated = false;
        this.connectionEstablished = false;
        this.serverAvailable = false;
        
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
        
        // Security
        this.lastActivity = Date.now();
        this.ipAddress = null;
        
        // Callbacks
        this.onError = options.onError || ((error) => this.defaultErrorHandler(error));
        this.onTokenRefresh = options.onTokenRefresh || ((token) => this.defaultTokenRefreshHandler(token));
        this.onReconnect = options.onReconnect || null;

        // Initialize methods
        this.initWithTokenCheck = () => this._initWithTokenCheck();
        this.handleRetry = (error) => this._handleRetry(error);
        this.handleError = (error) => this._handleError(error);
        this.handleConnect = () => this._handleConnect();
        this.handleDisconnect = (reason) => this._handleDisconnect(reason);
        this.checkServerAvailability = () => this._checkServerAvailability();
        
        // Initialize connections
        this.initWithTokenCheck();
        this.initAuthSocket();
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
     * Check if server is available
     * @private
     */
    async _checkServerAvailability() {
        try {
            const checkUrls = [
                '/api/settings/current',
                '/socket.io/'
            ];

            let serverAvailable = false;
            
            for (const url of checkUrls) {
                try {
                    const controller = new AbortController();
                    const timeout = setTimeout(() => controller.abort(), CONFIG.SERVER_CHECK_TIMEOUT);
                    
                    const response = await fetch(url, {
                        method: 'GET',
                        signal: controller.signal
                    });
                    
                    clearTimeout(timeout);
                    
                    if (response.status) {
                        serverAvailable = true;
                        break;
                    }
                } catch (error) {
                    console.debug(`[Socket] Server check on ${url} failed:`, error.message);
                }
            }

            this.serverAvailable = serverAvailable;
            
            if (!this.serverAvailable) {
                console.warn('[Socket] Server not available, will retry...');
                this.serverCheckTimer = setTimeout(() => this.checkServerAvailability(), CONFIG.SERVER_CHECK_INTERVAL);
            }
            return this.serverAvailable;
        } catch (error) {
            console.warn('[Socket] Server check failed:', error);
            this.serverAvailable = false;
            this.serverCheckTimer = setTimeout(() => this.checkServerAvailability(), CONFIG.SERVER_CHECK_INTERVAL);
            return false;
        }
    }

    /**
     * Initialize authentication status WebSocket
     * @private
     */
    initAuthSocket() {
        if (typeof io === 'undefined') {
            console.error('[Socket] Socket.IO library not loaded');
            return;
        }

        try {
            if (this.authSocket) {
                this.authSocket.disconnect();
            }

            this.authSocket = io('/auth', {
                reconnection: true,
                reconnectionAttempts: CONFIG.MAX_RETRIES,
                reconnectionDelay: this.reconnectDelay,
                reconnectionDelayMax: 10000,
                randomizationFactor: 0.5,
                transports: ['websocket'],
                upgrade: false,
                forceNew: true
            });

            // Setup auth socket event handlers
            this.authSocket.on('connect', () => {
                console.debug('[AuthSocket] Connected');
                this.checkAuthViaWebSocket();
            });

            this.authSocket.on('disconnect', (reason) => {
                console.log('[AuthSocket] Disconnected:', reason);
                if (reason === 'io server disconnect') {
                    setTimeout(() => this.initAuthSocket(), 5000);
                }
            });

            this.authSocket.on('connect_error', (error) => {
                console.error('[AuthSocket] Connection error:', error);
                setTimeout(() => this.initAuthSocket(), 5000);
            });

            this.authSocket.on('auth_update', (data) => {
                console.debug('[AuthSocket] Received auth update:', data);
                if (typeof window !== 'undefined' && window.App?.Auth?.updateAuthStatus) {
                    window.App.Auth.updateAuthStatus(data?.authenticated ?? false);
                }
            });

            this.authSocket.on('auth_status_response', (data) => {
                console.debug('[AuthSocket] Received auth status:', data);
                if (typeof window !== 'undefined' && window.App?.Auth?.updateAuthStatus) {
                    window.App.Auth.updateAuthStatus(data?.authenticated ?? false);
                }
            });

        } catch (error) {
            console.error('[AuthSocket] Initialization error:', error);
            setTimeout(() => this.initAuthSocket(), 5000);
        }
    }

    /**
     * Initialize connection with token check
     * @private
     */
    async _initWithTokenCheck() {
        try {
            if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                throw new Error('Max reconnect attempts reached');
            }

            console.debug('[Socket] Starting connection with token check');

            if (!(await this.checkServerAvailability())) {
                console.debug('[Socket] Server not available, delaying connection attempt');
                await new Promise(resolve => setTimeout(resolve, 2000));
                return this.handleRetry(new Error('Server not available'));
            }
            
            if (window.App?.Auth && !(await window.App.Auth.checkAuth())) {
                console.debug('[Socket] User not authenticated, waiting...');
                await new Promise(resolve => setTimeout(resolve, 2000));
                return this.handleRetry(new Error('User not authenticated'));
            }
            
            await this.waitForAPI();
            
            const socketToken = await this.getSocketToken();
            this.init(socketToken);
        } catch (error) {
            console.error('[Socket] Initialization error:', error);
            this.handleRetry(error);
        }
    }

    /**
     * Initialize socket connection
     * @private
     */
    init(token) {
        try {
            console.debug('[Socket] Initializing connection...');
            
            if (typeof io === 'undefined') {
                throw new Error('Socket.IO library not loaded');
            }

            this.cleanup();

            this.socket = io({
                reconnection: true,
                reconnectionAttempts: this.maxReconnectAttempts,
                reconnectionDelay: this.reconnectDelay,
                reconnectionDelayMax: CONFIG.MAX_RETRY_DELAY,
                randomizationFactor: 0.5,
                transports: ['websocket'],
                upgrade: false,
                timeout: CONFIG.CONNECTION_TIMEOUT,
                auth: { token },
                secure: window.location.protocol === 'https:'
            });

            this.setupEventHandlers();
        } catch (error) {
            console.error('[Socket] Initialization error:', error);
            this.handleRetry(error);
        }
    }

    /**
     * Handle connection error
     * @private
     * @param {Error} error - Error object
     */
    _handleError(error) {
        console.error('[Socket] Connection error:', error);
        this.reconnectAttempts++;
        
        // Enhanced exponential backoff with jitter
        this.reconnectDelay = Math.min(
            Math.max(this.reconnectDelay * 2, CONFIG.INITIAL_RETRY_DELAY) + Math.random() * 2000,
            CONFIG.MAX_RETRY_DELAY
        );
        
        if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
            this.showAlert(
                'error', 
                'Connection Error', 
                'Real-time updates disabled. Please check your network connection.'
            );
            setTimeout(() => {
                this.reconnectAttempts = 0;
                this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            }, 120000);
        } else {
            console.log(`[Socket] Will retry in ${Math.round(this.reconnectDelay/1000)} sec...`);
            setTimeout(() => {
                this.checkServerAvailability().then(available => {
                    if (available) this.initWithTokenCheck();
                });
            }, this.reconnectDelay);
        }
    }

    // ... (остальные методы остаются без изменений)
}

// Initialize and export singleton instance
let socketManagerInstance = null;

export function initializeSocketManager() {
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
                }
            });
        } catch (error) {
            console.error('Failed to initialize SocketManager:', error);
            throw error;
        }
    }
    return socketManagerInstance;
}

// For backward compatibility with global App object
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    try {
        window.App.Sockets = window.App.Sockets || initializeSocketManager();
    } catch (error) {
        console.error('Failed to initialize global App.Sockets:', error);
        window.App.Sockets = {
            emit: () => console.warn('SocketManager not initialized'),
            disconnect: () => {}
        };
    }
}
