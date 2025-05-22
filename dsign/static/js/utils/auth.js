/**
 * Authentication service module
 * @module AuthService
 */

/**
 * Service for handling authentication, tokens and authorization state
 */
export class AuthService {
    constructor() {
        // Initialize logger (will be set properly when App is available)
        this.logger = typeof window !== 'undefined' ? window.App?.Logger : null;
        this.tokenKey = 'auth_token';
        this.authStatusKey = 'auth_status';
    }

    /**
     * Check user authentication status
     * @async
     * @returns {Promise<boolean>} True if user is authenticated
     */
    async checkAuth() {
        try {
            this.logger?.debug('Checking authentication status');
            
            const token = this.getToken();
            if (!token) {
                this.logger?.debug('No token found');
                this.clearAuth();
                return false;
            }
            
            if (!this.isTokenValid(token)) {
                this.logger?.warn('Invalid token format or expired');
                this.clearAuth();
                return false;
            }

            // Use dynamic import for API if not available globally
            const api = typeof window !== 'undefined' && window.App?.API 
                ? window.App.API 
                : await import('./api.js');

            const response = await api.fetch('/auth/api/check-auth');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            
            if (data?.authenticated && data?.token_valid) {
                this.logger?.debug('User authenticated');
                return true;
            }
            
            this.logger?.debug('User not authenticated');
            this.clearAuth();
            return false;
        } catch (error) {
            this.logger?.error('Authentication check failed', error);
            this.clearAuth();
            return false;
        }
    }

    /**
     * Get token from localStorage
     * @returns {string|null} Token or null if not found
     */
    getToken() {
        try {
            return typeof localStorage !== 'undefined' 
                ? localStorage.getItem(this.tokenKey) 
                : null;
        } catch (e) {
            this.logger?.error('Failed to get auth token', e);
            return null;
        }
    }

    /**
     * Save token to localStorage
     * @param {string} token JWT token
     */
    setToken(token) {
        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(this.tokenKey, token);
                this.logger?.debug('Token saved to storage');
            }
        } catch (e) {
            this.logger?.error('Failed to save auth token', e);
        }
    }

    /**
     * Clear authentication data
     */
    clearAuth() {
        this.logger?.debug('Clearing authentication data');
        
        if (typeof localStorage !== 'undefined') {
            localStorage.removeItem(this.tokenKey);
        }

        if (typeof window !== 'undefined') {
            window.App?.Helpers?.setCachedData?.(this.authStatusKey, { value: false });
            window.App?.Sockets?.disconnect?.();
        }
    }

    /**
     * Validate token format and expiration
     * @param {string} token JWT token
     * @returns {boolean} True if token is valid
     */
    isTokenValid(token) {
        if (!token || token.split('.').length !== 3) {
            return false;
        }

        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const isExpired = payload.exp * 1000 < Date.now();
            return !isExpired;
        } catch {
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
            this.logger?.info('User logged in successfully');
            
            if (typeof window !== 'undefined' && 
                window.App?.Sockets && 
                !window.App.Sockets.isConnected()) {
                setTimeout(() => {
                    window.App.Sockets.connect();
                }, 300);
            }
        } else {
            this.logger?.warn('Login response missing token');
        }
    }

    /**
     * Handle unauthorized access
     */
    handleUnauthorized() {
        if (typeof window === 'undefined') return;
        
        if (window.location.pathname.startsWith('/auth/login')) {
            this.logger?.debug('Already on login page, skipping redirect');
            return;
        }
        
        this.logger?.warn('Handling unauthorized access');
        this.clearAuth();
        
        setTimeout(() => {
            const redirect = encodeURIComponent(
                window.location.pathname + window.location.search
            );
            window.location.href = `/auth/login?redirect=${redirect}`;
        }, 100);
    }

    /**
     * Wait for valid token to appear
     * @param {number} [maxAttempts=5] Maximum attempts
     * @param {number} [delay=1000] Delay between attempts (ms)
     * @returns {Promise<string|null>} Token or null if not found
     */
    waitForToken(maxAttempts = 5, delay = 1000) {
        return new Promise((resolve) => {
            let attempt = 0;
            
            const checkToken = () => {
                attempt++;
                const token = this.getToken();
                
                if (this.isTokenValid(token) || attempt >= maxAttempts) {
                    resolve(token || null);
                } else {
                    this.logger?.debug(`Waiting for token (attempt ${attempt}/${maxAttempts})`);
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
            // Use dynamic import for API if not available globally
            const api = typeof window !== 'undefined' && window.App?.API 
                ? window.App.API 
                : await import('./api.js');

            const response = await api.fetch('/auth/socket-token', {
                credentials: 'include',
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });
            
            if (!response.ok) {
                if (response.status === 401) {
                    throw new Error('Authentication required');
                }
                if (response.status === 404) {
                    throw new Error('Endpoint not found');
                }
                throw new Error(`HTTP ${response.status}`);
            }
            
            const contentType = response.headers.get('content-type');
            if (!contentType?.includes('application/json')) {
                throw new Error('Invalid response format');
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
            this.logger?.error('Socket token fetch failed', error);
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
        if (!window.location.pathname.includes('/auth/login')) {
            setInterval(() => {
                window.App.Auth.checkAuth().catch(error => {
                    window.App.Logger?.error('Periodic auth check failed', error);
                });
            }, window.App.config?.authCheckInterval || 60000);
        }
    });
}
