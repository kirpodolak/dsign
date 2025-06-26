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
        this.socketManager = new SocketManager({
            authService: this.authService,
            logger: this.logger
        });
        this.playerControls = new PlayerControls({
            API: { fetch: fetchAPI },
            Alerts: { showAlert, showError },
            Helpers: {
                toggleButtonState: this.toggleButtonState.bind(this)
            }
        });

        this.initPromise = null;
        this.socketInitialized = false;
    }

    async init() {
        if (this.initPromise) {
            return this.initPromise;
        }

        this.initPromise = (async () => {
            try {
                this.logger.debug('Starting application initialization');
                
                this.initGlobalState();

                if (this.preventRedirectLoops()) {
                    return;
                }

                await this.authService.checkAuth();

                await Promise.all([
                    this.setupLoader(),
                    this.setupAlerts(),
                    this.setupErrorHandling(),
                    this.setupAuthMonitoring()
                ]);

                document.addEventListener('auth:status_changed', (event) => {
                    const isAuthenticated = event.detail;
                    if (isAuthenticated && !this.socketInitialized) {
                        this.initWebSocket().catch(error => {
                            this.logger.error('WebSocket init after auth change failed:', error);
                        });
                    }
                });

                if (this.authService.getToken()) {
                    await this.initWebSocket();
                }

                this.initModules();

                this.logger.debug('Application initialization completed');
                window.App.state.initialized = true;
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
            retryDelays: [2000, 5000, 10000]
        };

        if (this.debugMode) {
            this.logger.debug('Global state initialized', window.App.state);
        }
    }

    preventRedirectLoops() {
        const isLoginPage = window.location.pathname.includes('/api/auth/login');
        const hasRedirectLoop = window.location.search.includes('redirect_loop=true');
    
        if (isLoginPage) {
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
        this.authService.startAuthStatusChecker();

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.authService.checkAuth().catch(error => {
                    this.logger.warn('Visibility change auth check error:', error);
                });
            }
        });

        if (typeof window !== 'undefined') {
            window.App.trigger = window.App.trigger || function(event, data) {
                document.dispatchEvent(new CustomEvent(event, { detail: data }));
            };
        }
    }

    async initWebSocket() {
        try {
            await this.socketManager.connect();
            this.socketInitialized = true;
            window.App.state.socketConnected = true;
            this.logger.debug('WebSocket initialized via SocketManager');
        } catch (error) {
            this.logger.error('WebSocket initialization failed:', error);
            
            const delay = this.calculateReconnectDelay();
            this.logger.debug(`Will retry in ${delay}ms...`);
            setTimeout(() => this.initWebSocket(), delay);
        }
    }

    calculateReconnectDelay() {
        this.retryCount = this.retryCount || 0;
        const baseDelay = 1000;
        const maxDelay = 30000;
        
        const delay = Math.min(
            baseDelay * Math.pow(2, this.retryCount), 
            maxDelay
        );
        
        const jitter = delay * 0.5 * Math.random();
        this.retryCount++;
        
        return Math.min(delay + jitter, maxDelay);
    }

    handleAuthError(errorData) {
        this.logger.error('Authentication error:', errorData);
        
        fetch('/api/auth/refresh-token')
            .then(response => response.json())
            .then(data => {
                if (data.token) {
                    this.logger.debug('Token refreshed, reconnecting...');
                    this.initWebSocket();
                } else {
                    this.logger.error('Failed to refresh token');
                    window.location.href = '/login?reason=session_expired';
                }
            })
            .catch(err => {
                this.logger.error('Token refresh failed:', err);
                window.location.href = '/login?reason=auth_error';
            });
    }

    handleRateLimitError() {
        const retryAfter = 60;
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
            
            this.socketManager.disconnect();
            window.App.state.socketConnected = false;
        });

        window.addEventListener('online', () => {
            showAlert(
                'success',
                'Connection Restored',
                'You are back online'
            );
            
            if (!window.App.state.socketConnected && this.authService.status) {
                this.initWebSocket().catch(error => {
                    this.logger.error('WebSocket reinit after online failed:', error);
                });
            }
        });
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
        this.socketManager.disconnect();
        this.socketInitialized = false;
        window.App.state.socketConnected = false;
        
        window.removeEventListener('online', this.handleOnline);
        window.removeEventListener('offline', this.handleOffline);
    }
}

const appInitializer = new AppInitializer();

export { AppInitializer };

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => appInitializer.init());
} else {
    appInitializer.init();
}
