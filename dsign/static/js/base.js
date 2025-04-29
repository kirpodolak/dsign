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
        }
    };

    // Конфигурация приложения
    const CONFIG = {
        AUTH_CHECK_INTERVAL: 30000,
        MAX_API_RETRIES: 3,
        INIT_RETRY_DELAY: 100
    };

    // Минимальные реализации зависимостей
    const setupDependencies = () => {
        window.App.Helpers = window.App.Helpers || {
            showPageLoader: () => console.debug('[Loader] Showing'),
            hidePageLoader: () => console.debug('[Loader] Hiding'),
            getCachedData: (key) => localStorage.getItem(key),
            setCachedData: (key, value, ttl) => {
                const item = {
                    value: value,
                    expires: Date.now() + (ttl || 0)
                };
                localStorage.setItem(key, JSON.stringify(item));
            }
        };

        window.App.API = window.App.API || {
            fetch: async (url, options) => {
                const response = await fetch(url, {
                    credentials: 'include',
                    ...options
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response;
            }
        };

        window.App.Alerts = window.App.Alerts || {
            showAlert: (type, title, message) => console.log(`[${type}] ${title}: ${message}`),
            showError: (title, message) => console.error(`[Error] ${title}: ${message}`)
        };
    };

    // Сервис аутентификации
    const authService = {
        AUTH_KEY: 'auth_status',

        async checkAuth() {
            try {
                // Проверка кеша
                const cached = this.getCachedAuth();
                if (cached !== null) return cached;

                // Запрос к API
                const response = await window.App.API.fetch('/auth/api/check-auth', {
                    headers: { 'Accept': 'application/json' }
                });
                const data = await response.json();
                const isAuthenticated = data?.authenticated === true;
                
                this.setCachedAuth(isAuthenticated);
                return isAuthenticated;
            } catch (error) {
                console.debug('Auth check failed:', error);
                return false;
            }
        },

        getCachedAuth() {
            const item = localStorage.getItem(this.AUTH_KEY);
            if (!item) return null;
            
            const { value, expires } = JSON.parse(item);
            if (expires && Date.now() > expires) return null;
            
            return value;
        },

        setCachedAuth(value) {
            const item = {
                value: value,
                expires: Date.now() + CONFIG.AUTH_CHECK_INTERVAL
            };
            localStorage.setItem(this.AUTH_KEY, JSON.stringify(item));
        },

        clearAuthCache() {
            localStorage.removeItem(this.AUTH_KEY);
        }
    };

    // Обработка аутентификации
    const handleAuthFlow = async () => {
        const isLoginPage = window.location.pathname.includes('/auth/login');
        const isAuthenticated = await authService.checkAuth();

        if (!isAuthenticated && !isLoginPage) {
            const redirect = encodeURIComponent(window.location.pathname + window.location.search);
            window.location.href = `/auth/login?redirect=${redirect}`;
            return false;
        }

        if (isAuthenticated && isLoginPage) {
            const redirectTo = new URLSearchParams(window.location.search).get('redirect') || '/';
            window.location.href = redirectTo;
            return false;
        }

        return true;
    };

    // Инициализация UI
    const initializeUI = async () => {
        try {
            if (window.App.Index?.initAllButtons) {
                await window.App.Index.initAllButtons();
            }
        } catch (error) {
            console.error('UI initialization failed:', error);
        }
    };

    // Основная инициализация приложения
    const initializeApp = async () => {
        window.App.Helpers.showPageLoader();

        try {
            // Проверка аутентификации
            const shouldContinue = await handleAuthFlow();
            if (!shouldContinue) return;

            // Инициализация UI
            await initializeUI();

            // Периодическая проверка аутентификации
            if (!window.location.pathname.includes('/auth/login')) {
                setInterval(async () => {
                    const isAuth = await authService.checkAuth();
                    if (!isAuth) window.location.href = '/auth/login';
                }, CONFIG.AUTH_CHECK_INTERVAL);
            }

            // Помечаем приложение как инициализированное
            window.App.Core.initialized = true;
            window.App.Core.onReadyCallbacks.forEach(cb => cb());
            
        } catch (error) {
            console.error('App initialization failed:', error);
            window.App.Alerts.showError('Initialization Error', 'Failed to start application');
        } finally {
            window.App.Helpers.hidePageLoader();
        }
    };

    // Проверка готовности зависимостей
    const checkDependencies = () => {
        if (!window.App.API?.fetch) {
            setTimeout(checkDependencies, CONFIG.INIT_RETRY_DELAY);
            return;
        }
        initializeApp().catch(console.error);
    };

    // Старт приложения
    setupDependencies();
    
    if (document.readyState === 'complete') {
        checkDependencies();
    } else {
        document.addEventListener('DOMContentLoaded', checkDependencies);
    }

    // Публичный API
    window.App.Base = {
        checkAuth: authService.checkAuth.bind(authService),
        refreshAuth: () => {
            authService.clearAuthCache();
            return authService.checkAuth();
        }
    };
})();
