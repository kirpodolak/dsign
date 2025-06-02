import { showAlert, showError } from './alerts.js';
import { getToken, getCookie, deleteCookie } from './helpers.js';
import { AuthService } from './auth.js';
import { SocketManager } from './sockets.js';
import PlayerControls from './player-controls.js';
import { AppLogger } from './logging.js';
import { fetchAPI } from './api.js';

class AppInitializer {
    constructor() {
        this.retryCount = 0;
        this.maxRetryCount = 3;
        this.initialRetryDelay = 2000;
        this.debugMode = window.location.hostname === 'localhost' || 
                        window.location.hostname === '127.0.0.1' ||
                        window.location.search.includes('debug=true');

        this.logger = new AppLogger('AppInitializer');
        this.authService = new AuthService();
        this.socketManager = null;
        this.playerControls = new PlayerControls({
            API: { fetch: fetchAPI },
            Alerts: { showAlert, showError },
            Helpers: {
                toggleButtonState: this.toggleButtonState.bind(this)
            }
        });

        this.initPromise = null;
    }

    async init() {
        // Ensure initialization only happens once
        if (this.initPromise) {
            return this.initPromise;
        }

        this.initPromise = (async () => {
            try {
                this.logger.debug('Starting application initialization');
                
                // Initialize global state first
                this.initGlobalState();

                // Prevent redirect loops before any auth checks
                if (this.preventRedirectLoops()) {
                    return;
                }

                // Initialize socket manager after global state
                this.socketManager = new SocketManager({
                    authService: this.authService,
                    logger: this.logger
                });

                // Check authentication
                const isAuthenticated = await this.checkAuth();
                if (!isAuthenticated) {
                    return;
                }

                // Setup core components
                await Promise.all([
                    this.setupLoader(),
                    this.setupAlerts(),
                    this.setupErrorHandling(),
                    this.setupAuthMonitoring()
                ]);

                // Initialize WebSocket if authenticated
                await this.initWebSocket();

                // Initialize other modules
                this.initModules();

                this.logger.debug('Application initialization completed');
            } catch (error) {
                this.logger.error('Initialization error:', error);
                this.showFatalError('Application initialization failed');
                throw error;
            }
        })();

        return this.initPromise;
    }

    initGlobalState() {
        window.App = window.App || {};
        window.App.state = window.App.state || {
            navigationInProgress: false,
            socketConnected: false,
            initialized: false,
            lastAuthCheck: null,
            retryDelays: [2000, 5000, 10000] // Progressive delays for retries
        };

        if (this.debugMode) {
            this.logger.debug('Global state initialized', window.App.state);
        }
    }

    preventRedirectLoops() {
        const isLoginPage = window.location.pathname.includes('/api/auth/login');
        const hasRedirectLoop = window.location.search.includes('redirect_loop=true');
    
        if (isLoginPage) {
            // Clear any existing next parameters to break the loop
            if (window.location.search.includes('next=')) {
                window.location.href = '/api/auth/login?clear=true';
                return true;
            }
            if (hasRedirectLoop) {
                window.location.href = '/api/auth/login?clear=true';
                return true;
            }
        }
        return false;
    }

    async checkAuth() {
        try {
            const response = await fetch('/api/auth/status', {
                credentials: 'include',
                headers: {
                    'Accept': 'application/json'
                }
            });
    
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
        
            const data = await response.json();
            return data.authenticated;
        } catch (error) {
            console.error('Auth check failed:', error);
            // Only redirect if not already on login page
            if (!window.location.pathname.includes('/api/auth/login')) {
                window.location.href = '/api/auth/login';
            }
            return false;
        }
    }

    async setupLoader() {
        return new Promise((resolve) => {
            const loader = document.getElementById('page-loader');
            if (!loader) {
                resolve();
                return;
            }

            const fadeOut = () => {
                loader.style.transition = 'opacity 300ms ease-out';
                loader.style.opacity = '0';
                
                setTimeout(() => {
                    loader.style.display = 'none';
                    document.dispatchEvent(new CustomEvent('app-ready', {
                        detail: {
                            authenticated: this.authService.getToken() !== null,
                            timestamp: Date.now()
                        }
                    }));
                    resolve();
                }, 300);
            };

            // Start fade out after minimum display time
            setTimeout(fadeOut, 500);
        });
    }

    setupAlerts() {
        if (typeof Swal === 'undefined') {
            this.logger.warn('SweetAlert2 not available, using fallback');
            window.showAlert = (type, title, message) => {
                console[type === 'error' ? 'error' : 'log'](`[${type}] ${title}: ${message}`);
            };
            return;
        }

        document.addEventListener('app-alert', (event) => {
            try {
                const { type, title, message, options } = event.detail;
                Swal.fire({
                    icon: type || 'info',
                    title: title || 'Notification',
                    text: message,
                    toast: options?.toast !== false,
                    position: options?.position || 'top-end',
                    showConfirmButton: options?.showConfirmButton || false,
                    timer: options?.timer || 3000,
                    ...options
                });
            } catch (e) {
                this.logger.error('Alert error:', e);
            }
        });

        window.showAlert = (type, title, message, options) => {
            document.dispatchEvent(new CustomEvent('app-alert', {
                detail: { type, title, message, options }
            }));
        };
    }

