// static/js/utils/api.js
const API_BASE_URL = '/api';
let authToken = null;

async function fetchAPI(endpoint, options = {}) {
    try {
        // Build base URL
        let url = endpoint.startsWith('http') ? endpoint : `${API_BASE_URL}/${endpoint.replace(/^\//, '')}`;
        
        // Handle query parameters
        if (options.query) {
            const queryParams = new URLSearchParams();
            for (const [key, value] of Object.entries(options.query)) {
                if (value !== undefined && value !== null) {
                    queryParams.append(key, value);
                }
            }
            url += (url.includes('?') ? '&' : '?') + queryParams.toString();
        }

        const headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            ...(authToken && { 'Authorization': `Bearer ${authToken}` }),
            ...options.headers
        };

        // Add CSRF token for modifying requests
        const method = options.method ? options.method.toUpperCase() : 'GET';
        if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
            headers['X-CSRFToken'] = getCSRFToken();
        }

        const response = await fetch(url, {
            ...options,
            headers,
            credentials: 'include'
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error(`API request failed: ${error.message}`);
        throw error;
    }
}

function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function setAuthToken(token) {
    authToken = token;
}

// Экспортируем все функции
export {
    fetchAPI as default, // Основная функция как экспорт по умолчанию
    getCSRFToken,
    setAuthToken
};

// Для обратной совместимости с кодом, который использует глобальный объект
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.API = {
        fetch: fetchAPI,
        getCSRFToken,
        setAuthToken
    };
}
