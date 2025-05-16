export class AppLogger {
    constructor(moduleName = 'App') {
        if (!window.App) {
            console.error('AppLogger: window.App not initialized');
            throw new Error('App framework not loaded');
        }
        
        this.moduleName = moduleName;
        this.enableServerLogging = true;
        this.debugEnabled = window.App.config?.debug || false;
    }

    // Добавить проверку на доступность сокетов
    _canSendToServer() {
        return this.enableServerLogging && 
               window.App?.Sockets?.isConnected && 
               typeof window.App.Sockets.emit === 'function';
    }

    // Обновить метод error
    error(message, error = null, context = null) {
        const errorData = {
            message: `${this.moduleName}: ${message}`,
            stack: error?.stack,
            context
        };
        
        console.error(errorData.message, error || '', context || '');
        
        if (this._canSendToServer()) {
            try {
                window.App.Sockets.emit('client_error', {
                    ...errorData,
                    level: 'error',
                    url: window.location.href
                });
            } catch (e) {
                console.error('Failed to send error to server:', e);
            }
        }
    }
}
