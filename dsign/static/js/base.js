/**
 * Core Application Initialization Module (ES Module)
 * Handles application bootstrap, authentication, and core services
 */

import { AppInitializer } from './utils/app-init.js';
import { clearToken } from './utils/helpers.js';
import { AppLogger } from './utils/logging.js';

class AppCore {
    constructor() {
        this.initialized = false;
        this.onReadyCallbacks = [];
        this.config = {
            debug: window.location.hostname === 'localhost',
            socketReconnectDelay: 1000,
            maxSocketRetries: 5,
            authCheckInterval: 60000,
            apiTimeout: 30000,
            socketEndpoint: '/socket.io'
        };
        this.state = {
            navigationInProgress: false,
            authChecked: false,
            socketInitialized: false,
            socketConnected: false
        };
        this.logger = new AppLogger('AppCore');
    }

    onReady(callback) {
        if (this.initialized && typeof callback === 'function') {
            try {
                callback();
            } catch (error) {
                this.handleError('Ready callback error:', error);
            }
        } else {
            this.onReadyCallbacks.push(callback);
        }
    }

    handleError(message, error) {
        this.logger.error(message, error);
        if (this.alerts) {
            this.alerts.showError('Error', message, error);
        }
    }

    async initialize() {
        this.logger.info('Starting application initialization');

        try {
            // Initialize core services first
            this.api = new APIService();
            this.auth = new AuthService();
            this.alerts = new AlertSystem();

            // Ensure legacy globals exist before any service calls.
            // Some modules access window.App.config/state/auth directly.
            window.App = window.App || {};
            window.App.config = window.App.config || this.config;
            window.App.state = window.App.state || this.state;
            window.App.API = window.App.API || this.api;
            window.App.Auth = window.App.Auth || this.auth;
            window.App.auth = window.App.auth || this.auth;
            window.App.Alerts = window.App.Alerts || this.alerts;
            window.App.alerts = window.App.alerts || this.alerts;
            
            // Wait for essential services to be ready
            await this.waitForDependencies();

            // Initialize AppInitializer
            this.initializer = new AppInitializer({
                api: this.api,
                auth: this.auth,
                alerts: this.alerts
            });
            
            await this.initializer.init();

            // Check authentication state
            const isLoginPage = window.location.pathname.includes('/api/auth/login');
            const isAuth = await this.auth.checkAuth().catch(() => false);
            if (!isAuth && !isLoginPage) {
                this.logger.warn('Not authenticated - redirecting to login');
                this.auth.handleUnauthorized();
                return;
            }

            // Initialize WebSocket with delay
            setTimeout(() => this.initializeWebSockets(), 500);
            
            // Set up periodic auth checks for non-login pages
            if (!isLoginPage) {
                this.logger.debug('Setting up periodic auth checks');
                
                setInterval(async () => {
                    const isAuth = await this.auth.checkAuth();
                    if (!isAuth) {
                        this.logger.warn('Periodic auth check failed');
                        this.auth.handleUnauthorized();
                    }
                }, this.config.authCheckInterval);
            }

            // Mark core as initialized
            this.initialized = true;
            this.logger.info('App core initialized');
            
            // Execute ready callbacks
            this.executeReadyCallbacks();
            
        } catch (error) {
            this.logger.error('App initialization failed:', error);
            if (this.alerts) {
                this.alerts.showError(
                    'Initialization Error', 
                    'Failed to start application', 
                    error
                );
            }
        }
    }

    async waitForDependencies() {
        const maxAttempts = 10;
        const delay = 500;
        
        for (let i = 0; i < maxAttempts; i++) {
            if (window.App?.API && window.App?.Auth) {
                return;
            }
            await new Promise(resolve => setTimeout(resolve, delay));
        }
        throw new Error('Dependencies not available');
    }

    executeReadyCallbacks() {
        this.onReadyCallbacks.forEach(cb => {
            try {
                if (typeof cb === 'function') cb();
            } catch (error) {
                this.handleError('Ready callback error:', error);
            }
        });
        this.onReadyCallbacks = [];
    }

    async initializeWebSockets() {
        if (this.state.socketInitialized) return;
        
        try {
            const isAuth = await this.auth.checkAuth();
            if (!isAuth) {
                this.logger.warn('WebSocket init aborted - not authenticated');
                return;
            }

            this.logger.debug('Initializing WebSocket connection');
            
            // Get fresh socket token with retry logic
            let socketToken;
            try {
                const result = await this.auth.getSocketToken();
                socketToken = result.token;
            } catch (error) {
                this.logger.error('Failed to get socket token:', error);
                // Refresh is cookie-based; retry once
                await this.auth.refreshToken().catch(() => {});
                const result = await this.auth.getSocketToken();
                socketToken = result.token;
            }
            
            // Initialize socket connection
            const socket = io(this.config.socketEndpoint, {
                auth: { token: socketToken },
                reconnection: true,
                reconnectionAttempts: this.config.maxSocketRetries,
                reconnectionDelay: this.config.socketReconnectDelay,
                transports: ['websocket'],
                upgrade: false
            });

            // Store socket globally
            window.appSocket = socket;
            this.state.socketInitialized = true;

            // Setup event handlers
            this.setupSocketHandlers(socket);
            
            // Expose socket interface
            this.sockets = {
                connect: () => socket.connect(),
                disconnect: () => socket.disconnect(),
                isConnected: () => socket.connected,
                emit: (event, data) => {
                    if (socket.connected) {
                        socket.emit(event, data);
                    } else {
                        this.logger.warn('Socket not connected, cannot emit', event);
                    }
                }
            };

        } catch (error) {
            this.handleSocketError(error);
        }
    }

