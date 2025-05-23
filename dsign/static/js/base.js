/**
 * Core Application Initialization Module (ES Module)
 * Handles application bootstrap, authentication, and core services
 */

import Swal from 'sweetalert2';
import AppInitializer from './utils//app-init.js';

// Import other utils as needed
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
            socketInitialized: false
        };
        this.logger = new AppLogger('AppCore');
        this.initializer = AppInitializer;
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
    }

    async initialize() {
        this.logger.info('Starting application initialization');

        try {
            // Delegate initialization to AppInitializer
            await this.initializer.init();

            // Check authentication state
            const isLoginPage = window.location.pathname.includes('/api/auth/login');
            const token = getToken();
            
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
            this.onReadyCallbacks.forEach(cb => {
                try {
                    if (typeof cb === 'function') cb();
                } catch (error) {
                    this.handleError('Ready callback error:', error);
                }
            });
            
            this.onReadyCallbacks = [];
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

    async initializeWebSockets() {
        if (this.state.socketInitialized) return;
        
        try {
            // Check authentication first
            const isAuth = await this.auth.checkAuth();
            if (!isAuth) {
                this.logger.warn('WebSocket init aborted - not authenticated');
                return;
            }

            this.logger.debug('Initializing WebSocket connection');
            
            // Get fresh socket token
            const { token, socketUrl } = await this.auth.getSocketToken();
            
            // Initialize socket connection
            const socket = io(socketUrl || this.config.socketEndpoint, {
                auth: { token },
                reconnection: true,
                reconnectionAttempts: this.config.maxSocketRetries,
                reconnectionDelay: this.config.socketReconnectDelay,
                transports: ['websocket'],
                upgrade: false
            });

            // Store socket globally
            window.appSocket = socket;
            this.state.socketInitialized = true;

            // Event handlers
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
            this.logger.error('WebSocket initialization failed:', error);
            if (error.message.includes('auth') || error.message.includes('token')) {
                this.auth.handleUnauthorized();
            }
        }
    }
}

class AuthService {
    constructor() {
        this.logger = new AppLogger('AuthService');
    }

    async checkAuth() {
        try {
            const token = getToken();
            if (!token) return false;
            
            const response = await fetch('/auth/api/check-auth', {
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

    handleUnauthorized() {
        if (window.App?.state?.navigationInProgress) return;
        
        window.App.state.navigationInProgress = true;
        clearToken();
        
        // Disconnect socket if exists
        if (window.appSocket) {
            window.appSocket.disconnect();
            delete window.appSocket;
        }
        
        // Prevent redirect loops
        const currentPath = window.location.pathname;
        if (!currentPath.includes('/api/auth/login')) {
            this.logger.warn('Redirecting to login');
            const redirectUrl = encodeURIComponent(currentPath + window.location.search);
            window.location.href = `/api/auth/login?redirect=${redirectUrl}`;
        }
    }

    async waitForToken(maxAttempts = 10, interval = 500) {
        let attempts = 0;
        
        return new Promise((resolve, reject) => {
            const checkToken = () => {
                attempts++;
                const token = getToken();
                
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

    async getSocketToken() {
        try {
            const response = await fetch('/auth/socket-token');
            if (!response.ok) {
                throw new Error('Failed to get socket token');
            }
            return await response.json();
        } catch (error) {
            this.logger.error('Failed to get socket token:', error);
            throw error;
        }
    }
}

class AlertSystem {
    constructor() {
        this.logger = new AppLogger('AlertSystem');
    }

    showAlert(type, title, message, options = {}) {
        this.logger.info(`Alert: ${title}`, { type, message });
        
        // Dispatch event for UI components
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

            const token = getToken();
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