    setupErrorHandling() {
        // Global error handler
        window.addEventListener('error', (event) => {
            this.logger.error('Global Error:', event.error);
            
            if (this.isAuthError(event.error)) {
                this.handleAuthError(event.error);
                return;
            }
            
            showError(
                'Application Error',
                event.message || 'An unexpected error occurred',
                { timer: 5000 }
            );
        });

        // Unhandled promise rejections
        window.addEventListener('unhandledrejection', (event) => {
            const error = event.reason;
            this.logger.error('Unhandled Rejection:', error);
            
            if (error?.status === 401) {
                this.handleAuthError(new Error('Session expired'));
                return;
            }
            
            if (error?.status === 429) {
                this.handleRateLimitError();
                return;
            }
            
            showError(
                'Async Error',
                error?.message || 'An async operation failed'
            );
        });

        this.setupNetworkErrorHandling();
    }

    isAuthError(error) {
        return error?.message?.includes('authentication') || 
               error?.message?.includes('token') ||
               error?.status === 401;
    }

    setupAuthMonitoring() {
        // Periodic auth checks
        this.authCheckInterval = setInterval(() => {
            this.checkAuth().catch(error => {
                this.logger.warn('Auth monitoring error:', error);
            });
        }, 5 * 60 * 1000); // 5 minutes

        // Check auth when tab becomes visible
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.checkAuth().catch(error => {
                    this.logger.warn('Visibility change auth check error:', error);
                });
            }
        });
    }

    async initWebSocket() {
        try {
            const token = await this.authService.waitForToken();
            if (!token) {
                throw new Error('No valid token available');
            }
        
            this.socketManager = new SocketManager({
                token,
                authService: this.authService,
                logger: this.logger
            });
        
            await this.socketManager.connect();
        } catch (error) {
            this.logger.error('WebSocket initialization failed:', error);
            // Don't retry if it's an auth error
            if (error.message.includes('auth') || error.message.includes('token')) {
                return;
            }
            // Retry logic here...
        }
    }

    handleRateLimitError() {
        const retryAfter = 60; // seconds
        showError(
            'Too Many Requests',
            `Please wait ${retryAfter} seconds before trying again`,
            { timer: 5000 }
        );
    }

    toggleButtonState(button, loading) {
        if (!button) return;
        
        button.disabled = loading;
        const spinner = button.querySelector('.spinner');
        if (spinner) {
            spinner.style.display = loading ? 'inline-block' : 'none';
        }
        
        const buttonText = button.querySelector('.button-text');
        if (buttonText) {
            buttonText.style.visibility = loading ? 'hidden' : 'visible';
        }
    }

    initModules() {
        document.addEventListener('app-ready', () => {
            try {
                this.playerControls.init();
                document.dispatchEvent(new Event('app-modules-ready'));
            } catch (error) {
                this.logger.error('Module initialization error:', error);
            }
        });
    }

    setupNetworkErrorHandling() {
        window.addEventListener('offline', () => {
            showError(
                'Connection Lost',
                'You are currently offline. Some features may not work.',
                { timer: false }
            );
            
            if (this.socketManager) {
                this.socketManager.disconnect();
            }
        });

        window.addEventListener('online', () => {
            showAlert(
                'success',
                'Connection Restored',
                'You are back online'
            );
            
            if (!window.App.state.socketConnected && this.socketManager) {
                this.initWebSocket();
            }
        });
    }

    handleAuthError(error) {
        this.logger.warn('Handling auth error:', error);
        
        // Clear auth data
        this.authService.clearAuth();
        
        // Disconnect sockets
        if (this.socketManager) {
            this.socketManager.disconnect();
        }
        
        // Redirect to login if not already there
        if (!window.location.pathname.includes('/api/auth/login')) {
            const redirectUrl = encodeURIComponent(window.location.pathname + window.location.search);
            window.location.href = `/api/auth/login?redirect=${redirectUrl}`;
        }
    }

    showFatalError(message) {
        try {
            const errorContainer = document.createElement('div');
            errorContainer.id = 'fatal-error-container';
            errorContainer.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                background: #dc3545;
                color: white;
                padding: 15px;
                z-index: 99999;
                text-align: center;
                font-family: sans-serif;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            `;
            
            const errorMessage = document.createElement('div');
            errorMessage.textContent = `Fatal Error: ${message}`;
            errorMessage.style.marginBottom = '10px';
            errorMessage.style.fontWeight = 'bold';
            
            const reloadButton = document.createElement('button');
            reloadButton.textContent = 'Reload Page';
            reloadButton.style.cssText = `
                background: white;
                color: #dc3545;
                border: none;
                padding: 5px 15px;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            `;
            reloadButton.onclick = () => window.location.reload();
            
            errorContainer.appendChild(errorMessage);
            errorContainer.appendChild(reloadButton);
            
            document.body.prepend(errorContainer);
        } catch (e) {
            console.error('Failed to display fatal error:', e);
        }
    }

    cleanup() {
        if (this.authCheckInterval) {
            clearInterval(this.authCheckInterval);
        }
        
        if (this.socketManager) {
            this.socketManager.disconnect();
        }
        
        window.removeEventListener('online', this.handleOnline);
        window.removeEventListener('offline', this.handleOffline);
    }
}

// Initialize and export singleton instance
const appInitializer = new AppInitializer();

// Export for testing purposes
export { AppInitializer };

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => appInitializer.init());
} else {
    appInitializer.init();
}
