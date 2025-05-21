/**
 * Core Application Initialization Module
 * Handles application bootstrap, authentication, and core services
 */
(function() {
    // Global initialization flag
    if (window.__DSIGN_INITIALIZED__) return;
    window.__DSIGN_INITIALIZED__ = true;

    // Initialize core App object with enhanced structure
    window.App = window.App || {
        Core: {
            initialized: false,
            onReadyCallbacks: [],
            onReady: function(callback) {
                if (this.initialized && typeof callback === 'function') {
                    try {
                        callback();
                    } catch (error) {
                        this.handleError('Ready callback error:', error);
                    }
                } else {
                    this.onReadyCallbacks.push(callback);
                }
            },
            handleError: function(message, error) {
                console.error('[Core]', message, error);
                if (window.App.Logger?.error) {
                    window.App.Logger.error(message, error);
                }
            }
        },
        
        config: {
            debug: window.location.hostname === 'localhost',
            socketReconnectDelay: 1000,
            maxSocketRetries: 5,
            authCheckInterval: 60000,
            apiTimeout: 30000,
            socketEndpoint: '/socket.io'
        },
        
        state: {
            navigationInProgress: false,
            authChecked: false,
            socketInitialized: false
        }
    };

    // Enhanced logger initialization with fallback
    const initializeLogger = () => {
        const fallbackLogger = {
            debug: (...args) => window.App?.config?.debug && console.debug('[DEBUG]', ...args),
            info: (...args) => console.log('[INFO]', ...args),
            warn: (...args) => console.warn('[WARN]', ...args),
            error: (message, error, context = {}) => {
                console.error('[ERROR]', message, error || '');
                if (window.appSocket?.connected) {
                    const errorData = {
                        level: 'error',
                        message,
                        error: error?.toString(),
                        stack: error?.stack,
                        context,
                        url: window.location.href,
                        timestamp: new Date().toISOString()
                    };
                    
                    try {
                        window.appSocket.emit('client_error', errorData);
                    } catch (socketError) {
                        console.error('Failed to send error via socket:', socketError);
                    }
                }
            }
        };

        try {
            window.App.Logger = window.App.Logger || fallbackLogger;
            window.App.Logger.info('Logger initialized');
        } catch (e) {
            console.error('Logger initialization failed, using fallback', e);
            window.App.Logger = fallbackLogger;
        }
    };

    // Enhanced helpers initialization
    const initializeHelpers = () => {
        window.App.Helpers = window.App.Helpers || {
            getCachedData: (key) => {
                try {
                    const item = localStorage.getItem(key);
                    if (!item) return null;
                    
                    const parsed = JSON.parse(item);
                    if (parsed.expires && parsed.expires < Date.now()) {
                        localStorage.removeItem(key);
                        return null;
                    }
                    return parsed.value;
                } catch (e) {
                    window.App.Logger.error('Failed to parse cached data', e, { key });
                    return null;
                }
            },
            
            setCachedData: (key, value, ttl = null) => {
                try {
                    const item = {
                        value,
                        expires: ttl ? Date.now() + ttl : null
                    };
                    localStorage.setItem(key, JSON.stringify(item));
                } catch (e) {
                    window.App.Logger.error('Failed to cache data', e, { key });
                }
            },
            
            getToken: () => {
                try {
                    return localStorage.getItem('authToken') || 
                           document.cookie.replace(/(?:(?:^|.*;\s*)authToken\s*=\s*([^;]*).*$)|^.*$/, '$1');
                } catch (e) {
                    window.App.Logger.error('Failed to get auth token', e);
                    return null;
                }
            },
            
            clearToken: () => {
                try {
                    localStorage.removeItem('authToken');
                    document.cookie = 'authToken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
                } catch (e) {
                    window.App.Logger.error('Failed to clear auth token', e);
                }
            }
        };
    };

    // Enhanced API service with timeout support
    const initializeAPI = () => {
        window.App.API = window.App.API || {
            fetch: async (url, options = {}) => {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 
                    options.timeout || window.App.config.apiTimeout);
                
                const requestId = Math.random().toString(36).substring(2, 9);
                const startTime = performance.now();
                
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
                        signal: controller.signal,
                        ...options,
                        headers
                    });

                    clearTimeout(timeoutId);
                    const duration = (performance.now() - startTime).toFixed(2);
                    
                    window.App.Logger.debug(`API Response [${requestId}]: ${response.status} (${duration}ms)`, {
                        status: response.status,
                        url
                    });

                    if (response.status === 401) {
                        window.App.Logger.warn('Authentication expired', { url });
                        window.App.Auth.handleUnauthorized();
                        throw new Error('Authentication required');
                    }

                    if (response.status === 429) {
                        window.App.Logger.warn('Rate limit exceeded', { url });
                        throw new Error('Too many requests');
                    }

                    if (!response.ok) {
                        const errorData = await response.json().catch(() => ({}));
                        throw new Error(errorData.message || `HTTP ${response.status}`);
                    }

                    return response;
                } catch (error) {
                    clearTimeout(timeoutId);
                    window.App.Logger.error(`API Request [${requestId}] failed:`, error, {
                        url,
                        method: options.method || 'GET'
                    });
                    throw error;
                }
            }
        };
    };

    // Alert system with UI integration
    const initializeAlerts = () => {
        const alertSystem = {
            showAlert: function(type, title, message, options = {}) {
                window.App.Logger.info(`Alert: ${title}`, { type, message });
                
                // Dispatch event for UI components
                const event = new CustomEvent('app-alert', {
                    detail: { type, title, message, ...options }
                });
                document.dispatchEvent(event);
            },
            
            showError: function(title, message, error) {
                window.App.Logger.error(`Error Alert: ${title}`, error, { message });
                this.showAlert('error', title, message || error?.message);
            }
        };

        window.App.Alerts = window.App.Alerts || alertSystem;
        // Bind context to prevent 'this' issues
        window.App.Alerts.showError = alertSystem.showError.bind(alertSystem);
    };

    // Enhanced authentication flow
    const initializeAuth = () => {
        window.App.Auth = window.App.Auth || {
            checkAuth: async () => {
                try {
                    // Initialize state if not exists
                    if (!window.App.state) {
                        window.App.state = {
                            navigationInProgress: false,
                            authChecked: false
                        };
                    }

                    if (window.App.state.navigationInProgress) return false;
                    
                    const token = window.App.Helpers.getToken();
                    if (!token) return false;
                    
                    // Verify token with backend
                    const response = await window.App.API.fetch('/auth/api/check-auth', {
                        method: 'GET'
                    });
                    
                    const data = await response.json();
                    return data?.authenticated || false;
                } catch (error) {
                    window.App.Logger.warn('Auth check failed:', error);
                    return false;
                }
            },
            
            handleUnauthorized: function() {
                // Ensure state exists
                if (!window.App.state) {
                    window.App.state = {
                        navigationInProgress: false,
                        authChecked: false
                    };
                }

                if (window.App.state.navigationInProgress) return;
                
                window.App.state.navigationInProgress = true;
                window.App.Helpers.clearToken();
                
                // Disconnect socket if exists
                if (window.appSocket) {
                    window.appSocket.disconnect();
                    delete window.appSocket;
                }
                
                // Prevent redirect loops
                const currentPath = window.location.pathname;
                if (!currentPath.includes('/auth/login')) {
                    window.App.Logger.warn('Redirecting to login');
                    const redirectUrl = encodeURIComponent(currentPath + window.location.search);
                    window.location.href = `/auth/login?redirect=${redirectUrl}`;
                }
            },
            
            waitForToken: async (maxAttempts = 10, interval = 500) => {
                let attempts = 0;
                
                return new Promise((resolve, reject) => {
                    const checkToken = () => {
                        attempts++;
                        const token = window.App.Helpers.getToken();
                        
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
            },
            
            getSocketToken: async () => {
                try {
                    const response = await window.App.API.fetch('/auth/socket-token');
                    if (!response.ok) {
                        throw new Error('Failed to get socket token');
                    }
                    return await response.json();
                } catch (error) {
                    window.App.Logger.error('Failed to get socket token:', error);
                    throw error;
                }
            }
        };
    };

    // WebSocket initialization with enhanced reconnection logic
    const initializeWebSockets = () => {
        window.App.Core.onReady(async () => {
            if (window.App.state.socketInitialized) return;
            
            try {
                // Check authentication first
                const isAuth = await window.App.Auth.checkAuth();
                if (!isAuth) {
                    window.App.Logger.warn('WebSocket init aborted - not authenticated');
                    return;
                }

                window.App.Logger.debug('Initializing WebSocket connection');
                
                // Get fresh socket token
                const { token, socketUrl } = await window.App.Auth.getSocketToken();
                
                // Initialize socket connection
                const socket = io(socketUrl || window.App.config.socketEndpoint, {
                    auth: { token },
                    reconnection: true,
                    reconnectionAttempts: window.App.config.maxSocketRetries,
                    reconnectionDelay: window.App.config.socketReconnectDelay,
                    transports: ['websocket'],
                    upgrade: false
                });

                // Store socket globally
                window.appSocket = socket;
                window.App.state.socketInitialized = true;

                // Event handlers
                socket.on('connect', () => {
                    window.App.Logger.info('WebSocket connected');
                    window.App.state.socketConnected = true;
                });

                socket.on('disconnect', (reason) => {
                    window.App.Logger.warn('WebSocket disconnected:', reason);
                    window.App.state.socketConnected = false;
                });

                socket.on('connect_error', (error) => {
                    window.App.Logger.error('WebSocket connection error:', error);
                    if (error.message.includes('auth') || error.message.includes('token')) {
                        window.App.Auth.handleUnauthorized();
                    }
                });

                // Global socket interface
                window.App.Sockets = {
                    connect: () => socket.connect(),
                    disconnect: () => socket.disconnect(),
                    isConnected: () => socket.connected,
                    emit: (event, data) => {
                        if (socket.connected) {
                            socket.emit(event, data);
                        } else {
                            window.App.Logger.warn('Socket not connected, cannot emit', event);
                        }
                    }
                };

            } catch (error) {
                window.App.Logger.error('WebSocket initialization failed:', error);
                if (error.message.includes('auth') || error.message.includes('token')) {
                    window.App.Auth.handleUnauthorized();
                }
            }
        });
    };

    // Core application initialization
    const initializeApp = async () => {
        window.App.Logger.info('Starting application initialization');
        
        try {
            // Initialize state if not exists
            if (!window.App.state) {
                window.App.state = {
                    navigationInProgress: false,
                    authChecked: false,
                    socketInitialized: false
                };
            }

            // Check authentication state
            const isLoginPage = window.location.pathname.includes('/auth/login');
            const token = window.App.Helpers.getToken();
            
            if (!token && !isLoginPage) {
                window.App.Logger.warn('No token - redirecting to login');
                window.App.Auth.handleUnauthorized();
                return;
            }

            // Initialize WebSocket with delay
            setTimeout(initializeWebSockets, 500);
            
            // Set up periodic auth checks for non-login pages
            if (!isLoginPage) {
                window.App.Logger.debug('Setting up periodic auth checks');
                
                setInterval(async () => {
                    const isAuth = await window.App.Auth.checkAuth();
                    if (!isAuth) {
                        window.App.Logger.warn('Periodic auth check failed');
                        window.App.Auth.handleUnauthorized();
                    }
                }, window.App.config.authCheckInterval);
            }

            // Mark core as initialized
            window.App.Core.initialized = true;
            window.App.Logger.info('App core initialized');
            
            // Execute ready callbacks
            window.App.Core.onReadyCallbacks.forEach(cb => {
                try {
                    if (typeof cb === 'function') cb();
                } catch (error) {
                    window.App.Core.handleError('Ready callback error:', error);
                }
            });
            
            window.App.Core.onReadyCallbacks = [];
        } catch (error) {
            window.App.Logger.error('App initialization failed:', error);
            if (window.App.Alerts?.showError) {
                window.App.Alerts.showError(
                    'Initialization Error', 
                    'Failed to start application', 
                    error
                );
            }
        }
    };

    // Application bootstrap sequence
    const bootstrap = () => {
        try {
            // Initialize core services
            initializeLogger();
            initializeHelpers();
            initializeAPI();
            initializeAlerts();
            initializeAuth();
            
            // Start application
            if (document.readyState === 'complete') {
                initializeApp();
            } else {
                document.addEventListener('DOMContentLoaded', initializeApp);
            }
        } catch (error) {
            console.error('Bootstrap failed:', error);
            if (window.App.Alerts?.showError) {
                window.App.Alerts.showError(
                    'Bootstrap Error',
                    'Failed to initialize application',
                    error
                );
            }
        }
    };

    // Public API with safety checks
    window.App.Base = {
        checkAuth: () => window.App.Auth?.checkAuth?.(),
        handleUnauthorized: () => window.App.Auth?.handleUnauthorized?.(),
        refreshSession: async () => {
            try {
                const response = await window.App.API.fetch('/auth/api/refresh-token');
                if (response.ok) {
                    const data = await response.json();
                    if (data.token) {
                        window.App.Helpers.setCachedData('authToken', data.token);
                    }
                    return true;
                }
                return false;
            } catch (error) {
                window.App.Logger.error('Session refresh failed:', error);
                return false;
            }
        },
        getConfig: () => ({ ...window.App?.config }),
        setDebugMode: (enabled) => {
            if (window.App?.config) {
                window.App.config.debug = enabled;
                window.App.Logger.info(`Debug mode ${enabled ? 'enabled' : 'disabled'}`);
            }
        }
    };

    // Global error handling
    window.addEventListener('error', (event) => {
        window.App.Logger?.error('Global error:', event.error, {
            message: event.message,
            source: event.filename,
            line: event.lineno,
            column: event.colno
        });
    });

    window.addEventListener('unhandledrejection', (event) => {
        window.App.Logger?.error('Unhandled rejection:', event.reason);
        if (window.App.Alerts?.showError) {
            window.App.Alerts.showError('Async Error', 'An operation failed', event.reason);
        }
    });

    // Start the application
    bootstrap();
})();
