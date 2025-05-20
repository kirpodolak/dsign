/**
 * Application Initialization Module
 * Handles core application setup, authentication, and error handling
 */

class AppInitializer {
    static retryCount = 0;
    static maxRetryCount = 3;
    static retryDelay = 2000;

    static async init() {
        try {
            // Check authentication first
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
            console.error('Initialization error:', error);
            this.showFatalError('Application initialization failed');
        }
    }

    static async checkAuth() {
        try {
            // Get token from storage
            const token = localStorage.getItem('authToken') || 
                         this.getCookie('authToken');
            
            const isLoginPage = window.location.pathname.includes('/auth/login');
            
            if (!token && !isLoginPage) {
                console.warn('[Auth] No token found, redirecting to login');
                const redirectUrl = encodeURIComponent(
                    window.location.pathname + window.location.search
                );
                window.location.href = `/auth/login?redirect=${redirectUrl}`;
                return false;
            }

            // Verify token with server if not on login page
            if (!isLoginPage) {
                const response = await fetch('/auth/api/check-auth', {
                    credentials: 'include'
                });
                
                if (response.status === 429) {
                    this.handleRateLimitError();
                    return false;
                }
                
                if (response.status === 401) {
                    this.handleAuthError(new Error('Session expired'));
                    return false;
                }

                const isValid = await this.verifyToken(token);
                if (!isValid) {
                    this.handleAuthError(new Error('Invalid token'));
                    return false;
                }
            }
            
            return true;

        } catch (error) {
            console.error('Auth check error:', error);
            this.handleAuthError(error);
            return false;
        }
    }

    static handleRateLimitError() {
        const alertMessage = 'Too many requests. Please wait before trying again.';
        
        if (typeof Swal !== 'undefined') {
            Swal.fire({
                icon: 'error',
                title: 'Rate Limit Exceeded',
                text: alertMessage,
                timer: 5000
            });
        } else {
            console.error(alertMessage);
            window.location.href = '/auth/login?error=rate_limit';
        }
        
        const retryDelay = Math.min(
            Math.pow(2, this.retryCount) * 1000,
            30000
        );
        
        setTimeout(() => {
            this.retryCount++;
            this.checkAuth();
        }, retryDelay);
    }

    static async verifyToken(token) {
        try {
            const response = await fetch('/auth/api/verify-token', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                }
            });

