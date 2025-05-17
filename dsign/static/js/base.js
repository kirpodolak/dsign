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
            debug: window.location.hostname === 'localhost',
            socketReconnectDelay: 1000,
            maxSocketRetries: 5,
            authCheckInterval: 60000
        }
    };

    // Безопасная инициализация логгера
    const initializeLogger = () => {
        if (!window.App) window.App = {};
        
        // Fallback логгер на случай проблем с инициализацией
        const fallbackLogger = {
            debug: (...args) => window.App?.config?.debug && console.debug('[DEBUG]', ...args),
            info: (...args) => console.log('[INFO]', ...args),
            warn: (...args) => console.warn('[WARN]', ...args),
            error: (message, error, context) => {
                console.error('[ERROR]', message, error || '');
                if (window.App?.Sockets?.isConnected) {
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
            trackEvent: () => {}
        };

        try {
            if (!window.App.Logger) {
                if (typeof window.AppLogger !== 'undefined') {
                    window.App.Logger = new window.AppLogger('AppCore');
                } else {
                    window.App.Logger = fallbackLogger;
                }
            }
        } catch (e) {
            console.error('Logger initialization failed, using fallback', e);
            window.App.Logger = fallbackLogger;
        }
    };

    // Инициализация зависимостей с защитными проверками
    const setupDependencies = () => {
        initializeLogger();

        window.App.Helpers = window.App.Helpers || {
            getCachedData: (key) => {
                try {
                    const item = localStorage.getItem(key);
                    return item ? JSON.parse(item) : null;
                } catch (e) {
                    window.App.Logger?.error('Failed to parse cached data', e, { key });
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
                    window.App.Logger?.error('Failed to cache data', e, { key });
                }
            },
            getToken: () => {
                try {
                    return localStorage.getItem('auth_token');
                } catch (e) {
                    window.App.Logger?.error('Failed to get auth token', e);
                    return null;
                }
            }
        };

        window.App.API = window.App.API || {
            fetch: async (url, options = {}) => {
                const startTime = performance.now();
                const requestId = Math.random().toString(36).substring(2, 9);
                
                try {
                    window.App.Logger?.debug(`API Request [${requestId}]: ${url}`, {
                        method: options.method || 'GET',
                        headers: options.headers
                    });

                    const headers = {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        ...options.headers
                    };

                    const token = window.App.Helpers?.getToken();
                    if (token) {
                        headers['Authorization'] = `Bearer ${token}`;
                    }

                    const response = await fetch(url, {
                        credentials: 'include',
                        ...options,
                        headers
                    });

                    const duration = (performance.now() - startTime).toFixed(2);
                    window.App.Logger?.debug(`API Response [${requestId}]: ${response.status} (${duration}ms)`, {
                        status: response.status,
                        url
                    });

                    if (response.status === 401) {
                        window.App.Logger?.warn('Authentication expired', { url });
                        window.App.Base?.handleUnauthorized();
                        throw new Error('Authentication required');
                    }

                    if (!response.ok) {
                        const errorData = await response.json().catch(() => ({}));
                        throw new Error(errorData.message || `HTTP ${response.status}`);
                    }

                    return response;
                } catch (error) {
                    window.App.Logger?.error(`API Request [${requestId}] failed:`, error, {
                        url,
                        method: options.method || 'GET'
                    });
                    throw error;
                }
            }
        };

        window.App.Alerts = window.App.Alerts || {
            showAlert: (type, title, message) => {
                window.App.Logger?.info(`Alert: ${title}`, { type, message });
            },
            showError: (title, message, error) => {
                window.App.Logger?.error(`Error Alert: ${title}`, error, { message });
            }
        };
    };

    // Обработка аутентификации
    const handleAuthFlow = async () => {
        if (window.App?.isNavigationInProgress) {
            window.App.Logger?.debug('Navigation already in progress');
            return true;
        }

        const isLoginPage = window.location.pathname.includes('/auth/login');
        window.App.Logger?.debug(`Auth flow check: isLoginPage=${isLoginPage}`);
        
        try {
            const token = window.App.Helpers?.getToken();
            if (!token && !isLoginPage) {
                window.App.Logger?.warn('No token available - redirecting to login');
                window.App.isNavigationInProgress = true;
                window.App.Auth?.handleUnauthorized();
                return false;
            }

            // Для страницы логина не проверяем аутентификацию
            if (isLoginPage) return true;

            const isAuthenticated = await window.App.Auth?.checkAuth();
            window.App.Logger?.debug(`Auth status: authenticated=${isAuthenticated}`);

            if (!isAuthenticated) {
                window.App.Logger?.warn('Unauthorized access - redirecting to login');
                window.App.isNavigationInProgress = true;
                window.App.Auth?.handleUnauthorized();
                return false;
            }

            return true;
        } catch (error) {
            window.App.Logger?.error('Auth flow error:', error);
            return false;
        }
    };

    // Инициализация WebSocket
    const initializeWebSockets = () => {
        window.App.Core?.onReady(async () => {
            if (!window.App?.Sockets) {
                try {
                    // Ждем действительного токена перед подключением
                    const token = await window.App.Auth?.waitForToken();
                    if (!token) {
                        window.App.Logger?.warn('WebSocket initialization aborted - no valid token');
                        return;
                    }

                    window.App.Logger?.debug('Initializing WebSocket connection');
                    window.App.Sockets = new WebSocketManager({
                        onError: (error) => {
                            window.App.Logger?.error('WebSocket error:', error);
                        },
                        getToken: () => window.App.Helpers?.getToken(),
                        handleRetry: () => {
                            window.App.Logger?.debug('Attempting to reconnect WebSocket');
                            setTimeout(initializeWebSockets, window.App.config?.socketReconnectDelay || 1000);
                        }
                    });
                } catch (error) {
                    window.App.Logger?.error('Failed to initialize WebSocket:', error);
                }
            }
        });
    };

    // Основная инициализация приложения
    const initializeApp = async () => {
        window.App.Logger?.info('Starting application initialization');

        try {
            const shouldContinue = await handleAuthFlow();
            if (!shouldContinue) {
                window.App.Logger?.debug('Auth flow interrupted');
                return;
            }

            // Задержка перед инициализацией WebSocket для гарантированной загрузки токена
            setTimeout(() => {
                initializeWebSockets();
            }, 500);

            if (!window.location.pathname.includes('/auth/login')) {
                window.App.Logger?.debug('Setting up periodic auth checks');
                setInterval(async () => {
                    window.App.Logger?.debug('Running periodic auth check');
                    const isAuth = await window.App.Auth?.checkAuth();
                    if (!isAuth) {
                        window.App.Logger?.warn('Periodic check failed - unauthorized');
                        window.App.Auth?.handleUnauthorized();
                    }
                }, window.App.config?.authCheckInterval || 60000);
            }

            window.App.Core.initialized = true;
            window.App.Logger?.info('App core initialized, executing ready callbacks');
            
            window.App.Core.onReadyCallbacks.forEach(cb => {
                try {
                    cb();
                } catch (error) {
                    window.App.Logger?.error('Error in ready callback:', error);
                }
            });
            
            window.App.Logger?.info('Application initialized successfully');
        } catch (error) {
            window.App.Logger?.error('Application initialization failed:', error);
            window.App.Alerts?.showError('Initialization Error', 'Failed to start application', error);
        }
    };

    // Безопасный старт приложения
    const startApp = () => {
        try {
            setupDependencies();
            
            // Добавляем задержку для стабилизации состояния
            setTimeout(() => {
                window.App.Logger?.debug('Setting up application dependencies');
                
                if (document.readyState === 'complete') {
                    window.App.Logger?.debug('DOM already loaded, starting immediately');
                    initializeApp().catch(err => {
                        window.App.Logger?.error('Startup error:', err);
                    });
                } else {
                    document.addEventListener('DOMContentLoaded', () => {
                        window.App.Logger?.debug('DOM fully loaded, starting app');
                        initializeApp().catch(err => {
                            window.App.Logger?.error('Startup error:', err);
                        });
                    });
                }
            }, 100);
        } catch (error) {
            console.error('Critical startup error:', error);
        }
    };

    // Публичный API с защитными проверками
    window.App.Base = {
        checkAuth: () => window.App.Auth?.checkAuth(),
        refreshAuth: () => window.App.Auth?.clearAuth(),
        handleUnauthorized: () => window.App.Auth?.handleUnauthorized(),
        getConfig: () => ({ ...(window.App?.config || {}) }),
        setDebugMode: (enabled) => { 
            if (window.App?.config) {
                window.App.config.debug = enabled;
                window.App.Logger?.debug(`Debug mode ${enabled ? 'enabled' : 'disabled'}`);
            }
        },
        trackEvent: (eventData) => {
            if (window.App.Logger?.trackEvent) {
                window.App.Logger.trackEvent(eventData);
            }
        }
    };

    // Глобальные обработчики ошибок с защитными проверками
    window.addEventListener('error', (event) => {
        window.App.Logger?.error('Unhandled error:', event.error, {
            message: event.message,
            source: event.filename,
            line: event.lineno,
            column: event.colno
        });
    });

    window.addEventListener('unhandledrejection', (event) => {
        window.App.Logger?.error('Unhandled promise rejection:', event.reason, {
            promise: event.promise
        });
    });

    // Запуск приложения с защитной проверкой
    if (window.App?.Logger) {
        window.App.Logger.debug('Starting application bootstrap');
    } else {
        console.log('Starting application bootstrap');
    }
    startApp();
})();
