// dsign/static/js/utils/auth.js

/**
 * Сервис для работы с аутентификацией
 * Централизует все операции с токенами и состоянием авторизации
 */
class AuthService {
    constructor() {
        this.logger = window.App?.Logger;
        this.tokenKey = 'auth_token';
        this.authStatusKey = 'auth_status';
    }

    /**
     * Проверяет статус аутентификации пользователя
     * @returns {Promise<boolean>} true если пользователь аутентифицирован
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

            const response = await window.App.API?.fetch('/auth/api/check-auth');
            const data = await response?.json();
            
            if (data?.authenticated && data?.token) {
                this.logger?.debug('User authenticated');
                this.setToken(data.token);
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
     * Получает токен из localStorage
     * @returns {string|null} Токен или null если отсутствует
     */
    getToken() {
        try {
            return localStorage.getItem(this.tokenKey);
        } catch (e) {
            this.logger?.error('Failed to get auth token', e);
            return null;
        }
    }

    /**
     * Сохраняет токен в localStorage
     * @param {string} token JWT токен
     */
    setToken(token) {
        try {
            localStorage.setItem(this.tokenKey, token);
            this.logger?.debug('Token saved to storage');
        } catch (e) {
            this.logger?.error('Failed to save auth token', e);
        }
    }

    /**
     * Очищает данные аутентификации
     */
    clearAuth() {
        this.logger?.debug('Clearing authentication data');
        localStorage.removeItem(this.tokenKey);
        window.App.Helpers?.setCachedData(this.authStatusKey, { value: false });
        
        if (window.App?.Sockets) {
            window.App.Sockets.disconnect();
        }
    }

    /**
     * Проверяет валидность формата и срока действия токена
     * @param {string} token JWT токен
     * @returns {boolean} true если токен валиден
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
     * Обрабатывает успешный вход пользователя
     * @param {object} response Ответ сервера
     */
    handleLoginSuccess(response) {
        if (response?.token) {
            this.setToken(response.token);
            this.logger?.info('User logged in successfully');
            
            // Инициируем подключение WebSocket если нужно
            if (window.App.Sockets && !window.App.Sockets.isConnected) {
                setTimeout(() => {
                    window.App.Sockets.connect();
                }, 300);
            }
        } else {
            this.logger?.warn('Login response missing token');
        }
    }

    /**
     * Обрабатывает неавторизованный доступ
     */
    handleUnauthorized() {
        if (window.location.pathname.startsWith('/auth/login')) {
            this.logger?.debug('Already on login page, skipping redirect');
            return;
        }
        
        this.logger?.warn('Handling unauthorized access');
        this.clearAuth();
        
        setTimeout(() => {
            const redirect = encodeURIComponent(window.location.pathname + window.location.search);
            window.location.href = `/auth/login?redirect=${redirect}`;
        }, 100);
    }

    /**
     * Ожидает появления валидного токена
     * @param {number} [maxAttempts=5] Максимальное количество попыток
     * @param {number} [delay=1000] Задержка между попытками (мс)
     * @returns {Promise<string|null>} Токен или null если не найден
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
}

// Инициализация и экспорт сервиса
window.App = window.App || {};
window.App.Auth = new AuthService();

/**
 * Инициализация обработчиков аутентификации
 */
document.addEventListener('DOMContentLoaded', () => {
    // Периодическая проверка аутентификации
    if (!window.location.pathname.includes('/auth/login')) {
        setInterval(() => {
            window.App.Auth.checkAuth().catch(error => {
                window.App.Logger?.error('Periodic auth check failed', error);
            });
        }, window.App.config?.authCheckInterval || 60000);
    }
});

// Экспорт для использования в модульной системе
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AuthService;
}