    setupSocketHandlers(socket) {
        socket.on('connect', () => {
            this.logger.info('WebSocket connected');
            this.state.socketConnected = true;
        });

        socket.on('disconnect', (reason) => {
            this.logger.warn('WebSocket disconnected:', reason);
            this.state.socketConnected = false;
        });

        socket.on('connect_error', (error) => {
            const msg = error?.message || String(error || '');
            this.logger.error('WebSocket connection error:', { message: msg });
            if (msg.includes('auth') || msg.includes('token') || msg.includes('Unauthorized') || msg.includes('401')) {
                this.auth.handleUnauthorized();
            }
        });
    }

    handleSocketError(error) {
        const msg = error?.message || String(error || '');
        this.logger.error('WebSocket initialization failed:', { message: msg });
        if (msg.includes('auth') || msg.includes('token') || msg.includes('Unauthorized') || msg.includes('401')) {
            this.auth.handleUnauthorized();
        } else {
            setTimeout(() => this.initializeWebSockets(), 5000);
        }
    }
}

class AuthService {
    constructor() {
        this.logger = new AppLogger('AuthService');
    }

    async checkAuth() {
        try {
            const response = await window.App.API.fetch('/api/auth/status', { method: 'GET' });
            const data = await response.json().catch(() => ({}));
            return Boolean(data?.authenticated);
        } catch (error) {
            this.logger.warn('Auth check failed:', error);
            return false;
        }
    }

    async refreshToken() {
        try {
            const response = await window.App.API.fetch('/api/auth/refresh-token', {
                method: 'POST',
                credentials: 'include'
            });
            
            if (!response.ok) throw new Error('Token refresh failed');
            await response.json().catch(() => ({}));
            return true;
        } catch (error) {
            this.logger.error('Token refresh failed:', error);
            throw error;
        }
    }

    handleUnauthorized() {
        window.App = window.App || {};
        window.App.state = window.App.state || {};
        if (window.App.state.navigationInProgress) return;
        window.App.state.navigationInProgress = true;
        clearToken();
        
        if (window.appSocket) {
            window.appSocket.disconnect();
            delete window.appSocket;
        }
        
        const currentPath = window.location.pathname;
        if (!currentPath.includes('/api/auth/login')) {
            this.logger.warn('Redirecting to login');
            const redirectUrl = encodeURIComponent(currentPath + window.location.search);
            window.location.href = `/api/auth/login?redirect=${redirectUrl}`;
        }
    }

    setToken(token) {
        void token;
    }

    clearAuth() {
        // Best-effort cleanup of legacy non-HttpOnly storage.
        clearToken();
    }

    async getSocketToken() {
        try {
            const response = await window.App.API.fetch('/api/auth/socket-token', { credentials: 'include' });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            if (!data?.token) {
                throw new Error('No token in response');
            }
            return data;
        } catch (error) {
            this.logger.error('Failed to get socket token:', error);
            throw error;
        }
    }

    async waitForToken(maxAttempts = 10, interval = 500) {
        // Legacy API: session cookie auth does not expose a token to JS.
        void maxAttempts;
        void interval;
        return Promise.resolve('session');
    }
}

class AlertSystem {
    constructor() {
        this.logger = new AppLogger('AlertSystem');
    }

    showAlert(type, title, message, options = {}) {
        this.logger.info(`Alert: ${title}`, { type, message });
        
        const event = new CustomEvent('app-alert', {
            detail: { type, title, message, ...options }
        });
        document.dispatchEvent(event);
    }
    
    showError(title, message, error) {
        this.logger.error(`Error Alert: ${title}`, error, { message });
        this.showAlert('error', title, message || error?.message);
    }
}

class APIService {
    constructor() {
        this.logger = new AppLogger('APIService');
    }

    async fetch(url, options = {}) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 
            options.timeout || window.App?.config?.apiTimeout || 30000);
        
        const requestId = Math.random().toString(36).substring(2, 9);
        const startTime = performance.now();
        
        try {
            this.logger.debug(`API Request [${requestId}]: ${url}`, {
                method: options.method || 'GET',
                headers: options.headers
            });

            const headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                ...options.headers
            };

            const response = await fetch(url, {
                credentials: 'include',
                signal: controller.signal,
                ...options,
                headers
            });

            clearTimeout(timeoutId);
            const duration = (performance.now() - startTime).toFixed(2);
            
            this.logger.debug(`API Response [${requestId}]: ${response.status} (${duration}ms)`, {
                status: response.status,
                url
            });

            if (response.status === 401) {
                this.logger.warn('Authentication expired', { url });
                (window.App?.auth || window.App?.Auth)?.handleUnauthorized?.();
                throw new Error('Authentication required');
            }

            if (response.status === 429) {
                this.logger.warn('Rate limit exceeded', { url });
                throw new Error('Too many requests');
            }

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }

            return response;
        } catch (error) {
            clearTimeout(timeoutId);
            this.logger.error(`API Request [${requestId}] failed:`, error, {
                url,
                method: options.method || 'GET'
            });
            throw error;
        }
    }
}

// Initialize and export a singleton App instance.
// Guard against double-loading (e.g., script included twice).
const App = window.__DSIGN_APP__ || new AppCore();
window.__DSIGN_APP__ = App;
App.logger = App.logger || new AppLogger('App');

// Note: App.initialize() is called from the base template once DOM is ready.

// Global error handling
window.addEventListener('error', (event) => {
    App.logger.error('Global error:', event.error, {
        message: event.message,
        source: event.filename,
        line: event.lineno,
        column: event.colno
    });
});

window.addEventListener('unhandledrejection', (event) => {
    App.logger.error('Unhandled rejection:', event.reason);
    if (App.alerts) {
        App.alerts.showError('Async Error', 'An operation failed', event.reason);
    }
});

export default App;
