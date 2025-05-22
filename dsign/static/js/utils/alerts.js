/**
 * Alert Notification Module
 * @module Alerts
 * @description Provides styled alert notifications for the application
 */

// Create alert container only in browser environment
let alertContainer;

if (typeof document !== 'undefined') {
    alertContainer = document.createElement('div');
    alertContainer.id = 'alerts-container';
    alertContainer.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 9999;
        max-width: 400px;
        pointer-events: none;
    `;
    document.body.appendChild(alertContainer);
}

/**
 * Color mapping for different alert types
 * @constant
 * @type {Object}
 */
const ALERT_COLORS = {
    success: '#28a745',
    error: '#dc3545',
    warning: '#ffc107',
    info: '#17a2b8',
    default: '#17a2b8'
};

/**
 * Show styled alert notification
 * @param {string} type - Alert type (success, error, warning, info)
 * @param {string} title - Alert title
 * @param {string} message - Alert message
 * @param {number} [duration=5000] - Duration in ms before auto-dismiss
 */
export function showAlert(type, title, message, duration = 5000) {
    if (typeof document === 'undefined') return;

    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.style.cssText = `
        padding: 15px;
        margin-bottom: 10px;
        border-radius: 4px;
        background-color: ${getAlertColor(type)};
        color: white;
        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        animation: fadeIn 0.3s ease-out;
        pointer-events: auto;
    `;
    
    alert.innerHTML = `
        <strong>${title}</strong>
        <div>${message}</div>
    `;

    alertContainer.appendChild(alert);
    
    // Auto-dismiss after duration
    const dismiss = () => {
        alert.style.opacity = '0';
        alert.style.transition = 'opacity 0.3s ease-out';
        setTimeout(() => {
            alert.remove();
        }, 300);
    };

    const timer = setTimeout(dismiss, duration);
    
    // Allow manual dismissal
    alert.addEventListener('click', () => {
        clearTimeout(timer);
        dismiss();
    });
}

/**
 * Show error alert (convenience wrapper for showAlert)
 * @param {string} title - Error title
 * @param {string} message - Error message
 * @param {number} [duration=8000] - Duration in ms before auto-dismiss
 */
export function showError(title, message, duration = 8000) {
    showAlert('error', title, message, duration);
}

/**
 * Get color for alert type
 * @private
 * @param {string} type - Alert type
 * @returns {string} Color code
 */
function getAlertColor(type) {
    return ALERT_COLORS[type] || ALERT_COLORS.default;
}

/**
 * Show success alert (convenience wrapper for showAlert)
 * @param {string} title - Success title
 * @param {string} message - Success message
 * @param {number} [duration=3000] - Duration in ms before auto-dismiss
 */
export function showSuccess(title, message, duration = 3000) {
    showAlert('success', title, message, duration);
}

/**
 * Show warning alert (convenience wrapper for showAlert)
 * @param {string} title - Warning title
 * @param {string} message - Warning message
 * @param {number} [duration=5000] - Duration in ms before auto-dismiss
 */
export function showWarning(title, message, duration = 5000) {
    showAlert('warning', title, message, duration);
}

// Maintain global accessibility for backward compatibility
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Alerts = {
        showAlert,
        showError,
        showSuccess,
        showWarning,
        getAlertColor
    };
}

// Add basic styles if not already present
if (typeof document !== 'undefined') {
    const styleId = 'alerts-module-styles';
    if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.textContent = `
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .alert {
                opacity: 0;
                animation: fadeIn 0.3s ease-out forwards;
            }
        `;
        document.head.appendChild(style);
    }
}
