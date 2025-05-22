/**
 * Utility Helpers Module
 * @module Helpers
 * @description Collection of utility functions for authentication, cookies, UI and caching
 */

const cache = {
    data: {},
    timestamps: {},
    TTL: 300000 // 5 minutes cache lifetime
};

// ======================
// Authentication Helpers
// ======================

/**
 * Retrieves authentication token from storage
 * @returns {string|null} The auth token or null if not found
 */
export function getToken() {
    try {
        return typeof localStorage !== 'undefined' 
            ? localStorage.getItem('authToken') || getCookie('authToken') || null
            : null;
    } catch (error) {
        console.error('Token retrieval error:', error);
        return null;
    }
}

/**
 * Stores authentication token
 * @param {string} token - The auth token to store
 * @param {boolean} [remember=false] - Whether to persist beyond session
 */
export function setToken(token, remember = false) {
    try {
        if (typeof localStorage !== 'undefined' && remember) {
            localStorage.setItem('authToken', token);
        }
        setCookie('authToken', token, remember ? 7 : 1); // 7 days or 1 day expiry
    } catch (error) {
        console.error('Token storage error:', error);
    }
}

/**
 * Clears authentication token from all storage
 */
export function clearToken() {
    try {
        if (typeof localStorage !== 'undefined') {
            localStorage.removeItem('authToken');
        }
        deleteCookie('authToken');
    } catch (error) {
        console.error('Token clearance error:', error);
    }
}

// =================
// Cookie Helpers
// =================

/**
 * Gets cookie value by name
 * @param {string} name - Cookie name
 * @returns {string|null} Cookie value or null
 */
export function getCookie(name) {
    try {
        if (typeof document === 'undefined') return null;
        const match = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
        return match ? decodeURIComponent(match[2]) : null;
    } catch (error) {
        console.error('Cookie read error:', error);
        return null;
    }
}

/**
 * Sets cookie with security flags
 * @param {string} name - Cookie name
 * @param {string} value - Cookie value
 * @param {number} days - Days until expiration
 */
export function setCookie(name, value, days) {
    try {
        if (typeof document === 'undefined') return;
        const date = new Date();
        date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
        const secureFlag = location.protocol === 'https:' ? ';Secure' : '';
        const sameSite = ';SameSite=Lax';
        document.cookie = `${name}=${encodeURIComponent(value)};expires=${date.toUTCString()};path=/${secureFlag}${sameSite}`;
    } catch (error) {
        console.error('Cookie set error:', error);
    }
}

/**
 * Deletes cookie by name
 * @param {string} name - Cookie name to delete
 */
export function deleteCookie(name) {
    try {
        if (typeof document === 'undefined') return;
        document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/`;
    } catch (error) {
        console.error('Cookie delete error:', error);
    }
}

// =================
// UI Helpers
// =================

/**
 * Debounce function for limiting rapid calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in ms
 * @param {boolean} [immediate=false] - Whether to call immediately
 * @returns {Function} Debounced function
 */
export function debounce(func, wait, immediate = false) {
    let timeout;
    return function() {
        const context = this, args = arguments;
        const later = () => {
            timeout = null;
            if (!immediate) func.apply(context, args);
        };
        const callNow = immediate && !timeout;
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
        if (callNow) func.apply(context, args);
    };
}

/**
 * Toggles loading state of a button
 * @param {HTMLElement} button - Button element
 * @param {boolean} isLoading - Loading state
 */
export function toggleButtonState(button, isLoading) {
    if (!button || typeof document === 'undefined') return;
    
    try {
        const originalHTML = button.dataset.originalHtml || button.innerHTML;
        if (isLoading) {
            button.dataset.originalHtml = originalHTML;
            button.innerHTML = `
                <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
                Loading...
            `;
            button.disabled = true;
        } else {
            button.innerHTML = originalHTML;
            button.disabled = false;
        }
    } catch (error) {
        console.error('Button state toggle error:', error);
    }
}

/**
 * Shows full-page loader
 */
export function showPageLoader() {
    try {
        if (typeof document === 'undefined') return;
        let loader = document.getElementById('page-loader');
        if (!loader) {
            loader = document.createElement('div');
            loader.id = 'page-loader';
            loader.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.5);
                z-index: 9999;
                display: flex;
                justify-content: center;
                align-items: center;
                transition: opacity 0.3s ease;
            `;
            loader.innerHTML = `
                <div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            `;
            document.body.appendChild(loader);
        }
        loader.style.display = 'flex';
        loader.style.opacity = '1';
    } catch (error) {
        console.error('Page loader error:', error);
    }
}

/**
 * Hides full-page loader
 */
export function hidePageLoader() {
    try {
        if (typeof document === 'undefined') return;
        const loader = document.getElementById('page-loader');
        if (loader) {
            loader.style.opacity = '0';
            setTimeout(() => {
                loader.style.display = 'none';
            }, 300);
        }
    } catch (error) {
        console.error('Page loader hide error:', error);
    }
}

// =================
// Cache Helpers
// =================

/**
 * Gets cached data if valid
 * @param {string} key - Cache key
 * @returns {any|null} Cached data or null
 */
export function getCachedData(key) {
    try {
        if (cache.timestamps[key] && Date.now() - cache.timestamps[key] < cache.TTL) {
            return cache.data[key];
        }
        return null;
    } catch (error) {
        console.error('Cache read error:', error);
        return null;
    }
}

/**
 * Sets data in cache
 * @param {string} key - Cache key
 * @param {any} data - Data to cache
 */
export function setCachedData(key, data) {
    try {
        cache.data[key] = data;
        cache.timestamps[key] = Date.now();
    } catch (error) {
        console.error('Cache write error:', error);
    }
}

// Legacy aliases for backward compatibility
export const getAuthToken = getToken;
export const removeAuthToken = clearToken;

// Maintain global accessibility for backward compatibility
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Helpers = {
        getToken,
        setToken,
        clearToken,
        getCookie,
        setCookie,
        deleteCookie,
        debounce,
        toggleButtonState,
        showPageLoader,
        hidePageLoader,
        getCachedData,
        setCachedData,
        getAuthToken,
        removeAuthToken
    };
}
