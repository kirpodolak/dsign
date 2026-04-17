/**
 * Core Application Initialization Module (ES Module)
 * Handles application bootstrap, authentication, and core services
 */

import { AppInitializer } from './utils/app-init.js';
import { clearToken } from './utils/helpers.js';
import { AppLogger } from './utils/logging.js';
import { StartupNetworkAssistant } from './utils/startup-network.js';

class AppCore {
    constructor() {
        this.initializationStarted = false;
        this.initialized = false;
        this.socketClientLoaderPromise = null;
        this.onReadyCallbacks = [];
        this.startupNetworkAssistant = null;
        this.config = {
            debug: window.location.hostname === 'localhost',
            socketReconnectDelay: 1000,
            maxSocketRetries: 5,
            authCheckInterval: 60000,
            apiTimeout: 30000,
            socketEndpoint: '/socket.io',
            startupIpDisplayMs: 60000,
            startupNetworkPromptMs: 120000
        };
        this.state = {
            navigationInProgress: false,
            authChecked: false,
            isAuthenticated: null,
            socketInitialized: false,
            socketConnected: false
        };
        this.logger = new AppLogger('AppCore');
        this.syncGlobalAppContext();
    }

    isAuthPage() {
        const path = window.location.pathname || '';
        return path.startsWith('/api/auth/');
    }

    shouldInitializeSockets() {
        if (this.isAuthPage()) return false;
        const path = window.location.pathname || '';
        return path === '/' || path.startsWith('/playlist/');
    }

    shouldRunPeriodicAuthChecks() {
        // Keep periodic auth checks only where they are truly needed.
        return this.shouldInitializeSockets();
    }

    async ensureSocketClientLoaded() {
        if (typeof window.io === 'function') {
            return true;
        }
        if (!this.socketClientLoaderPromise) {
            this.socketClientLoaderPromise = import('./utils/socket.io.esm.min.js')
                .then((module) => {
                    const socketIoFactory = module?.io || module?.default?.io || module?.default;
                    if (typeof socketIoFactory !== 'function') {
                        throw new Error('Socket.IO module does not export io factory');
                    }
                    window.io = socketIoFactory;
                    return true;
                })
                .catch((error) => {
                    this.logger.error('Failed to load Socket.IO client:', error);
                    this.socketClientLoaderPromise = null;
                    return false;
                });
        }
        return this.socketClientLoaderPromise;
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
        if (this.alerts) {
            this.alerts.showError('Error', message, error);
        }
    }

    syncGlobalAppContext() {
        if (typeof window === 'undefined') return;
        const globalApp = window.App && typeof window.App === 'object' ? window.App : {};
        window.App = globalApp;

        const existingConfig = globalApp.config && typeof globalApp.config === 'object'
            ? globalApp.config
            : {};
        const existingState = globalApp.state && typeof globalApp.state === 'object'
            ? globalApp.state
            : {};

        globalApp.config = { ...this.config, ...existingConfig };
        globalApp.state = { ...this.state, ...existingState };

        if (this.api) {
            globalApp.api = this.api;
            globalApp.API = this.api;
        }
        if (this.auth) {
            globalApp.auth = this.auth;
            globalApp.Auth = this.auth;
        }
        if (this.alerts) {
            globalApp.alerts = this.alerts;
            globalApp.Alerts = this.alerts;
        }
    }

