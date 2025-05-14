(function() {
    // Глобальный флаг инициализации
    if (window.__DSIGN_INITIALIZED__) return;
    window.__DSIGN_INITIALIZED__ = true;

    // Инициализация базового объекта App
    window.App = window.App || {
        Core: {
            initialized: false,
            onReadyCallbacks: [],
            onReady: function(callback) {
                if (this.initialized) {
                    callback();
                } else {
                    this.onReadyCallbacks.push(callback);
                }
            }
        },
        config: {
            debug: process.env.NODE_ENV !== 'production',
            socketReconnectDelay: 1000,
            maxSocketRetries: 5,
            authCheckInterval: 60000
        }
    };

    // Инициализация логгера (интеграция с logging.js)
    const initializeLogger = () => {
        if (!window.App.Logger && typeof window.AppLogger !== 'undefined') {
            window.App.Logger = new window.AppLogger('AppCore');
        } else {
            window.App.Logger = window.App.Logger || {
                debug: (...args) => window.App.config.debug && console.debug('[DEBUG]', ...args),
                info: (...args) => console.log('[INFO]', ...args),
                warn: (...args) => console.warn('[WARN]', ...args),
                error: (message, error, context) => {
                    console.error('[ERROR]', message, error || '');
                    if (window.App.Sockets?.isConnected) {
                        window.App.Sockets.emit('client_error', {
                            level: 'error',
                            message,
                            error: error?.toString(),
                            stack: error?.stack,
                            context,
                            url: window.location.href
                        });
                    }
                },
                trackEvent: (eventData) => {
                    if (window.App.Sockets?.isConnected) {
                        window.App.Sockets.emit('client_event', eventData);
                    }
                }
            };
        }
    };

    // Минимальные реализации зависимостей
    const setupDependencies = () => {
        initializeLogger();

        window.App.Helpers = window.App.Helpers || {
            getCachedData: (key) => {
                try {
                    const item = localStorage.getItem(key);
                    return item ? JSON.parse(item) : null;
                } catch (e) {
                    window.App.Logger.error('Failed to parse cached data', e, { key });
                    return null;
                }
            },
            setCachedData: (key, value, ttl) => {
                try {
                    const item = {
                        value: value,
                        expires: ttl ? Date.now() + ttl : null
                    };
                    localStorage.setItem(key, JSON.stringify(item));
                } catch (e) {
                    window.App.Logger.error('Failed to cache data', e, { key });
                }
            },
            getToken: () => {
                try {
                    return localStorage.getItem('auth_token');
                } catch (e) {
                    window.App.Logger.error('Failed to get auth token', e);
                    return null;
                }
            }
        };

        window.App.API = window.App.API || {
            fetch: async (url, options = {}) => {
                const startTime = performance.now();
                const requestId = Math.random().toString(36).substring(2, 9);
                
                try {
                    window.App.Logger.debug(`API Request [${requestId}]: ${url}`, {
                        method: options.method || 'GET',
                        headers: options.headers
                    });

                    const headers = {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        ...options.headers
                    };

                    const token = window.App.Helpers.getToken();
                    if (token) {
                        headers['Authorization'] = `Bearer ${token}`;
                    }

                    const response = await fetch(url, {
                        credentials: 'include',
                        ...options,
                        headers
                    });

                    const duration = (performance.now() - startTime).toFixed(2);
                    window.App.Logger.debug(`API Response [${requestId}]: ${response.status} (${duration}ms)`, {
                        status: response.status,
                        url
                    });

                    if (response.status === 401) {
                        window.App.Logger.warn('Authentication expired', { url });
                        window.App.Base.handleUnauthorized();
                        throw new Error('Authentication required');
                    }

                    if (!response.ok) {
                        const errorData = await response.json().catch(() => ({}));
                        throw new Error(errorData.message || `HTTP ${response.status}`);
                    }

                    return response;
                } catch (error) {
                    window.App.Logger.error(`API Request [${requestId}] failed:`, error, {
                        url,
                        method: options.method || 'GET'
                    });
                    throw error;
                }
            }
        };

        window.App.Alerts = window.App.Alerts || {
            showAlert: (type, title, message) => {
                window.App.Logger.info(`Alert: ${title}`, { type, message });
                // UI integration would go here
            },
            showError: (title, message, error) => {
                window.App.Logger.error(`Error Alert: ${title}`, error, { message });
                // UI integration would go here
            }
        };
    };

    // Сервис аутентификации
    const authService = {
        async checkAuth() {
            try {
                window.App.Logger.debug('Checking authentication status');
                const response = await window.App.API.fetch('/auth/api/check-auth');
                const data = await response.json();
                
                if (data?.authenticated && data?.token) {
                    window.App.Logger.debug('User authenticated');
                    localStorage.setItem('auth_token', data.token);
                    return true;
                }
                
                window.App.Logger.debug('User not authenticated');
                this.clearAuth();
                return false;
            } catch (error) {
                window.App.Logger.error('Authentication check failed', error);
                this.clearAuth();
                return false;
            }
        },

        clearAuth() {
            window.App.Logger.debug('Clearing authentication data');
            localStorage.removeItem('auth_token');
            window.App.Helpers.setCachedData('auth_status', { value: false });
            
            if (window.App.Sockets) {
                window.App.Sockets.disconnect();
            }
        },

        handleUnauthorized() {
            window.App.Logger.warn('Handling unauthorized access');
            this.clearAuth();
            const redirect = encodeURIComponent(window.location.pathname + window.location.search);
            window.location.href = `/auth/login?redirect=${redirect}`;
        }
    };

    // Обработка аутентификации
    const handleAuthFlow = async () => {
        if (window.App.isNavigationInProgress) {
            window.App.Logger.debug('Navigation already in progress');
            return true;
        }
    
        const isLoginPage = window.location.pathname.includes('/auth/login');
        window.App.Logger.debug(`Auth flow check: isLoginPage=${isLoginPage}`);
        
        const isAuthenticated = await authService.checkAuth();
        window.App.Logger.debug(`Auth status: authenticated=${isAuthenticated}`);

        if (!isAuthenticated && !isLoginPage) {
            window.App.Logger.warn('Unauthorized access - redirecting to login');
            window.App.isNavigationInProgress = true;
            authService.handleUnauthorized();
            return false;
        }

        if (isAuthenticated && isLoginPage) {
            window.App.Logger.debug('Authenticated on login page - redirecting to home');
            window.App.isNavigationInProgress = true;
            const redirectTo = new URLSearchParams(window.location.search).get('redirect') || '/';
            window.location.href = redirectTo;
            return false;
        }

        return true;
    };

    // Инициализация WebSocket
    const initializeWebSockets = () => {
        window.App.Core.onReady(() => {
            if (!window.App.Sockets) {
                window.App.Logger.debug('Initializing WebSocket connection');
                // Реальная инициализация происходит в sockets.js
            }
        });
    };

    // Основная инициализация приложения
    const initializeApp = async () => {
        window.App.Logger.info('Starting application initialization');

        try {
            // Проверка аутентификации
            const shouldContinue = await handleAuthFlow();
            if (!shouldContinue) {
                window.App.Logger.debug('Auth flow interrupted');
                return;
            }

            // Инициализация WebSocket
            initializeWebSockets();

            // Периодическая проверка аутентификации
            if (!window.location.pathname.includes('/auth/login')) {
                window.App.Logger.debug('Setting up periodic auth checks');
                setInterval(async () => {
                    window.App.Logger.debug('Running periodic auth check');
                    const isAuth = await authService.checkAuth();
                    if (!isAuth) {
                        window.App.Logger.warn('Periodic check failed - unauthorized');
                        authService.handleUnauthorized();
                    }
                }, window.App.config.authCheckInterval);
            }

            // Помечаем приложение как инициализированное
            window.App.Core.initialized = true;
            window.App.Logger.info('App core initialized, executing ready callbacks');
            
            window.App.Core.onReadyCallbacks.forEach(cb => {
                try {
                    cb();
                } catch (error) {
                    window.App.Logger.error('Error in ready callback:', error);
                }
            });
            
            window.App.Logger.info('Application initialized successfully');
        } catch (error) {
            window.App.Logger.error('Application initialization failed:', error);
            window.App.Alerts.showError('Initialization Error', 'Failed to start application', error);
        }
    };

    // Старт приложения
    const startApp = () => {
        try {
            window.App.Logger.debug('Setting up application dependencies');
            setupDependencies();
            
            if (document.readyState === 'complete') {
                window.App.Logger.debug('DOM already loaded, starting immediately');
                initializeApp().catch(err => window.App.Logger.error('Startup error:', err));
            } else {
                window.App.Logger.debug('Waiting for DOM content to load');
                document.addEventListener('DOMContentLoaded', () => {
                    window.App.Logger.debug('DOM content loaded, starting initialization');
                    initializeApp().catch(err => window.App.Logger.error('Startup error:', err));
                });
            }
        } catch (error) {
            console.error('Critical startup error:', error);
        }
    };

    // Публичный API
    window.App.Base = {
        checkAuth: authService.checkAuth.bind(authService),
        refreshAuth: authService.clearAuth.bind(authService),
        handleUnauthorized: authService.handleUnauthorized.bind(authService),
        getConfig: () => ({ ...window.App.config }),
        setDebugMode: (enabled) => { 
            window.App.config.debug = enabled;
            window.App.Logger.debug(`Debug mode ${enabled ? 'enabled' : 'disabled'}`);
        },
        trackEvent: (eventData) => {
            if (window.App.Logger.trackEvent) {
                window.App.Logger.trackEvent(eventData);
            }
        }
    };

    // Глобальный обработчик ошибок
    window.addEventListener('error', (event) => {
        window.App.Logger.error('Unhandled error:', event.error, {
            message: event.message,
            source: event.filename,
            line: event.lineno,
            column: event.colno
        });
    });

    // Обработчик необработанных promise rejections
    window.addEventListener('unhandledrejection', (event) => {
        window.App.Logger.error('Unhandled promise rejection:', event.reason, {
            promise: event.promise
        });
    });

    // Запуск приложения
    window.App.Logger.debug('Starting application bootstrap');
    startApp();
})();
