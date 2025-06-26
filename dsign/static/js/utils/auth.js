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
        
        // Token configuration
        this.tokenKey = 'auth_token';
        this.refreshTokenKey = 'refresh_token';
        this.socketTokenKey = 'socket_token';
        this.authStatusKey = 'auth_status';
        
        // API endpoints
        this.loginEndpoint = '/api/auth/login';
        this.checkAuthEndpoint = '/api/auth/check-auth';
        this.refreshTokenEndpoint = '/api/auth/refresh-token';
        this.socketTokenEndpoint = '/api/auth/socket-token';
        
        // Timers
        this.authStatusInterval = null;
        this.tokenRefreshInterval = null;
        this.socketTokenRefreshInterval = null;
        
        // State
        this.status = false;
        this.tokenCheckAttempts = 0;
        this.maxTokenCheckAttempts = 5;
        this.tokenRefreshThreshold = 5 * 60 * 1000; // 5 minutes before expiration
        this.socketTokenRefreshThreshold = 2 * 60 * 1000; // 2 minutes buffer for socket token
    }

    async checkAuth() {
        try {
            this.logger.debug('Checking authentication status');
            
            // First check if we have any token at all
            const token = this.getToken();
            if (!token) {
                this.logger.debug('No token found - requiring login');
                return false;
            }

            // Then validate token
            if (!this._isTokenValid(token)) {
                this.logger.debug('Token invalid - requiring reauthentication');
                this.clearAuth();
                return false;
            }

            // Verify authentication status
            return await this._verifyAuthStatus();
        } catch (error) {
            this.logger.error('Authentication check failed', error);
            this.clearAuth();
            return false;
        }
    }

    _isTokenValid(token) {
        if (!token) return false;
        
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            return payload.exp * 1000 > Date.now();
        } catch (e) {
            this.logger.warn('Token validation failed:', e);
            return false;
        }
    }

    async _verifyAuthStatus() {
        try {
            const response = await fetch(this.checkAuthEndpoint, {
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`,
                    'Accept': 'application/json'
                }
            });
            
            if (response.status === 401) {
                this.handleUnauthorized();
                return false;
            }
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            this.updateAuthStatus(data?.authenticated ?? false);
            return data?.authenticated;
        } catch (error) {
            this.logger.error('Auth verification failed:', error);
            throw error;
        }
    }

    async login(credentials) {
        try {
            const response = await fetch(this.loginEndpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(credentials)
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.message || 'Login failed');
            }

            const data = await response.json();
            this._processLoginResponse(data);
            return data;
        } catch (error) {
            this.logger.error('Login failed:', error);
            throw error;
        }
    }

    _processLoginResponse(response) {
        if (!response?.token) {
            throw new Error('Invalid login response');
        }

        this.setToken(response.token);
        if (response.refresh_token) {
            this.setRefreshToken(response.refresh_token);
        }

        this.updateAuthStatus(true);
        this._initSocketConnection();
    }

    _initSocketConnection() {
        if (window.App?.Sockets) {
            // Only initialize socket connection after successful login
            setTimeout(() => {
                window.App.Sockets.initAfterAuth();
            }, 300);
        }
    }

    async getSocketToken(forceRefresh = false) {
        // Don't attempt if we don't have a valid auth token
        if (!this.getToken()) {
            throw new Error('Authentication required before getting socket token');
        }

        try {
            const response = await fetch(this.socketTokenEndpoint, {
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`,
                    'Accept': 'application/json'
                }
            });
            
            if (response.status === 401) {
                this.handleUnauthorized();
                return null;
            }
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            localStorage.setItem(this.socketTokenKey, data.token);
            return data;
        } catch (error) {
            this.logger.error('Socket token error:', error);
            throw error;
        }
    }

    handleUnauthorized() {
        // Don't redirect if we're already on login page
        if (window.location.pathname.includes('/login')) return;

        this.clearAuth();
        const redirectPath = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/login?next=${redirectPath}`;
    }

    clearAuth() {
        this.logger.debug('Clearing authentication data');
        
        // Clear tokens
        window.App = window.App || {};
        delete window.App.token;
        
        localStorage.removeItem(this.tokenKey);
        localStorage.removeItem(this.refreshTokenKey);
        localStorage.removeItem(this.socketTokenKey);
        
        // Clear cookies
        this._clearCookie(this.tokenKey);
        this._clearCookie(this.refreshTokenKey);
        this._clearCookie(this.socketTokenKey);
        
        // Update status
        this.updateAuthStatus(false);
        
        // Disconnect sockets
        window.App?.Sockets?.disconnect?.();
        
        // Cleanup timers
        this._cleanupTimers();
    }

    _clearCookie(name) {
        document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
    }

    _cleanupTimers() {
        clearInterval(this.authStatusInterval);
        clearInterval(this.tokenRefreshInterval);
        clearInterval(this.socketTokenRefreshInterval);
    }

    updateAuthStatus(isAuthenticated) {
        if (this.status === isAuthenticated) return;
        
        this.status = isAuthenticated;
        this._triggerAuthStatusChange(isAuthenticated);
        this.logger.debug(`Auth status updated: ${isAuthenticated}`);
    }

    _triggerAuthStatusChange(isAuthenticated) {
        if (typeof window !== 'undefined') {
            const event = new CustomEvent('auth:status_changed', {
                detail: isAuthenticated
            });
            document.dispatchEvent(event);
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
            localStorage.setItem(this.tokenKey, token);
            document.cookie = `${this.tokenKey}=${token}; path=/; max-age=86400; Secure; SameSite=Lax`;
        } catch (e) {
            this.logger.error('Failed to save auth token', e);
        }
    }

    setRefreshToken(token) {
        if (!token) return;

        try {
            localStorage.setItem(this.refreshTokenKey, token);
            document.cookie = `${this.refreshTokenKey}=${token}; path=/; max-age=604800; Secure; SameSite=Strict`;
        } catch (e) {
            this.logger.error('Failed to save refresh token', e);
        }
    }

    async refreshToken() {
        const refreshToken = this.getRefreshToken();
        if (!refreshToken) {
            throw new Error('No refresh token available');
        }

        const response = await fetch(this.refreshTokenEndpoint, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Authorization': `Bearer ${refreshToken}`,
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            const errorData = await this._parseErrorResponse(response);
            throw new Error(errorData.message || `HTTP ${response.status}`);
        }
        
        const data = await response.json();
        this.setToken(data.token);
        if (data.refresh_token) {
            this.setRefreshToken(data.refresh_token);
        }
        return true;
    }

    async _parseErrorResponse(response) {
        try {
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                return await response.json();
            }
            return { message: await response.text() };
        } catch (e) {
            return { message: `HTTP ${response.status}` };
        }
    }

    startAuthStatusChecker() {
        this._clearAuthStatusChecker();
        
        // Initial check
        this.checkAuth().catch(error => {
            this.logger.error('Initial auth check failed:', error);
        });

        // Periodic checks
        this.authStatusInterval = setInterval(() => {
            this.checkAuth().catch(error => {
                this.logger.error('Periodic auth check failed:', error);
            });
        }, 30000);
    }

    _clearAuthStatusChecker() {
        if (this.authStatusInterval) {
            clearInterval(this.authStatusInterval);
            this.authStatusInterval = null;
        }
    }

    _shouldRefreshToken(token) {
        if (!token || typeof token !== 'string' || token.split('.').length !== 3) {
            return false;
        }

        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const expiresAt = payload.exp * 1000;
            return (expiresAt - Date.now()) < this.tokenRefreshThreshold;
        } catch (e) {
            this.logger.warn('Token refresh check failed:', e);
            return false;
        }
    }

    waitForToken(maxAttempts = 10, delay = 1000) {
        return new Promise((resolve, reject) => {
            let attempt = 0;
            
            const checkToken = () => {
                attempt++;
                const token = this.getToken();
                
                if (token && this._isTokenValid(token)) {
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
}

// Initialize and export service for global access
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Auth = window.App.Auth || new AuthService();
    
    // Only start auth checker on non-login pages
    document.addEventListener('DOMContentLoaded', () => {
        if (!window.location.pathname.includes('/login')) {
            window.App.Auth.checkAuth().catch(() => {
                // Silent error - will be handled by auth flow
            });
        }
    });
}
