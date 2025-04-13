(function() {
    // Initialize global App object
    window.App = window.App || {};
    
    // Default empty implementations for dependencies
    const dependencies = {
        Helpers: {
            showPageLoader: () => console.log('Show loader'),
            hidePageLoader: () => console.log('Hide loader'),
            getCachedData: () => null,
            setCachedData: () => {}
        },
        API: {
            fetch: async () => { throw new Error('API not initialized'); }
        },
        Alerts: {
            showAlert: (type, title, message) => console.log(`[${type}] ${title}: ${message}`),
            showError: (title, message) => console.error(`${title}: ${message}`)
        },
        Sockets: {
            emit: () => Promise.reject('Sockets not initialized')
        }
    };

    // Merge with existing implementations
    Object.keys(dependencies).forEach(namespace => {
        window.App[namespace] = { ...dependencies[namespace], ...(window.App[namespace] || {}) };
    });

    // Destructure with fallbacks
    const { 
        showPageLoader, 
        hidePageLoader,
        getCachedData,
        setCachedData
    } = window.App.Helpers;
    
    const { fetch: fetchAPI } = window.App.API;
    const { showAlert, showError } = window.App.Alerts;
    const { emit: socketEmit } = window.App.Sockets;

    // Auth token cache key
    const AUTH_CACHE_KEY = 'auth_status';
    const AUTH_CACHE_TTL = 30000; // 30 seconds

    /**
     * Check authentication status with caching
     * @returns {Promise<boolean>} True if authenticated
     */
    async function checkAuth() {
        try {
            // Check cache first
            const cachedAuth = getCachedData(AUTH_CACHE_KEY);
            if (cachedAuth !== null) {
                return cachedAuth;
            }

            const response = await fetchAPI('/auth/api/check-auth', {
                credentials: 'include',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            const isAuthenticated = data?.authenticated === true;
            
            // Cache the result
            setCachedData(AUTH_CACHE_KEY, isAuthenticated);
            
            return isAuthenticated;
            
        } catch (error) {
            console.error('Auth check failed:', error);
            return false;
        }
    }

    /**
     * Handle authentication flow
     * @returns {Promise<boolean>} True if should continue initialization
     */
    async function handleAuthentication() {
        try {
            const isAuthenticated = await checkAuth();
            
            if (!isAuthenticated) {
                if (!window.location.pathname.includes('/auth/login')) {
                    window.location.href = '/auth/login?redirect=' + encodeURIComponent(window.location.pathname);
                }
                return false;
            }
            
            return true;
            
        } catch (error) {
            showError('Authentication Error', 'Failed to verify login status');
            return false;
        }
    }

    /**
     * Initialize UI components
     */
    async function initializeUI() {
        try {
            // Initialize buttons if component exists
            if (window.App.Index?.initAllButtons) {
                await window.App.Index.initAllButtons();
            }
            
            // Initialize player controls if component exists
            if (window.App.PlayerControls?.setCurrentUser) {
                const userResponse = await fetchAPI('/auth/api/current-user');
                if (userResponse?.id) {
                    window.App.PlayerControls.setCurrentUser(userResponse.id);
                }
            }
            
        } catch (error) {
            console.error('UI initialization failed:', error);
            showError('Initialization Error', 'Some components failed to load');
        }
    }

    /**
     * Main application initialization
     */
    async function initializeApp() {
        try {
            showPageLoader();
            
            // Check authentication
            const shouldContinue = await handleAuthentication();
            if (!shouldContinue) return;
            
            // Initialize UI components
            await initializeUI();
            
            // Periodic auth check
            setInterval(async () => {
                const isAuthenticated = await checkAuth();
                if (!isAuthenticated) {
                    showAlert('warning', 'Session Expired', 'You will be redirected to login');
                    setTimeout(() => {
                        window.location.href = '/auth/login';
                    }, 3000);
                }
            }, AUTH_CACHE_TTL);
            
        } catch (error) {
            console.error('App initialization failed:', error);
            showError('Initialization Failed', 'Application failed to start properly');
        } finally {
            hidePageLoader();
        }
    }

    /**
     * Safe initialization wrapper
     */
    function safeInitialize() {
        try {
            // Wait for all dependencies to load
            const checkDependencies = setInterval(() => {
                if (window.App.API && window.App.Alerts && window.App.Helpers) {
                    clearInterval(checkDependencies);
                    initializeApp().catch(error => {
                        console.error('Unhandled initialization error:', error);
                    });
                }
            }, 100);
            
            // Timeout if dependencies don't load
            setTimeout(() => {
                clearInterval(checkDependencies);
                showError('Error', 'Failed to load required components');
            }, 5000);
            
        } catch (error) {
            console.error('Startup error:', error);
        }
    }

    // Start the application
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        safeInitialize();
    } else {
        document.addEventListener('DOMContentLoaded', safeInitialize);
    }

    // Expose public methods
    window.App.Base = {
        initializeApp: safeInitialize,
        checkAuth,
        refreshAuth: () => {
            // Clear cache and check auth again
            setCachedData(AUTH_CACHE_KEY, null);
            return checkAuth();
        }
    };
})();