            return response.ok;
        } catch (error) {
            console.error('Token verification failed:', error);
            return false;
        }
    }

    static getCookie(name) {
        const match = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
        return match ? decodeURIComponent(match[2]) : null;
    }

    static async setupLoader() {
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

    static setupAlerts() {
        if (typeof Swal === 'undefined') {
            console.warn('SweetAlert2 not available, using console fallback');
            window.showAlert = (type, title, message) => {
                console.log(`[${type}] ${title}: ${message}`);
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
                console.error('Alert error:', e);
            }
        });

        window.showAlert = (type, title, message, options) => {
            document.dispatchEvent(new CustomEvent('app-alert', {
                detail: { type, title, message, options }
            }));
        };
    }

    static setupErrorHandling() {
        window.addEventListener('error', (event) => {
            console.error('[Global Error]', event.error);
            
            if (this.isAuthError(event.error)) {
                this.handleAuthError(event.error);
                return;
            }
            
            this.showError(
                'Application Error',
                event.message || 'An unexpected error occurred',
                { timer: 5000 }
            );
        });

        window.addEventListener('unhandledrejection', (event) => {
            console.error('[Unhandled Rejection]', event.reason);
            
            if (event.reason?.status === 401) {
                this.handleAuthError(new Error('Session expired'));
                return;
            }
            
            if (event.reason?.status === 429) {
                this.handleRateLimitError();
                return;
            }
            
            this.showError(
                'Async Error',
                event.reason?.message || 'An async operation failed'
            );
        });

        this.setupNetworkErrorHandling();
    }

    static isAuthError(error) {
        return error?.message?.includes('authentication') || 
               error?.message?.includes('token') ||
               error?.status === 401;
    }

    static setupAuthMonitoring() {
        setInterval(() => {
            this.checkAuthStatus().catch(console.warn);
        }, 300000);

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.checkAuthStatus().catch(console.warn);
            }
        });
    }

    static async checkAuthStatus() {
        try {
            const response = await fetch('/auth/api/check-auth', {
                credentials: 'include'
            });

            if (response.status === 401) {
                this.handleAuthError(new Error('Session expired'));
            }
            
            if (response.status === 429) {
                this.handleRateLimitError();
            }
        } catch (error) {
            console.warn('Auth check failed:', error);
        }
    }

    static async initWebSocket() {
        if (typeof io === 'undefined') {
            console.warn('Socket.io not available');
            return;
        }

        try {
            // Use AuthService to get complete socket connection data
            const { token, socketUrl, expiresIn } = await window.App.Auth.getSocketToken();
            if (!token) throw new Error('No socket token available');

            // Initialize socket connection with enhanced options
            const socket = socketUrl ? io(socketUrl, {
                auth: { token },
                reconnection: true,
                reconnectionAttempts: 5,
                reconnectionDelay: 1000,
                reconnectionDelayMax: 5000,
                timeout: 10000,
                transports: ['websocket'],
                upgrade: false
            }) : io({
                auth: { token },
                reconnection: true,
                reconnectionAttempts: 5,
                reconnectionDelay: 1000,
                reconnectionDelayMax: 5000,
                timeout: 10000,
                transports: ['websocket']
            });

            // Enhanced socket error handling
            socket.on('connect_error', (err) => {
                console.error('Socket connection error:', err);
                if (err.message.includes('auth') || err.message.includes('token')) {
                    this.handleAuthError(err);
                }
            });

            socket.on('disconnect', (reason) => {
                console.log('Socket disconnected:', reason);
                if (reason === 'io server disconnect') {
                    this.handleAuthError(new Error('Server disconnected'));
                }
            });

            socket.on('connect', () => {
                console.log('Socket connected successfully');
                this.retryCount = 0; // Reset retry counter on successful connection
            });

            // Store socket globally
            window.appSocket = socket;

        } catch (error) {
            console.error('Socket initialization failed:', error);
            
            // Implement retry logic with exponential backoff
            if (this.retryCount < this.maxRetryCount && !error.message.includes('auth')) {
                const delay = this.retryDelay * Math.pow(2, this.retryCount);
                this.retryCount++;
                console.warn(`Retrying WebSocket connection in ${delay}ms (attempt ${this.retryCount}/${this.maxRetryCount})`);
                setTimeout(() => this.initWebSocket(), delay);
            } else if (error.message.includes('auth')) {
                this.handleAuthError(error);
            }
        }
    }

    static initModules() {
        document.addEventListener('app-ready', () => {
            if (window.PlayerControls) {
                PlayerControls.init();
            }
        });
    }

    static setupNetworkErrorHandling() {
        window.addEventListener('offline', () => {
            this.showError(
                'Connection Lost',
                'You are currently offline. Some features may not work.',
                { timer: false }
            );
        });

        window.addEventListener('online', () => {
            this.showAlert(
                'success',
                'Connection Restored',
                'You are back online'
            );
        });
    }

    static handleAuthError(error) {
        console.error('[Auth Error]', error);
        
        localStorage.removeItem('authToken');
        this.deleteCookie('authToken');
        
        this.showError(
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

    static showError(title, message, options = {}) {
        document.dispatchEvent(new CustomEvent('app-alert', {
            detail: {
                type: 'error',
                title,
                message,
                options: {
                    timer: 3000,
                    ...options
                }
            }
        }));
    }

    static showFatalError(message) {
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

    static deleteCookie(name) {
        document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=${window.location.hostname};`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    try {
        AppInitializer.init();
    } catch (error) {
        console.error('Initialization failed:', error);
        AppInitializer.showFatalError('Failed to initialize application');
    }
});

if (typeof module !== 'undefined' && module.exports) {
    module.exports = AppInitializer;
}
