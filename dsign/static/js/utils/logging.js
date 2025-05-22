/**
 * Enhanced logging module with server logging capability
 * @module AppLogger
 */

/**
 * Class for application logging with console and server capabilities
 */
export class AppLogger {
    /**
     * Create a new logger instance
     * @param {string} [moduleName='App'] - Name of the module for log prefixing
     */
    constructor(moduleName = 'App') {
        // Optional App framework check (can be removed if using pure modules)
        if (typeof window !== 'undefined' && !window.App) {
            console.warn('AppLogger: window.App not initialized - falling back to console only');
        }
        
        this.moduleName = moduleName;
        this.enableServerLogging = true;
        this.debugEnabled = (typeof window !== 'undefined' && window.App?.config?.debug) || false;
    }

    /**
     * Check if server logging is available
     * @private
     * @returns {boolean} True if server logging is enabled and sockets are available
     */
    _canSendToServer() {
        return this.enableServerLogging && 
               typeof window !== 'undefined' &&
               window.App?.Sockets?.isConnected && 
               typeof window.App.Sockets.emit === 'function';
    }

    /**
     * Log error message with optional error object and context
     * @param {string} message - Error message
     * @param {Error|null} [error=null] - Error object
     * @param {object|null} [context=null] - Additional context data
     */
    error(message, error = null, context = null) {
        const fullMessage = `${this.moduleName}: ${message}`;
        const errorData = {
            message: fullMessage,
            stack: error?.stack,
            context,
            level: 'error',
            url: typeof window !== 'undefined' ? window.location.href : null,
            timestamp: new Date().toISOString()
        };
        
        // Always log to console
        console.error(fullMessage, error || '', context || '');
        
        // Optional server logging
        if (this._canSendToServer()) {
            try {
                window.App.Sockets.emit('client_error', errorData);
            } catch (e) {
                console.error('Failed to send error to server:', e);
            }
        }
    }

    /**
     * Log debug message (only shown in debug mode)
     * @param {string} message - Debug message
     * @param {object|null} [data=null] - Additional debug data
     */
    debug(message, data = null) {
        if (!this.debugEnabled) return;
        
        const fullMessage = `${this.moduleName}: ${message}`;
        console.debug(fullMessage, data || '');
    }

    /**
     * Log informational message
     * @param {string} message - Info message
     * @param {object|null} [data=null] - Additional data
     */
    info(message, data = null) {
        const fullMessage = `${this.moduleName}: ${message}`;
        console.info(fullMessage, data || '');
    }

    /**
     * Log warning message
     * @param {string} message - Warning message
     * @param {object|null} [data=null] - Additional data
     */
    warn(message, data = null) {
        const fullMessage = `${this.moduleName}: ${message}`;
        console.warn(fullMessage, data || '');
    }
}

// Optional global export for backward compatibility
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Logger = window.App.Logger || new AppLogger('Core');
}

// For CommonJS environments (optional)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AppLogger };
}
