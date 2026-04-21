/**
 * App initialization helper.
 * This module is imported by `static/js/base.js` and must export `AppInitializer`.
 */
import { getCookie } from './helpers.js';

/**
 * Bootstrap helper that wires core services onto `window.App`.
 * The project uses a mix of legacy globals and ES modules; this keeps them consistent.
 */
export class AppInitializer {
    constructor({ api = null, auth = null, alerts = null, logger = null, sockets = null, helpers = null } = {}) {
        this.api = api;
        this.auth = auth;
        this.alerts = alerts;
        this.logger = logger;
        this.sockets = sockets;
        this.helpers = helpers;
    }

    async init() {
        if (typeof window === 'undefined') return;
        window.App = window.App || {};

        if (this.logger) window.App.logger = this.logger;
        if (this.api) window.App.API = this.api;
        if (this.auth) window.App.Auth = this.auth;
        if (this.alerts) window.App.Alerts = this.alerts;
        if (this.sockets) window.App.Sockets = this.sockets;
        if (this.helpers) window.App.Helpers = this.helpers;

        // Best-effort async init hooks (if services expose them).
        const maybe = async (svc) => {
            try {
                if (svc && typeof svc.init === 'function') await svc.init();
            } catch {
                // ignore to avoid blocking app boot
            }
        };
        await Promise.all([maybe(this.api), maybe(this.auth), maybe(this.alerts), maybe(this.sockets), maybe(this.helpers)]);
    }
}

/**
 * Service for handling authentication, tokens and authorization state
 */
export class AuthService {
    constructor() {
        // Initialize logger
        this.logger = typeof window !== 'undefined' && window.App?.logger 
            ? window.App.logger 
            : console;
        
        // Tokens must not be stored in JS-accessible storage (XSS risk).
        // Use session cookies (HttpOnly) + CSRF for state-changing requests.
        this.tokenKey = 'auth_token';
        this.authStatusKey = 'auth_status';
        this.loginEndpoint = '/api/auth/login';
        // NOTE: window.App.API.fetch prefixes endpoints with "/api".
        // So these must be relative to "/api", not full "/api/..." paths.
        this.checkAuthEndpoint = 'auth/status';
        this.refreshTokenEndpoint = 'auth/refresh-token';
        this.socketTokenEndpoint = 'auth/socket-token';
    }

    /**
     * Check user authentication status
     * @async
     * @returns {Promise<boolean>} True if user is authenticated
     */
    async checkAuth() {
        try {
            this.logger.debug('Checking authentication status');

            // window.App.API.fetch returns parsed JSON data (not a raw Response)
            const data = await window.App.API.fetch(this.checkAuthEndpoint, { credentials: 'include' });

            if (data?.authenticated) {
                this.logger.debug('User authenticated');
                return true;
            }
            
            this.logger.debug('User not authenticated');
            return false;
        } catch (error) {
            // API wrapper throws Error with optional `.status`
            if (error?.status === 401) {
                this.logger.debug('Auth status: not authenticated (401)');
                return false;
            }
            this.logger.error('Authentication check failed', error);
            return false;
        }
    }

    /**
     * Refresh authentication token
     * @async
     * @returns {Promise<boolean>} True if token was refreshed successfully
     * @throws {Error} If token refresh failed
     */
    async refreshToken() {
        try {
            const data = await window.App.API.fetch(this.refreshTokenEndpoint, {
                method: 'POST',
                credentials: 'include'
            });

            // If server uses an HttpOnly cookie for a token, JS should not store it.
            void data;
            return true;
        } catch (error) {
            this.logger.error('Token refresh failed:', error);
            throw error;
        }
    }

    /**
     * Get token from storage
     * @returns {string|null} Token or null if not found
     */
    getToken() {
        try {
            // Legacy: return any server-set cookie value if readable, but do not store tokens in JS.
            // Prefer session cookie auth; this should generally be null/empty.
            return getCookie(this.tokenKey) || null;
        } catch (e) {
            this.logger.error('Failed to get auth token', e);
            return null;
        }
    }

    /**
     * Save token to storage
     * @param {string} token JWT token
     */
    setToken(token) {
        try {
            // No-op: avoid persisting tokens in JS (XSS risk).
            void token;
        } catch (e) {
            this.logger.error('Failed to save auth token', e);
        }
    }

    /**
     * Clear authentication data
     */
    clearAuth() {
        try {
            this.logger.debug('Clearing authentication data');
            
            window.App = window.App || {};
            delete window.App.token;

            if (typeof window !== 'undefined') {
                window.App?.Helpers?.setCachedData?.(this.authStatusKey, { value: false });
                window.App?.Sockets?.disconnect?.();
            }
        } catch (e) {
            this.logger.error('Failed to clear auth token', e);
        }
    }

    /**
     * Validate token format and expiration
     * @param {string} token JWT token
     * @returns {boolean} True if token is valid
     */
    isTokenValid(token) {
        void token;
        return false;
    }

    /**
     * Handle successful login
     * @param {object} response Server response
     */
    handleLoginSuccess(response) {
        void response;
        this.logger.info('User logged in successfully');
    }

    /**
     * Handle unauthorized access
     */
    handleUnauthorized() {
        if (window.location.pathname.startsWith('/auth/login')) {
            return;
        }

        const redirectPath = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/api/auth/login?next=${redirectPath}`;
    }

    /**
     * Wait for valid token to appear
     * @param {number} [maxAttempts=10] Maximum attempts
     * @param {number} [delay=1000] Delay between attempts (ms)
     * @returns {Promise<string>} Valid token
     * @throws {Error} If token not available after max attempts
     */
    waitForToken(maxAttempts = 10, delay = 1000) {
        return new Promise((resolve, reject) => {
            let attempt = 0;
            
            const checkToken = async () => {
                attempt++;
                try {
                    const ok = await this.checkAuth();
                    if (ok) {
                        resolve('session');
                        return;
                    }
                } catch {
                    // ignore
                }

                if (attempt >= maxAttempts) {
                    reject(new Error('Not authenticated'));
                } else {
                    this.logger.debug(`Waiting for token (attempt ${attempt}/${maxAttempts})`);
                    setTimeout(checkToken, delay);
                }
            };
            
            checkToken();
        });
    }

    /**
     * Get WebSocket connection token
     * @async
     * @returns {Promise<{token: string, expiresIn: number, socketUrl: string}>} WebSocket connection data
     * @throws {Error} If failed to get token
     */
    async getSocketToken() {
        try {
            const data = await window.App.API.fetch(this.socketTokenEndpoint, { credentials: 'include' });
            if (!data?.token) {
                throw new Error('Invalid token response');
            }
        
            return {
                token: data.token,
                expiresIn: data.expires_in || 300,
                socketUrl: data.socket_url || '/socket.io'
            };
        } catch (error) {
            this.logger.error('Socket token fetch failed', error);
            throw new Error(`Failed to get socket token: ${error.message}`);
        }
    }
}

// Initialize and export service for global access
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Auth = window.App.Auth || new AuthService();
}
