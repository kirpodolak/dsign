/**
 * Authentication service module
 * @module AuthService
 */
import { getCookie } from './helpers.js';

/**
 * Enhanced service for handling authentication, tokens and authorization state
 */
export class AuthService {
    constructor() {
        this.logger = typeof window !== 'undefined' && window.App?.logger 
            ? window.App.logger 
            : console;
        
        this.tokenKey = 'auth_token';
        this.refreshTokenKey = 'refresh_token';
        this.socketTokenKey = 'socket_token';
        this.authStatusKey = 'auth_status';
        this.loginEndpoint = '/api/auth/login';
        this.checkAuthEndpoint = '/api/auth/check-auth';
        this.refreshTokenEndpoint = '/api/auth/refresh-token';
        this.socketTokenEndpoint = '/api/auth/socket-token';
        this.authStatusInterval = null;
        this.tokenRefreshInterval = null;
        this.status = false;
        this.tokenCheckAttempts = 0;
        this.maxTokenCheckAttempts = 5;
        this.tokenRefreshThreshold = 5 * 60 * 1000; // 5 minutes before expiration
    }

    async checkAuth() {
        try {
            this.logger.debug('Checking authentication status');
            
            // First check token validity
            const token = this.getToken();
            if (!token || !this.isTokenValid(token)) {
                this.logger.debug(token ? 'Token invalid' : 'No token found');
                this.clearAuth();
                return false;
            }

            // Check if token needs refresh
            if (this.shouldRefreshToken(token)) {
                this.logger.debug('Token needs refresh, attempting...');
                try {
                    await this.refreshToken();
                } catch (error) {
                    this.logger.warn('Token refresh failed:', error);
                    this.clearAuth();
                    return false;
                }
            }

            // HTTP check with credentials
            const response = await fetch(this.checkAuthEndpoint, {
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`
                }
            });
            
            if (response.ok) {
                const data = await response.json();
                const isAuthenticated = data?.authenticated && data?.token_valid;
                this.updateAuthStatus(isAuthenticated);
                return isAuthenticated;
            }

            // WebSocket fallback
            return this.checkAuthViaWebSocket();
        } catch (error) {
            this.logger.error('Authentication check failed', error);
            this.clearAuth();
            return false;
        }
    }

    async checkAuthViaWebSocket() {
        if (!window.App?.Sockets?.isConnected?.()) {
            return false;
        }

        return new Promise((resolve) => {
            const timeout = setTimeout(() => {
                resolve(false);
            }, 3000);

            window.App.Sockets.emit('request_auth_status', {}, (response) => {
                clearTimeout(timeout);
                const isAuthenticated = response?.authenticated ?? false;
                this.updateAuthStatus(isAuthenticated);
                resolve(isAuthenticated);
            });
        });
    }

    startAuthStatusChecker() {
        if (this.authStatusInterval) {
            clearInterval(this.authStatusInterval);
        }

        // Initial check
        this.checkAuth().catch(error => {
            this.logger.error('Initial auth check failed:', error);
        });

        // Periodic checks with exponential backoff
        this.authStatusInterval = setInterval(() => {
            this.checkAuth().catch(error => {
                this.logger.error('Periodic auth check failed:', error);
            });
        }, 30000);

        // Start token refresh monitor
        this.startTokenRefreshMonitor();
    }

    startTokenRefreshMonitor() {
        if (this.tokenRefreshInterval) {
            clearInterval(this.tokenRefreshInterval);
        }

        this.tokenRefreshInterval = setInterval(() => {
            const token = this.getToken();
            if (token && this.shouldRefreshToken(token)) {
                this.logger.debug('Automatically refreshing token...');
                this.refreshToken().catch(error => {
                    this.logger.error('Auto token refresh failed:', error);
                });
            }
        }, 60000); // Check every minute
    }

    updateAuthStatus(isAuthenticated) {
        if (this.status === isAuthenticated) return;
        
        this.status = isAuthenticated;
        if (typeof window !== 'undefined') {
            window.App?.trigger?.('auth:status_changed', isAuthenticated);
            if (isAuthenticated) {
                window.App?.Sockets?.connect?.();
            } else {
                window.App?.Sockets?.disconnect?.();
            }
        }
        this.logger.debug(`Auth status updated: ${isAuthenticated}`);
    }

    async refreshToken() {
        try {
            const refreshToken = this.getRefreshToken();
            if (!refreshToken) throw new Error('No refresh token available');

            const response = await fetch(this.refreshTokenEndpoint, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${refreshToken}`
                }
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const { token, refresh_token } = await response.json();
            this.setToken(token);
            if (refresh_token) {
                this.setRefreshToken(refresh_token);
            }
            return true;
        } catch (error) {
            this.logger.error('Token refresh failed:', error);
            this.clearAuth();
            throw error;
        }
    }

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

    getRefreshToken() {
        try {
            return localStorage.getItem(this.refreshTokenKey) || 
                   getCookie(this.refreshTokenKey);
        } catch (e) {
            this.logger.error('Failed to get refresh token', e);
            return null;
        }
    }

    setToken(token) {
        if (!token) return;

        try {
            window.App = window.App || {};
            window.App.token = token;
            
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(this.tokenKey, token);
            }
            
            if (typeof document !== 'undefined') {
                document.cookie = `${this.tokenKey}=${token}; path=/; max-age=${3600*24}; Secure; SameSite=Lax`;
            }
        } catch (e) {
            this.logger.error('Failed to save auth token', e);
        }
    }