    async initialize() {
        if (this.initializationStarted) {
            this.logger.debug('Initialization already started, skipping duplicate call');
            return;
        }
        this.initializationStarted = true;
        this.logger.info('Starting application initialization');

        try {
            // Initialize core services first
            this.api = new APIService();
            this.auth = new AuthService();
            this.alerts = new AlertSystem();
            this.syncGlobalAppContext();
            
            // Wait for essential services to be ready
            await this.waitForDependencies();

            // Initialize AppInitializer
            this.initializer = new AppInitializer({
                api: this.api,
                auth: this.auth,
                alerts: this.alerts
            });
            
            await this.initializer.init();

            // Check authentication state
            const isLoginPage = window.location.pathname.includes('/api/auth/login');
            const isAuth = await this.auth.checkAuth().catch(() => false);
            this.state.authChecked = true;
            this.state.isAuthenticated = isAuth;
            if (!isAuth && !isLoginPage) {
                this.logger.warn('Not authenticated - redirecting to login');
                this.auth.handleUnauthorized();
                return;
            }

            await this.initializeStartupNetworkAssistant();

            // Initialize WebSocket only on pages that currently use realtime updates.
            if (this.shouldInitializeSockets()) {
                setTimeout(() => this.initializeWebSockets(), 500);
            }
            
            // Set up periodic auth checks only for realtime pages.
            if (!isLoginPage && this.shouldRunPeriodicAuthChecks()) {
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
            this.executeReadyCallbacks();
            
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

    async initializeStartupNetworkAssistant() {
        if (this.startupNetworkAssistant) return;
        try {
            this.startupNetworkAssistant = new StartupNetworkAssistant({
                api: this.api,
                logger: this.logger,
                ipDisplayMs: Number(this.config.startupIpDisplayMs) || 60000,
                promptAutoHideMs: Number(this.config.startupNetworkPromptMs) || 120000,
            });
            await this.startupNetworkAssistant.init();
        } catch (error) {
            this.logger.warn('Startup network assistant initialization skipped', error);
        }
    }

    async waitForDependencies() {
        const maxAttempts = 10;
        const delay = 500;
        
        for (let i = 0; i < maxAttempts; i++) {
            const hasApi = Boolean(window.App?.API || window.App?.api || this.api);
            const hasAuth = Boolean(window.App?.Auth || window.App?.auth || this.auth);
            if (hasApi && hasAuth) {
                return;
            }
            await new Promise(resolve => setTimeout(resolve, delay));
        }
        throw new Error('Dependencies not available');
    }

    executeReadyCallbacks() {
        this.onReadyCallbacks.forEach(cb => {
            try {
                if (typeof cb === 'function') cb();
            } catch (error) {
                this.handleError('Ready callback error:', error);
            }
        });
        this.onReadyCallbacks = [];
    }

    resolveSocketConnection(socketUrl) {
        const fallbackPath = this.config.socketEndpoint || '/socket.io';
        const rawUrl = typeof socketUrl === 'string' ? socketUrl.trim() : '';
        if (!rawUrl) {
            return {
                uri: window.location.origin,
                path: fallbackPath
            };
        }

        try {
            const parsed = new URL(rawUrl, window.location.origin);
            return {
                uri: `${parsed.protocol}//${parsed.host}`,
                path: parsed.pathname || fallbackPath
            };
        } catch (error) {
            this.logger.warn('Invalid socket URL from server, using default socket path', { socketUrl: rawUrl });
            return {
                uri: window.location.origin,
                path: fallbackPath
            };
        }
    }

    async initializeWebSockets() {
        if (this.state.socketInitialized) return;
        
        try {
            const socketClientReady = await this.ensureSocketClientLoaded();
            if (!socketClientReady || typeof window.io !== 'function') {
                this.logger.warn('Socket.IO client unavailable, skipping websocket init');
                setTimeout(() => this.initializeWebSockets(), 500);
                return;
            }

            if (!this.state.authChecked) {
                const isAuth = await this.auth.checkAuth();
                this.state.authChecked = true;
                this.state.isAuthenticated = isAuth;
            }

            if (this.state.isAuthenticated === false) {
                this.logger.warn('WebSocket init aborted - not authenticated');
                return;
            }

            this.logger.debug('Initializing WebSocket connection');
            
            // Get fresh socket token with retry logic
            let socketToken;
            let socketConnection = this.resolveSocketConnection(this.config.socketEndpoint);
            try {
                const result = await this.auth.getSocketToken();
                socketToken = result.token;
                socketConnection = this.resolveSocketConnection(result.socket_url);
            } catch (error) {
                this.logger.error('Failed to get socket token:', error);
                // Refresh is cookie-based; retry once
                await this.auth.refreshToken().catch(() => {});
                const result = await this.auth.getSocketToken();
                socketToken = result.token;
                socketConnection = this.resolveSocketConnection(result.socket_url);
            }
            
            // Initialize socket connection
            const socket = io(socketConnection.uri, {
                path: socketConnection.path,
                auth: { token: socketToken },
                reconnection: true,
                reconnectionAttempts: this.config.maxSocketRetries,
                reconnectionDelay: this.config.socketReconnectDelay,
                transports: ['websocket'],
                upgrade: false
            });

            // Store socket globally
            window.appSocket = socket;
            this.state.socketInitialized = true;

            // Setup event handlers
            this.setupSocketHandlers(socket);
            
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
            this.handleSocketError(error);
        }
    }

    setupSocketHandlers(socket) {
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
            const message = String(error?.message || '').toLowerCase();
            if (message.includes('auth') || message.includes('token')) {
                this.auth.handleUnauthorized();
            }
        });
    }

    handleSocketError(error) {
        this.logger.error('WebSocket initialization failed:', error);
        const message = String(error?.message || '').toLowerCase();
        if (message.includes('auth') || message.includes('token')) {
            this.auth.handleUnauthorized();
        } else {
            setTimeout(() => this.initializeWebSockets(), 5000);
        }
    }
}

class AuthService {
    constructor() {
        this.logger = new AppLogger('AuthService');
        this._authCacheTtlMs = 15000;
        this._authCacheStorageKey = 'dsign_auth_status_cache';
        this._authCheckPromise = null;
        this._authCache = {
            value: null,
            timestamp: 0
        };
    }

    getApiClient() {
        return window.App?.API || window.App?.api || null;
    }

    getCachedAuthFromStorage() {
        try {
            const raw = sessionStorage.getItem(this._authCacheStorageKey);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (typeof parsed?.value !== 'boolean' || typeof parsed?.timestamp !== 'number') {
                return null;
            }
            return parsed;
        } catch {
            return null;
        }
    }

    setCachedAuth(value) {
        const entry = {
            value: Boolean(value),
            timestamp: Date.now()
        };
        this._authCache = entry;
        try {
            sessionStorage.setItem(this._authCacheStorageKey, JSON.stringify(entry));
        } catch {
            // ignore storage failures (private mode / quota)
        }
    }

