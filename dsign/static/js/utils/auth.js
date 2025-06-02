/**
 * Authentication service module
 * @module AuthService
 */
import { getCookie } from './helpers.js';

/**
 * Service for handling authentication, tokens and authorization state
 */
export class AuthService {
    constructor() {
        // Initialize logger
        this.logger = typeof window !== 'undefined' && window.App?.logger 
            ? window.App.logger 
            : console;
        
        this.tokenKey = 'auth_token';
        this.authStatusKey = 'auth_status';
        this.loginEndpoint = '/api/auth/login';
        this.checkAuthEndpoint = '/api/auth/check-auth';
        this.refreshTokenEndpoint = '/api/auth/refresh-token';
        this.socketTokenEndpoint = '/api/auth/socket-token';
    }

    /**
     * Check user authentication status
     * @async
     * @returns {Promise<boolean>} True if user is authenticated
     */
    async checkAuth() {
        try {
            this.logger.debug('Checking authentication status');
            
            const token = this.getToken();
            if (!token) {
                this.logger.debug('No token found');
                this.clearAuth();
                return false;
            }
            
            if (!this.isTokenValid(token)) {
                this.logger.warn('Invalid token format or expired');
                this.clearAuth();
                return false;
            }

            const response = await window.App.API.fetch(this.checkAuthEndpoint);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data?.authenticated && data?.token_valid) {
                this.logger.debug('User authenticated');
                return true;
            }
            
            this.logger.debug('User not authenticated');
            this.clearAuth();
            return false;
        } catch (error) {
            this.logger.error('Authentication check failed', error);
            this.clearAuth();
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
            const response = await window.App.API.fetch(this.refreshTokenEndpoint, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`
                }
            });
            
            if (!response.ok) throw new Error('Token refresh failed');
            
            const { token } = await response.json();
            this.setToken(token);
            return true;
        } catch (error) {
            this.logger.error('Token refresh failed:', error);
            this.clearAuth();
            throw error;
        }
    }

    /**
     * Get token from storage
     * @returns {string|null} Token or null if not found
     */
    getToken() {
        try {
            return window.App?.token || 
                   localStorage.getItem(this.tokenKey) || 
                   getCookie(this.tokenKey);
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
            window.App = window.App || {};
            window.App.token = token;
            
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(this.tokenKey, token);
            }
            
            // Set cookie if in browser context
            if (typeof document !== 'undefined') {
                document.cookie = `${this.tokenKey}=${token}; path=/; max-age=${3600*24*7}; Secure; SameSite=Lax`;
            }
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
            
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem(this.tokenKey);
            }

            if (typeof document !== 'undefined') {
                document.cookie = `${this.tokenKey}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
            }

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
        if (!token || typeof token !== 'string' || token.split('.').length !== 3) {
            return false;
        }

        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const isExpired = payload.exp * 1000 < Date.now();
            return !isExpired;
        } catch (e) {
            this.logger.warn('Token validation failed:', e);
            return false;
        }
    }

    /**
     * Handle successful login
     * @param {object} response Server response
     */
    handleLoginSuccess(response) {
        if (response?.token) {
            this.setToken(response.token);
            this.logger.info('User logged in successfully');
            
            if (typeof window !== 'undefined' && 
                window.App?.Sockets && 
                !window.App.Sockets.isConnected()) {
                setTimeout(() => {
                    window.App.Sockets.connect();
                }, 300);
            }
        } else {
            this.logger.warn('Login response missing token');
        }
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
            
            const checkToken = () => {
                attempt++;
                const token = this.getToken();
                
                if (token && this.isTokenValid(token)) {
                    resolve(token);
                } else if (attempt >= maxAttempts) {
                    reject(new Error('Token not available'));
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
            const response = await window.App.API.fetch(this.socketTokenEndpoint, {
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`
                }
            });
        
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
        
            const data = await response.json();
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
    
    // Initialize auth check handlers when DOM is ready
    document.addEventListener('DOMContentLoaded', () => {
        if (!window.location.pathname.includes('/api/auth/login')) {
            setInterval(() => {
                window.App.Auth.checkAuth().catch(error => {
                    window.App.logger?.error('Periodic auth check failed', error);
                });
            }, window.App.config?.authCheckInterval || 60000);
        }
    });
}
