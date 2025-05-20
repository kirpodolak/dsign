/**
 * Enhanced WebSocket Manager for real-time communication
 * Handles connection, authentication, and event management with improved security
 */
(function() {
    'use strict';

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

    class SocketManager {
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
            
            // Connection state tracking
            this.connectionState = {
                lastSuccess: null,
                lastError: null,
                retryCount: 0
            };
            
            // Callbacks
            this.onError = options.onError || this.defaultErrorHandler;
            this.onTokenRefresh = options.onTokenRefresh || this.defaultTokenRefreshHandler;
            this.onReconnect = options.onReconnect || null;
            
            // Initialize connection
            this.initWithTokenCheck();
        }

        // Default error handler
        defaultErrorHandler(error) {
            console.error('[Socket] Error:', error);
            this.showAlert('error', 'Connection Error', error.message);
        }

        // Default token refresh handler
        defaultTokenRefreshHandler(newToken) {
            console.debug('[Socket] Token refreshed');
            localStorage.setItem('authToken', newToken);
            document.cookie = `authToken=${newToken}; path=/; Secure; SameSite=Strict`;
        }

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

        async getSocketToken() {
            try {
                // First try to use AuthService if available
                if (window.App?.Auth?.getSocketToken) {
                    return await window.App.Auth.getSocketToken();
                }
                
                // Fallback to direct API call
                const response = await fetch('/api/socket-token', {
                    credentials: 'include'
                });
                
                if (!response.ok) {
                    throw new Error('Failed to get socket token');
                }
                
                const { token } = await response.json();
                return token;
            } catch (error) {
                console.error('[Socket] Token fetch error:', error);
                return null;
            }
        }

        waitForValidToken() {
            return new Promise((resolve, reject) => {
                let attempts = 0;
                
                const checkToken = () => {
                    attempts++;
                    const token = this.getValidToken();
                    
                    if (token) {
                        resolve(token);
                    } else if (attempts >= CONFIG.TOKEN_MAX_ATTEMPTS) {
                        reject(new Error('Token not available after maximum attempts'));
                    } else {
                        setTimeout(checkToken, CONFIG.TOKEN_CHECK_INTERVAL);
                    }
                };
                
                checkToken();
            });
        }

        getValidToken() {
            try {
                // Try multiple secure ways to get token
                const token = window.App?.Helpers?.getToken?.() || 
                             localStorage.getItem('authToken') || 
                             this.getCookie('authToken');
                
                if (!token) {
                    console.warn('[Socket] No authentication token available');
                    return null;
                }
                
                // Enhanced JWT validation
                if (!this.validateTokenStructure(token)) {
                    return null;
                }
                
                return token;
            } catch (error) {
                console.error('[Socket] Token validation error:', error);
                return null;
            }
        }

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

        scheduleTokenRefresh() {
            if (this.tokenRefreshTimer) {
                clearTimeout(this.tokenRefreshTimer);
            }
            
            this.tokenRefreshTimer = setTimeout(() => {
                this.refreshToken();
            }, CONFIG.TOKEN_REFRESH_THRESHOLD - 60000); // 1 minute before expiration
        }

        async refreshToken() {
            try {
                console.debug('[Socket] Refreshing token...');
                
                // First try to use AuthService if available
                if (window.App?.Auth?.refreshToken) {
                    return await window.App.Auth.refreshToken();
                }
                
                // Fallback to direct API call
                const response = await fetch('/api/refresh-token', {
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
                
                return token;
            } catch (error) {
                console.error('[Socket] Token refresh failed:', error);
                throw error;
            }
        }

        getCookie(name) {
            const match = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
            return match ? decodeURIComponent(match[2]) : null;
        }

        init() {
            try {
                console.debug('[Socket] Initializing connection...');
                
                if (typeof io === 'undefined') {
                    throw new Error('Socket.IO library not loaded');
                }

                this.cleanup();

                // Get client IP for security validation
                this.ipAddress = this.getClientIP();

                this.socket = io({
                    reconnection: true,
                    reconnectionAttempts: CONFIG.MAX_RETRIES,
                    reconnectionDelay: this.reconnectDelay,
                    transports: ['websocket'],
                    upgrade: false,
                    timeout: CONFIG.CONNECTION_TIMEOUT,
                    auth: (cb) => {
                        try {
                            const token = this.getValidToken();
                            if (!token) {
                                throw new Error('No authentication token available');
                            }
                            
                            // Include IP address in auth for additional security
                            cb({ 
                                token,
                                ip: this.ipAddress,
                                userAgent: navigator.userAgent
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

        getClientIP() {
            // This would be enhanced with actual IP detection in production
            return '';
        }

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
            this.socket.on('inactivity_timeout', (data) => this.handleInactivityTimeout(data));
            this.socket.on('auth_timeout', (data) => this.handleAuthTimeout(data));
            this.socket.on('pong', (latency) => this.handlePong(latency));
            this.socket.on('reconnect_failed', () => this.handleReconnectFailed());
            this.socket.on('reconnect_attempt', (attempt) => this.handleReconnectAttempt(attempt));
        }

        handleConnect() {
            console.debug('[Socket] Connection established');
            this.connectionEstablished = true;
            clearTimeout(this.connectionTimeout);
            
            this.isConnected = true;
            this.reconnectAttempts = 0;
            this.reconnectDelay = CONFIG.INITIAL_RETRY_DELAY;
            
            // Update connection state
            this.connectionState.lastSuccess = new Date();
            this.connectionState.retryCount = 0;
            
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
            
            // Show connection status
            this.showAlert('success', 'Connected', 'Real-time updates enabled');
            
            // Track activity
            this.lastActivity = Date.now();
        }

        handleAuthenticationResult(data) {
            clearTimeout(this.authTimeout);
            
            if (data.success) {
                this.isAuthenticated = true;
                console.debug('[Socket] Authentication successful');
                
                // Update IP address if changed
                if (data.ip) {
                    this.ipAddress = data.ip;
                }
            } else {
                this.isAuthenticated = false;
                console.error('[Socket] Authentication failed:', data.error);
                this.showAlert('error', 'Authentication Failed', data.error);
                this.disconnect();
            }
        }

        handleTokenRefresh(newToken) {
            console.debug('[Socket] Received token refresh');
            if (this.onTokenRefresh) {
                this.onTokenRefresh(newToken);
            }
        }

        handleAuthError(error) {
            this.onError(new Error(`Authentication error: ${error.message}`));
            if (window.App.Base?.handleUnauthorized) {
                window.App.Base.handleUnauthorized();
            }
        }

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

        handleError(error) {
            console.error('[Socket] Connection error:', error);
            this.reconnectAttempts++;
            
            // Update connection state
            this.connectionState.lastError = {
                time: new Date(),
                error: error.message
            };
            
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

        handleReconnectAttempt(attempt) {
            console.debug(`[Socket] Reconnect attempt ${attempt}`);
            if (this.onReconnect) {
                this.onReconnect(attempt);
            }
        }

        handleReconnectFailed() {
            this.onError(new Error('Max reconnection attempts reached'));
            this.showAlert(
                'error', 
                'Connection Error', 
                'Real-time updates disabled. Please refresh the page.'
            );
        }

        handleInactivityTimeout(data) {
            console.warn('[Socket] Disconnected due to inactivity');
            this.showAlert('warning', 'Session Expired', data.message);
            this.disconnect();
        }

        handleAuthTimeout(data) {
            console.warn('[Socket] Authentication timeout');
            this.showAlert('error', 'Authentication Timeout', 'Please refresh the page');
            this.disconnect();
        }

        handlePong(latency) {
            console.debug(`[Socket] Ping latency: ${latency}ms`);
            this.lastActivity = Date.now();
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
            this.showAlert(
                data.level || 'info', 
                data.title || 'Notification', 
                data.message,
                data.options
            );
        }

        showAlert(type, title, message, options = {}) {
            if (window.App.Alerts?.showAlert) {
                window.App.Alerts.showAlert(type, title, message, options);
            } else {
                console.log(`[${type}] ${title}: ${message}`);
            }
        }

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

        processPendingEvents() {
            while (this.pendingEvents.length > 0) {
                const { event, data, resolve, reject } = this.pendingEvents.shift();
                this.emit(event, data).then(resolve).catch(reject);
            }
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
            clearInterval(this.pingInterval);
            clearTimeout(this.authTimeout);
            clearTimeout(this.connectionTimeout);
            clearTimeout(this.tokenRefreshTimer);
            this.pingInterval = null;
            this.authTimeout = null;
            this.connectionTimeout = null;
            this.tokenRefreshTimer = null;
        }

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

        disconnect() {
            console.debug('[Socket] Disconnecting...');
            this.cleanup();
        }

        handleRetry(error) {
            if (this.reconnectAttempts >= CONFIG.MAX_RETRIES) {
                this.onError(new Error('Max retry attempts reached'));
                return;
            }
            
            console.log(`[Socket] Retrying connection (attempt ${this.reconnectAttempts + 1}/${CONFIG.MAX_RETRIES})...`);
            setTimeout(() => this.initWithTokenCheck(), this.reconnectDelay);
            this.reconnectAttempts++;
        }
    }

    // Initialize after App and DOM are ready
    function initialize() {
        if (!window.App) {
            console.warn('[Socket] App not initialized, waiting...');
            setTimeout(initialize, 100);
            return;
        }

        console.debug('[Socket] Initializing socket manager...');
        window.App.Sockets = new SocketManager({
            onError: (error) => {
                console.error('[Socket] Global error handler:', error);
                if (window.App.Alerts?.showError) {
                    window.App.Alerts.showError('Socket Error', error.message);
                }
            },
            onTokenRefresh: (newToken) => {
                console.debug('[Socket] Updating token from refresh');
                if (window.App.Helpers?.setToken) {
                    window.App.Helpers.setToken(newToken, true);
                } else {
                    localStorage.setItem('authToken', newToken);
                }
            },
            onReconnect: (attempt) => {
                console.debug(`[Socket] Reconnect attempt ${attempt}`);
            }
        });
    }

    // Start initialization when DOM is ready
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        initialize();
    } else {
        document.addEventListener('DOMContentLoaded', initialize);
    }
})();
