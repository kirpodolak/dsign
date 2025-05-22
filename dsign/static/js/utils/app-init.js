import { showAlert, showError } from './alerts.js';
import { getToken, getCookie, deleteCookie } from './helpers.js';
import AuthService from './auth.js';
import SocketManager from './sockets.js';
import PlayerControls from './player-controls.js';
import AppLogger from './logging.js';

class AppInitializer {
    constructor() {
        this.retryCount = 0;
        this.maxRetryCount = 3;
        this.retryDelay = 2000;
        this.debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

        this.logger = new AppLogger('AppInitializer');
        this.authService = new AuthService();
        this.socketManager = new SocketManager();
        this.playerControls = new PlayerControls();
    }

    async init() {
        try {
            // Initialize global state first
            this.initGlobalState();

            // Prevent redirect loops before any auth checks
            if (this.preventRedirectLoops()) {
                return;
            }

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
                this.setupAuthMonitoring(),
                this.initWebSocket()
            ]);

            // Initialize other modules
            this.initModules();

        } catch (error) {
            this.logger.error('Initialization error:', error);
            this.showFatalError('Application initialization failed');
        }
    }

    initGlobalState() {
        window.App = window.App || {};
        window.App.state = window.App.state || {
            navigationInProgress: false,
            socketConnected: false,
            initialized: true,
            lastAuthCheck: null
        };

        if (this.debugMode) {
            this.logger.debug('Global state initialized', window.App.state);
        }
    }

    preventRedirectLoops() {
        const isLoginPage = window.location.pathname.includes('/auth/login');
        const hasRedirectLoop = window.location.search.includes('redirect=%2Fauth%2Flogin');
        
        if (isLoginPage && hasRedirectLoop) {
            this.logger.warn('Redirect loop detected, resetting to login');
            window.location.href = '/auth/login';
            return true;
        }
        return false;
    }

    async checkAuth() {
        try {
            if (window.App.state?.navigationInProgress) {
                if (this.debugMode) {
                    this.logger.debug('Navigation already in progress');
                }
                return false;
            }

            // Get token from storage
            const token = getToken() || 
                         localStorage.getItem('authToken') || 
                         getCookie('authToken');
            
            const isLoginPage = window.location.pathname.includes('/auth/login');
            
            if (!token && !isLoginPage) {
                this.logger.warn('No token found, redirecting to login');
                window.App.state.navigationInProgress = true;
                const redirectUrl = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = `/auth/login?redirect=${redirectUrl}`;
                return false;
            }

            // Verify token with server if not on login page
            if (!isLoginPage) {
                const isValid = await this.authService.verifyToken(token);
                if (!isValid) {
                    this.handleAuthError(new Error('Invalid token'));
                    return false;
                }

                window.App.state.lastAuthCheck = Date.now();
            }
            
            return true;

        } catch (error) {
            this.logger.error('Auth check error:', error);
            this.handleAuthError(error);
            return false;
        }
    }

    handleRateLimitError() {
        const alertMessage = 'Too many requests. Please wait before trying again.';
        
        showError('Rate Limit Exceeded', alertMessage, { timer: 5000 });
        
        const retryDelay = Math.min(
            Math.pow(2, this.retryCount) * 1000,
            30000
        );
        
        setTimeout(() => {
            this.retryCount++;
            this.checkAuth();
        }, retryDelay);
    }

    async setupLoader() {
        return new Promise((resolve) => {
            setTimeout(() => {
                const loader = document.getElementById('page-loader');
                if (loader) {
                    loader.style.opacity = '0';
                    setTimeout(() => {
                        loader.style.display = 'none';
                        document.dispatchEvent(new CustomEvent('app-ready', {
                            detail: {
                                authenticated: !!localStorage.getItem('authToken'),
                                timestamp: Date.now()
                            }
                        }));
                        resolve();
                    }, 300);
                } else {
                    resolve();
                }
            }, 500);
        });
    }

    setupAlerts() {
        if (typeof Swal === 'undefined') {
            this.logger.warn('SweetAlert2 not available, using console fallback');
            window.showAlert = (type, title, message) => {
                this.logger.log(`[${type}] ${title}: ${message}`);
            };
            return;
        }

        document.addEventListener('app-alert', (event) => {
            try {
                const { type, title, message, options } = event.detail;
                const defaultOptions = {
                    icon: type || 'info',
                    title: title || 'Notification',
                    text: message,
                    toast: true,
                    position: 'top-end',
                    showConfirmButton: false,
                    timer: 3000
                };

                Swal.fire({ ...defaultOptions, ...options });
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
            this.logger.error('Unhandled Rejection:', event.reason);
            
            if (event.reason?.status === 401) {
                this.handleAuthError(new Error('Session expired'));
                return;
            }
            
            if (event.reason?.status === 429) {
                this.handleRateLimitError();
                return;
            }
            
            showError(
                'Async Error',
                event.reason?.message || 'An async operation failed'
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
        setInterval(() => {
            this.checkAuthStatus().catch(error => {
                this.logger.warn('Auth monitoring error:', error);
            });
        }, 300000); // 5 minutes

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.checkAuthStatus().catch(error => {
                    this.logger.warn('Visibility change auth check error:', error);
                });
            }
        });
    }

    async checkAuthStatus() {
        try {
            const token = localStorage.getItem('authToken') || getCookie('authToken');
            if (!token) {
                this.handleAuthError(new Error('No token found'));
                return;
            }

            const isValid = await this.authService.verifyToken(token);
            if (!isValid) {
                this.handleAuthError(new Error('Session expired'));
            }

            window.App.state.lastAuthCheck = Date.now();
        } catch (error) {
            this.logger.warn('Auth check failed:', error);
        }
    }

    async initWebSocket() {
        try {
            await this.socketManager.connect();
            window.App.state.socketConnected = true;
        } catch (error) {
            this.logger.error('Socket initialization failed:', error);
            
            if (this.retryCount < this.maxRetryCount && !error.message.includes('auth')) {
                const delay = this.retryDelay * Math.pow(2, this.retryCount);
                this.retryCount++;
                this.logger.warn(`Retrying WebSocket connection in ${delay}ms (attempt ${this.retryCount}/${this.maxRetryCount})`);
                setTimeout(() => this.initWebSocket(), delay);
            } else if (error.message.includes('auth')) {
                this.handleAuthError(error);
            }
        }
    }

    initModules() {
        document.addEventListener('app-ready', () => {
            this.playerControls.init();
        });
    }

    setupNetworkErrorHandling() {
        window.addEventListener('offline', () => {
            showError(
                'Connection Lost',
                'You are currently offline. Some features may not work.',
                { timer: false }
            );
        });

        window.addEventListener('online', () => {
            showAlert(
                'success',
                'Connection Restored',
                'You are back online'
            );
        });
    }

    handleAuthError(error) {
        this.logger.error('Auth Error:', error);
        
        localStorage.removeItem('authToken');
        deleteCookie('authToken');
        
        if (window.appSocket) {
            window.appSocket.disconnect();
        }

        showError(
            'Session Expired',
            'Your session has expired. Please log in again.',
            {
                timer: 5000,
                onClose: () => {
                    const redirectUrl = encodeURIComponent(window.location.pathname);
                    window.location.href = `/auth/login?redirect=${redirectUrl}`;
                }
            }
        );
    }

    showFatalError(message) {
        const errorDiv = document.createElement('div');
        errorDiv.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            background: #ff5252;
            color: white;
            padding: 1rem;
            z-index: 9999;
            text-align: center;
        `;
        errorDiv.textContent = `Fatal Error: ${message}`;
        document.body.prepend(errorDiv);
    }
}

// Экспортируем singleton экземпляр
const appInitializer = new AppInitializer();
export default appInitializer;
