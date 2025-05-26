/**
 * Core Application Initialization Module (ES Module)
 * Handles application bootstrap, authentication, and core services
 */

import { AppInitializer } from './utils/app-init.js';
import { getToken, clearToken, setCachedData, getCachedData } from './utils/helpers.js';
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
            const token = await this.auth.waitForToken().catch(() => null);
            
            if (!token && !isLoginPage) {
                this.logger.warn('No token - redirecting to login');
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
            // Verify token is available and valid
            const token = await this.auth.waitForToken();
            if (!token) {
                this.logger.warn('WebSocket init aborted - no token available');
                return;
            }

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
                await this.auth.refreshToken();
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
            this.logger.error('WebSocket connection error:', error);
            if (error.message.includes('auth') || error.message.includes('token')) {
                this.auth.handleUnauthorized();
            }
        });
    }

    handleSocketError(error) {
        this.logger.error('WebSocket initialization failed:', error);
        if (error.message.includes('auth') || error.message.includes('token')) {
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
            const token = await this.waitForToken();
            if (!token) return false;
            
            const response = await window.App.API.fetch('/auth/api/check-auth', {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            
            const data = await response.json();
            return data?.authenticated || false;
        } catch (error) {
            this.logger.warn('Auth check failed:', error);
            return false;
        }
    }

    async refreshToken() {
        try {
            const response = await window.App.API.fetch('/auth/refresh-token', {
                method: 'POST',
                credentials: 'include'
            });
            
            if (!response.ok) throw new Error('Token refresh failed');
            
            const { token } = await response.json();
            this.setToken(token);
            return true;
        } catch (error) {
            this.logger.error('Token refresh failed:', error);
            this.clearAuth();
            throw error;
        }
    }

    handleUnauthorized() {
        if (window.App?.state?.navigationInProgress) return;
        
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
        try {
            window.App = window.App || {};
            window.App.token = token;
            
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem('authToken', token);
            }
            
            if (typeof document !== 'undefined') {
                document.cookie = `authToken=${token}; path=/; max-age=${3600*24*7}; Secure; SameSite=Lax`;
            }
        } catch (e) {
            this.logger.error('Failed to save auth token', e);
        }
    }

    clearAuth() {
        try {
            window.App = window.App || {};
            delete window.App.token;
            
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem('authToken');
            }
            
            if (typeof document !== 'undefined') {
                document.cookie = 'authToken=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';
            }
        } catch (e) {
            this.logger.error('Failed to clear auth token', e);
        }
    }

    async getSocketToken() {
        try {
            const token = await this.waitForToken();
            const response = await window.App.API.fetch('/auth/socket-token', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            
            if (!response.ok) {
                if (response.status === 401) {
                    await this.refreshToken();
                    return this.getSocketToken();
                }
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
        let attempts = 0;
        
        return new Promise((resolve, reject) => {
            const checkToken = () => {
                attempts++;
                const token = window.App?.token || 
                             localStorage.getItem('authToken') || 
                             getCookie('authToken');
                
                if (token) {
                    resolve(token);
                } else if (attempts >= maxAttempts) {
                    reject(new Error('Token not available'));
                } else {
                    setTimeout(checkToken, interval);
                }
            };
            
            checkToken();
        });
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
            options.timeout || window.App.config.apiTimeout);
        
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

            const token = await window.App.auth.waitForToken().catch(() => null);
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }

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
                window.App.auth.handleUnauthorized();
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

// Initialize and export the App instance
const App = new AppCore();
App.logger = new AppLogger('App');
App.auth = new AuthService();
App.alerts = new AlertSystem();
App.api = new APIService();

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    App.initialize();
});

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
