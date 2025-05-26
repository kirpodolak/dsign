/**
 * API Service Module
 * @module APIService
 * @description Centralized API request handler with token management
 */

const API_BASE_URL = '/api';
let authToken = null;

/**
 * Main API fetch function
 * @async
 * @param {string} endpoint - API endpoint
 * @param {object} [options={}] - Request options
 * @param {string} [options.method] - HTTP method
 * @param {object} [options.headers] - Additional headers
 * @param {object} [options.query] - Query parameters
 * @param {object} [options.body] - Request body
 * @returns {Promise<object>} API response data
 * @throws {Error} On request failure
 */
async function fetchAPI(endpoint, options = {}) {
    try {
        // Build complete URL
        let url = endpoint.startsWith('http') 
            ? endpoint 
            : `${API_BASE_URL}/${endpoint.replace(/^\//, '')}`;

        // Process query parameters
        if (options.query) {
            const queryParams = new URLSearchParams();
            for (const [key, value] of Object.entries(options.query)) {
                if (value !== undefined && value !== null) {
                    queryParams.append(key, value);
                }
            }
            url += (url.includes('?') ? '&' : '?') + queryParams.toString();
        }

        // Prepare headers
        const headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            ...(authToken && { 'Authorization': `Bearer ${authToken}` }),
            ...options.headers
        };

        // Add CSRF token for state-changing requests
        const method = options.method?.toUpperCase() || 'GET';
        if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
            const csrfToken = getCSRFToken();
            if (csrfToken) {
                headers['X-CSRFToken'] = csrfToken;
            }
        }

        // Configure request
        const requestConfig = {
            method,
            headers,
            credentials: 'include',
            ...options
        };

        // Add body for non-GET requests
        if (method !== 'GET' && options.body) {
            requestConfig.body = JSON.stringify(options.body);
        }

        const response = await fetch(url, requestConfig);

        // Handle non-success responses
        if (!response.ok) {
            let errorData = {};
            try {
                errorData = await response.json();
            } catch (e) {
                console.warn('Failed to parse error response', e);
            }

            const error = new Error(errorData.message || `HTTP error! status: ${response.status}`);
            error.status = response.status;
            error.data = errorData;
            
            // Special handling for auth errors
            if (error.status === 401) {
                if (typeof window !== 'undefined' && window.App?.Auth?.handleUnauthorized) {
                    window.App.Auth.handleUnauthorized();
                }
            }
            
            throw error;
        }

        // Parse successful response
        try {
            return await response.json();
        } catch (e) {
            console.warn('Failed to parse successful response', e);
            return {};
        }
    } catch (error) {
        console.error(`API request to ${endpoint} failed:`, error);
        throw error;
    }
}

/**
 * Get CSRF token from meta tag
 * @returns {string} CSRF token or empty string if not found
 */
function getCSRFToken() {
    if (typeof document === 'undefined') return '';
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

/**
 * Set authentication token for API requests
 * @param {string} token - Authentication token
 */
function setAuthToken(token) {
    authToken = token;
    if (typeof window !== 'undefined') {
        window.App = window.App || {};
        window.App.token = token; // For global access if needed
    }
}

/**
 * Get current authentication token
 * @returns {string|null} Current auth token
 */
function getAuthToken() {
    return authToken;
}

// Create unified API object
const API = {
    fetch: fetchAPI,
    getCSRFToken,
    setAuthToken,
    getAuthToken,
    
    // Additional helper methods
    get: (endpoint, query = {}) => fetchAPI(endpoint, { method: 'GET', query }),
    post: (endpoint, body = {}, options = {}) => fetchAPI(endpoint, { 
        method: 'POST', 
        body, 
        ...options 
    }),
    put: (endpoint, body = {}, options = {}) => fetchAPI(endpoint, { 
        method: 'PUT', 
        body, 
        ...options 
    }),
    delete: (endpoint, options = {}) => fetchAPI(endpoint, { 
        method: 'DELETE', 
        ...options 
    })
};

// Export both named exports and unified API object
export {
    fetchAPI,
    getCSRFToken,
    setAuthToken,
    getAuthToken,
    API as default
};

// Maintain global accessibility
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.API = API;
    
    // For legacy support if needed
    window.App.fetchAPI = fetchAPI;
}
