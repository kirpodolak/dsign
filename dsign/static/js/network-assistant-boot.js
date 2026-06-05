import { StartupNetworkAssistant } from './utils/startup-network.js';

const api = {
    fetch(url, options = {}) {
        return fetch(url, { credentials: 'include', ...options });
    },
};

document.addEventListener('DOMContentLoaded', () => {
    const path = window.location.pathname || '';
    if (path.startsWith('/api/auth/')) return;
    const assistant = new StartupNetworkAssistant({
        api,
        ipDisplayMs: 60000,
        promptAutoHideMs: 120000,
    });
    assistant.init().catch(() => {});
});
