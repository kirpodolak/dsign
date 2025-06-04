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
        this.moduleName = moduleName;
        this.debugEnabled = (typeof window !== 'undefined' && window.App?.config?.debug) || false;
        this.logToServer = (typeof window !== 'undefined' && window.App?.config?.logToServer) || false;
    }

    /**
     * Send log to server
     * @private
     * @param {object} logData - Log data to send
     */
    _sendToServer(logData) {
        if (!this.logToServer || !window.App?.API?.fetch) return;

        try {
            window.App.API.fetch('/api/logs', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(logData)
            }).catch(() => {
                // Silent fail if logging fails
            });
        } catch (e) {
            // Silent fail if logging fails
        }
    }

    /**
     * Log error message with optional error object and context
     * @param {string} message - Error message
     * @param {Error|null} [error=null] - Error object
     * @param {object|null} [context=null] - Additional context data
     */
    error(message, error = null, context = null) {
        const fullMessage = `${this.moduleName}: ${message}`;
        const logData = {
            message: fullMessage,
            stack: error?.stack,
            context,
            level: 'error',
            module: this.moduleName,
            timestamp: new Date().toISOString()
        };

        console.error(fullMessage, error || '', context || '');
        this._sendToServer(logData);
    }

    /**
     * Log debug message (only shown in debug mode)
     * @param {string} message - Debug message
     * @param {object|null} [data=null] - Additional debug data
     */
    debug(message, data = null) {
        if (!this.debugEnabled) return;

        const fullMessage = `${this.moduleName}: ${message}`;
        const logData = {
            message: fullMessage,
            data,
            level: 'debug',
            module: this.moduleName,
            timestamp: new Date().toISOString()
        };

        console.debug(fullMessage, data || '');
        this._sendToServer(logData);
    }

    /**
     * Log informational message
     * @param {string} message - Info message
     * @param {object|null} [data=null] - Additional data
     */
    info(message, data = null) {
        const fullMessage = `${this.moduleName}: ${message}`;
        const logData = {
            message: fullMessage,
            data,
            level: 'info',
            module: this.moduleName,
            timestamp: new Date().toISOString()
        };

        console.info(fullMessage, data || '');
        this._sendToServer(logData);
    }

    /**
     * Log warning message
     * @param {string} message - Warning message
     * @param {object|null} [data=null] - Additional data
     */
    warn(message, data = null) {
        const fullMessage = `${this.moduleName}: ${message}`;
        const logData = {
            message: fullMessage,
            data,
            level: 'warn',
            module: this.moduleName,
            timestamp: new Date().toISOString()
        };

        console.warn(fullMessage, data || '');
        this._sendToServer(logData);
    }
}

// Initialize global logger instance if in browser context
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Logger = window.App.Logger || new AppLogger('Core');
}

// For CommonJS environments (optional)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { AppLogger };
}
