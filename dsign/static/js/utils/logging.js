export class AppLogger {
    constructor(moduleName = 'App') {
        this.moduleName = moduleName;
		this.enableServerLogging = true;
    }

    log(message, data = null) {
        const entry = {
            timestamp: new Date().toISOString(),
			module: this.moduleName,
			message,
			data
		};
        console.log(JSON.stringify(entry));
    }

    debug(message, data = null) {
        if (window.App.config.debug) {
            console.debug(`[${this.moduleName}] ${message}`, data || '');
        }
    }

    warn(message, data = null) {
        console.warn(`[${this.moduleName}] ${message}`, data || '');
        this._trackIfNeeded('warn', message, data);
    }

    error(message, error = null, context = null) {
        console.error(`[${this.moduleName}] ${message}`, error || '');
        this._trackIfNeeded('error', message, { error, context });
    }

    _trackIfNeeded(level, message, data) {
        if (this.enableServerLogging && window.App.Sockets?.isConnected) {
            window.App.Sockets.emit('client_error', {
                level,
                module: this.moduleName,
                message,
                data,
                url: window.location.href
            });
        }
    }
}