    setRefreshToken(token) {
        if (!token) return;

        try {
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(this.refreshTokenKey, token);
            }
            
            if (typeof document !== 'undefined') {
                document.cookie = `${this.refreshTokenKey}=${token}; path=/; max-age=${3600*24*7}; Secure; SameSite=Strict`;
            }
        } catch (e) {
            this.logger.error('Failed to save refresh token', e);
        }
    }

    clearAuth() {
        try {
            this.logger.debug('Clearing authentication data');
            
            window.App = window.App || {};
            delete window.App.token;
            
            if (typeof localStorage !== 'undefined') {
                localStorage.removeItem(this.tokenKey);
                localStorage.removeItem(this.refreshTokenKey);
                localStorage.removeItem(this.socketTokenKey);
            }

            if (typeof document !== 'undefined') {
                const clearCookie = (name) => {
                    document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
                };
                clearCookie(this.tokenKey);
                clearCookie(this.refreshTokenKey);
                clearCookie(this.socketTokenKey);
            }

            this.updateAuthStatus(false);
            
            if (typeof window !== 'undefined') {
                window.App?.Helpers?.setCachedData?.(this.authStatusKey, { value: false });
                window.App?.Sockets?.disconnect?.();
            }
        } catch (e) {
            this.logger.error('Failed to clear auth data', e);
        }
    }

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

    shouldRefreshToken(token) {
        if (!token || typeof token !== 'string' || token.split('.').length !== 3) {
            return false;
        }

        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const expiresAt = payload.exp * 1000;
            const timeRemaining = expiresAt - Date.now();
            return timeRemaining < this.tokenRefreshThreshold;
        } catch (e) {
            this.logger.warn('Token refresh check failed:', e);
            return false;
        }
    }

    handleLoginSuccess(response) {
        if (response?.token) {
            this.setToken(response.token);
            if (response.refresh_token) {
                this.setRefreshToken(response.refresh_token);
            }
            this.logger.info('User logged in successfully');
            this.updateAuthStatus(true);
            
            // Initiate WebSocket connection
            if (window.App?.Sockets && !window.App.Sockets.isConnected()) {
                setTimeout(() => {
                    window.App.Sockets.connect();
                }, 300);
            }
        } else {
            this.logger.warn('Login response missing token');
        }
    }

    handleUnauthorized() {
        if (window.location.pathname.startsWith('/auth/login')) {
            return;
        }

        this.clearAuth();
        const redirectPath = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/api/auth/login?next=${redirectPath}`;
    }

    waitForToken(maxAttempts = 10, delay = 1000) {
        return new Promise((resolve, reject) => {
            let attempt = 0;
            
            const checkToken = () => {
                attempt++;
                const token = this.getToken();
                
                if (token && this.isTokenValid(token)) {
                    this.tokenCheckAttempts = 0;
                    resolve(token);
                } else if (attempt >= maxAttempts) {
                    this.tokenCheckAttempts = 0;
                    reject(new Error('Token not available'));
                } else {
                    this.tokenCheckAttempts = attempt;
                    setTimeout(checkToken, delay);
                }
            };
            
            checkToken();
        });
    }

    async getSocketToken() {
        try {
            const token = this.getToken();
            if (!token) throw new Error('No base token available');

            const response = await fetch(this.socketTokenEndpoint, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
        
            if (!response.ok) {
                if (response.status === 401) {
                    await this.refreshToken();
                    return this.getSocketToken();
                }
                throw new Error(`HTTP ${response.status}`);
            }
        
            const data = await response.json();
            if (!data?.token) {
                throw new Error('Invalid token response');
            }
        
            // Cache socket token
            if (typeof localStorage !== 'undefined') {
                localStorage.setItem(this.socketTokenKey, data.token);
            }
        
            return {
                token: data.token,
                expiresIn: data.expires_in || 300,
                socketUrl: data.socket_url || '/socket.io'
            };
        } catch (error) {
            this.logger.error('Socket token fetch failed', error);
            throw error;
        }
    }
}

// Initialize and export service for global access
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Auth = window.App.Auth || new AuthService();
    
    document.addEventListener('DOMContentLoaded', () => {
        if (!window.location.pathname.includes('/api/auth/login')) {
            window.App.Auth.startAuthStatusChecker();
        }
    });
}