    clearCachedAuth() {
        this._authCache = { value: null, timestamp: 0 };
        try {
            sessionStorage.removeItem(this._authCacheStorageKey);
        } catch {
            // ignore storage failures
        }
    }

    async checkAuth() {
        const now = Date.now();
        const isMemoryCacheFresh =
            this._authCache.timestamp &&
            (now - this._authCache.timestamp) < this._authCacheTtlMs &&
            typeof this._authCache.value === 'boolean';
        if (isMemoryCacheFresh) {
            return this._authCache.value;
        }

        const storedCache = this.getCachedAuthFromStorage();
        const isStorageCacheFresh =
            storedCache &&
            (now - storedCache.timestamp) < this._authCacheTtlMs &&
            typeof storedCache.value === 'boolean';
        if (isStorageCacheFresh) {
            this._authCache = storedCache;
            return storedCache.value;
        }

        if (this._authCheckPromise) {
            return this._authCheckPromise;
        }

        this._authCheckPromise = (async () => {
            try {
                const apiClient = this.getApiClient();
                if (!apiClient || typeof apiClient.fetch !== 'function') {
                    throw new Error('API client not available');
                }
                const response = await apiClient.fetch('/api/auth/status', { method: 'GET' });
                const data = await response.json().catch(() => ({}));
                const isAuthenticated = Boolean(data?.authenticated);
                this.setCachedAuth(isAuthenticated);
                return isAuthenticated;
            } catch (error) {
                this.setCachedAuth(false);
                this.logger.warn('Auth check failed:', error);
                return false;
            } finally {
                this._authCheckPromise = null;
            }
        })();

        try {
            return await this._authCheckPromise;
        } catch (error) {
            this.logger.warn('Auth check failed:', error);
            return false;
        }
    }

    async refreshToken() {
        try {
            const apiClient = this.getApiClient();
            if (!apiClient || typeof apiClient.fetch !== 'function') {
                throw new Error('API client not available');
            }
            const response = await apiClient.fetch('/api/auth/refresh-token', {
                method: 'POST',
                credentials: 'include'
            });
            
            if (!response.ok) throw new Error('Token refresh failed');
            await response.json().catch(() => ({}));
            this.setCachedAuth(true);
            return true;
        } catch (error) {
            this.logger.error('Token refresh failed:', error);
            throw error;
        }
    }

    handleUnauthorized() {
        const appState = (() => {
            const globalApp = window.App && typeof window.App === 'object' ? window.App : {};
            window.App = globalApp;
            if (!globalApp.state || typeof globalApp.state !== 'object') {
                globalApp.state = {};
            }
            return globalApp.state;
        })();
        if (appState.navigationInProgress) return;
        
        appState.navigationInProgress = true;
        this.clearCachedAuth();
        clearToken();
        
        if (window.appSocket) {
            window.appSocket.disconnect();
            delete window.appSocket;
        }
        
        const currentPath = window.location.pathname;
        if (!currentPath.includes('/api/auth/login')) {
            this.logger.warn('Redirecting to login');
            const redirectUrl = encodeURIComponent(currentPath + window.location.search);
            window.location.href = `/api/auth/login?redirect=${redirectUrl}`;
        }
    }

    setToken(token) {
        void token;
    }

    clearAuth() {
        // Best-effort cleanup of legacy non-HttpOnly storage.
        clearToken();
    }

    async getSocketToken() {
        try {
            const apiClient = this.getApiClient();
            if (!apiClient || typeof apiClient.fetch !== 'function') {
                throw new Error('API client not available');
            }
            const response = await apiClient.fetch('/api/auth/socket-token', { credentials: 'include' });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            if (!data?.token) {
                throw new Error('No token in response');
            }
            return data;
        } catch (error) {
            this.logger.error('Failed to get socket token:', error);
            throw error;
        }
    }

    async waitForToken(maxAttempts = 10, interval = 500) {
        // Legacy API: session cookie auth does not expose a token to JS.
        void maxAttempts;
        void interval;
        return Promise.resolve('session');
    }
}

class AlertSystem {
    constructor() {
        this.logger = new AppLogger('AlertSystem');
    }

    showAlert(type, title, message, options = {}) {
        this.logger.info(`Alert: ${title}`, { type, message });
        
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
        const defaultTimeout = Number(window.App?.config?.apiTimeout) || 30000;
        const timeoutMs = Number(options.timeout) || defaultTimeout;
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        
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
                if (window.App?.auth?.handleUnauthorized) {
                    window.App.auth.handleUnauthorized();
                } else if (window.App?.Auth?.handleUnauthorized) {
                    window.App.Auth.handleUnauthorized();
                }
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
const existingApp = typeof window !== 'undefined' && window.App instanceof AppCore ? window.App : null;
const App = existingApp || new AppCore();
App.logger = new AppLogger('App');
App.auth = new AuthService();
App.alerts = new AlertSystem();
App.api = new APIService();
App.syncGlobalAppContext();

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
