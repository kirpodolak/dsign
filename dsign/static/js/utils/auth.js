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
            
            // Validate token presence and validity
            const token = this.getToken();
            if (!this._isTokenValid(token)) {
                this.logger.debug(token ? 'Token invalid' : 'No token found');
                this.clearAuth();
                return false;
            }

            // Refresh token if needed
            if (this._shouldRefreshToken(token)) {
                await this._attemptTokenRefresh();
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

    async _attemptTokenRefresh() {
        this.logger.debug('Token needs refresh, attempting...');
        try {
            await this.refreshToken();
        } catch (error) {
            this.logger.warn('Token refresh failed:', error);
            throw error;
        }
    }

    async _verifyAuthStatus() {
        try {
            // Try HTTP check first
            const httpCheck = await this._checkAuthViaHTTP();
            if (httpCheck.valid) {
                this.updateAuthStatus(httpCheck.authenticated);
                return httpCheck.authenticated;
            }

            // Fallback to WebSocket check
            return await this.checkAuthViaWebSocket();
        } catch (error) {
            this.logger.error('Auth verification failed:', error);
            throw error;
        }
    }

    async _checkAuthViaHTTP() {
        try {
            const response = await fetch(this.checkAuthEndpoint, {
                credentials: 'include',
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`
                }
            });
            
            if (!response.ok) {
                return { valid: false };
            }
            
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                throw new Error('Invalid response format');
            }
            
            const data = await response.json();
            return {
                valid: true,
                authenticated: data?.authenticated && data?.token_valid
            };
        } catch (error) {
            this.logger.error('HTTP auth check failed:', error);
            return { valid: false };
        }
    }

    async checkAuthViaWebSocket() {
        if (!window.App?.Sockets?.isConnected?.()) {
            throw new Error('Socket not connected');
        }

        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                reject(new Error('Socket authentication check timed out'));
            }, 3000);

            try {
                window.App.Sockets.emit('request_auth_status', {}, (response) => {
                    clearTimeout(timeout);
                    
                    if (response?.error) {
                        reject(new Error(response.error));
                        return;
                    }
                    
                    const isAuthenticated = response?.authenticated ?? false;
                    this.updateAuthStatus(isAuthenticated);
                    resolve(isAuthenticated);
                });
            } catch (error) {
                clearTimeout(timeout);
                reject(new Error(`WebSocket error: ${error.message}`));
            }
        });
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

        // Start monitoring
        this._startTokenMonitors();
    }

    _clearAuthStatusChecker() {
        if (this.authStatusInterval) {
            clearInterval(this.authStatusInterval);
            this.authStatusInterval = null;
        }
    }

    _startTokenMonitors() {
        this.startTokenRefreshMonitor();
        this.startSocketTokenRefreshMonitor();
    }

    startTokenRefreshMonitor() {
        this._clearTokenRefreshMonitor();
        
        this.tokenRefreshInterval = setInterval(() => {
            const token = this.getToken();
            if (token && this._shouldRefreshToken(token)) {
                this.logger.debug('Automatically refreshing token...');
                this.refreshToken().catch(error => {
                    this.logger.error('Auto token refresh failed:', error);
                });
            }
        }, 60000);
    }

    _clearTokenRefreshMonitor() {
        if (this.tokenRefreshInterval) {
            clearInterval(this.tokenRefreshInterval);
            this.tokenRefreshInterval = null;
        }
    }

    startSocketTokenRefreshMonitor() {
        this._clearSocketTokenRefreshMonitor();
        
        this.socketTokenRefreshInterval = setInterval(async () => {
            try {
                const socketToken = localStorage.getItem(this.socketTokenKey);
                if (socketToken && this._shouldRefreshSocketToken(socketToken)) {
                    this.logger.debug('Refreshing socket token...');
                    await this.getSocketToken(true);
                }
            } catch (error) {
                this.logger.error('Socket token refresh check failed:', error);
            }
        }, 30000);
    }

    _clearSocketTokenRefreshMonitor() {
        if (this.socketTokenRefreshInterval) {
            clearInterval(this.socketTokenRefreshInterval);
            this.socketTokenRefreshInterval = null;
        }
    }

    updateAuthStatus(isAuthenticated) {
        if (this.status === isAuthenticated) return;
        
        this.status = isAuthenticated;
        this._triggerAuthStatusChange(isAuthenticated);
        this._handleSocketConnection(isAuthenticated);
        this.logger.debug(`Auth status updated: ${isAuthenticated}`);
    }

    _triggerAuthStatusChange(isAuthenticated) {
        if (typeof window !== 'undefined') {
            window.App?.trigger?.('auth:status_changed', isAuthenticated);
        }
    }

    _handleSocketConnection(isAuthenticated) {
        if (typeof window !== 'undefined') {
            if (isAuthenticated) {
                window.App?.Sockets?.connect?.();
            } else {
                window.App?.Sockets?.disconnect?.();
            }
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
        this._updateTokens(data.token, data.refresh_token);
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

    _updateTokens(token, refreshToken) {
        this.setToken(token);
        if (refreshToken) {
            this.setRefreshToken(refreshToken);
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
            // Update in-memory token
            window.App = window.App || {};
            window.App.token = token;
            
            // Persist to storage
            this._persistToken(this.tokenKey, token, 3600*24);
        } catch (e) {
            this.logger.error('Failed to save auth token', e);
        }
    }

    setRefreshToken(token) {
        if (!token) return;

        try {
            this._persistToken(this.refreshTokenKey, token, 3600*24*7);
        } catch (e) {
            this.logger.error('Failed to save refresh token', e);
        }
    }

    _persistToken(key, token, maxAge) {
        if (typeof localStorage !== 'undefined') {
            localStorage.setItem(key, token);
        }
        
        if (typeof document !== 'undefined') {
            const sameSite = key === this.refreshTokenKey ? 'Strict' : 'Lax';
            document.cookie = `${key}=${token}; path=/; max-age=${maxAge}; Secure; SameSite=${sameSite}`;
        }
    }

    clearAuth() {
        try {
            this.logger.debug('Clearing authentication data');
            
            // Clear in-memory token
            window.App = window.App || {};
            delete window.App.token;
            
            // Clear storage
            this._clearStorage();
            
            // Update status
            this.updateAuthStatus(false);
            
            // Notify other components
            this._notifyAuthCleared();
            
            // Cleanup timers
            this._cleanupTimers();
        } catch (e) {
            this.logger.error('Failed to clear auth data', e);
        }
    }

    _clearStorage() {
        // Clear localStorage
        if (typeof localStorage !== 'undefined') {
            localStorage.removeItem(this.tokenKey);
            localStorage.removeItem(this.refreshTokenKey);
            localStorage.removeItem(this.socketTokenKey);
        }

        // Clear cookies
        if (typeof document !== 'undefined') {
            this._clearCookie(this.tokenKey);
            this._clearCookie(this.refreshTokenKey);
            this._clearCookie(this.socketTokenKey);
        }
    }

    _clearCookie(name) {
        document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
    }

    _notifyAuthCleared() {
        if (typeof window !== 'undefined') {
            window.App?.Helpers?.setCachedData?.(this.authStatusKey, { value: false });
            window.App?.Sockets?.disconnect?.();
        }
    }

    _cleanupTimers() {
        if (this.authStatusInterval) clearInterval(this.authStatusInterval);
        if (this.tokenRefreshInterval) clearInterval(this.tokenRefreshInterval);
        if (this.socketTokenRefreshInterval) clearInterval(this.socketTokenRefreshInterval);
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

    _shouldRefreshSocketToken(token) {
        if (!token || typeof token !== 'string' || token.split('.').length !== 3) {
            return true;
        }

        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const expiresAt = payload.exp * 1000;
            return (expiresAt - Date.now()) < this.socketTokenRefreshThreshold;
        } catch (e) {
            this.logger.warn('Socket token refresh check failed:', e);
            return true;
        }
    }

    handleLoginSuccess(response) {
        if (!response?.token) {
            this.logger.warn('Login response missing token');
            return;
        }

        this._processLoginResponse(response);
        this._initSocketConnection();
        this._getInitialSocketToken();
    }

    _processLoginResponse(response) {
        this.setToken(response.token);
        if (response.refresh_token) {
            this.setRefreshToken(response.refresh_token);
        }
        this.logger.info('User logged in successfully');
        this.updateAuthStatus(true);
    }

    _initSocketConnection() {
        if (window.App?.Sockets && !window.App.Sockets.isConnected()) {
            setTimeout(() => {
                window.App.Sockets.connect();
            }, 300);
        }
    }

    _getInitialSocketToken() {
        this.getSocketToken().catch(error => {
            this.logger.error('Initial socket token fetch failed:', error);
        });
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

    async getSocketToken(forceRefresh = false) {
        try {
            const currentOrigin = window.location.origin;
        
            const response = await fetch(this.socketTokenEndpoint, {
                headers: {
                    'Authorization': `Bearer ${this.getToken()}`,
                    'X-Requested-With': 'XMLHttpRequest',
                    'Origin': currentOrigin,
                    'Accept': 'application/json'
                }
            });
        
            if (!response.ok) {
                const contentType = response.headers.get('content-type');
                if (!contentType || !contentType.includes('application/json')) {
                    const text = await response.text();
                    throw new Error(`Invalid response format: ${text.substring(0, 100)}`);
                }
                
                const errorData = await response.json();
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
        
            const data = await response.json();
            localStorage.setItem(this.socketTokenKey, data.token);
            return this._formatSocketTokenResponse(data, currentOrigin);
        } catch (error) {
            this.logger.error('Socket token error:', error);
        
            if (error.message.includes('Invalid response format')) {
                throw new Error('Server returned HTML instead of JSON. Check server configuration.');
            }
        
            throw error;
        }
    }

    _formatSocketTokenResponse(data, currentOrigin) {
        return {
            token: data.token,
            expiresIn: data.expires_in || 1800,
            socketUrl: data.socket_url || currentOrigin.replace(/^http/, 'ws')
        };
    }

    getTokenExpiry(token) {
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const expiresAt = payload.exp * 1000;
            return Math.floor((expiresAt - Date.now()) / 1000);
        } catch (e) {
            this.logger.warn('Failed to parse token expiry', e);
            return 300;
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
