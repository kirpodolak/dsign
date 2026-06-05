import { AppLogger } from './logging.js';

export class StartupNetworkAssistant {
    constructor({ api, logger = null, ipDisplayMs = 60000, promptAutoHideMs = 120000 } = {}) {
        this.api = api || null;
        this.logger = logger || new AppLogger('StartupNetworkAssistant');
        this.ipDisplayMs = Math.max(5000, Number(ipDisplayMs) || 60000);
        this.promptAutoHideMs = Math.max(10000, Number(promptAutoHideMs) || 120000);
        this.isAuthPage = (window.location.pathname || '').startsWith('/api/auth/');
        this.overlayVisible = false;
        this.hideTimerId = null;
        this.connecting = false;
        this.storageKeys = {
            overlayShown: 'dsign_startup_network_overlay_shown',
            ipBadgeShown: 'dsign_startup_ip_badge_shown',
        };
    }

    async init() {
        if (this.isAuthPage) return;
        const data = await this.requestJson('/api/system/network/status', { method: 'GET' });
        if (!data?.success || !data.network) return;

        const network = data.network;
        const bootMode = data.assistant?.boot_mode || 'off';
        const debugMode = bootMode === 'debug';

        if (network.primary_ip && !debugMode && !this.hasSessionFlag(this.storageKeys.ipBadgeShown)) {
            this.showStartupIpBadge(network.primary_ip);
        }

        const offline = !network.internet_online;
        const shouldShowOverlay = debugMode || (offline && !this.hasSessionFlag(this.storageKeys.overlayShown));
        if (!shouldShowOverlay) return;

        if (!debugMode) {
            this.setSessionFlag(this.storageKeys.overlayShown);
        }

        const autoHideMs = debugMode ? 10000 : this.promptAutoHideMs;
        await this.showNetworkOverlay(network, { debugMode, autoHideMs });
        if (offline || debugMode) {
            await this.refreshWifiList();
        }
    }

    getElement(id) {
        return document.getElementById(id);
    }

    hasSessionFlag(key) {
        try {
            return sessionStorage.getItem(key) === '1';
        } catch {
            return false;
        }
    }

    setSessionFlag(key) {
        try {
            sessionStorage.setItem(key, '1');
        } catch {
            // Ignore storage failures (private mode / quota)
        }
    }

    getCsrfToken() {
        return document.querySelector('meta[name="csrf-token"]')?.content
            || document.cookie.match(/csrf_token=([^;]+)/)?.[1]
            || '';
    }

    async requestJson(url, options = {}) {
        try {
            const method = String(options.method || 'GET').toUpperCase();
            const headers = { ...(options.headers || {}) };
            if (method !== 'GET' && method !== 'HEAD') {
                headers['X-CSRFToken'] = headers['X-CSRFToken'] || this.getCsrfToken();
            }
            const response = await this.api.fetch(url, {
                ...options,
                headers,
            });
            return await response.json();
        } catch (error) {
            this.logger.warn(`Network assistant request failed: ${url}`, error);
            return null;
        }
    }

    formatNetworkStatusMessage(network, debugMode) {
        const online = Boolean(network?.internet_online);
        const ssid = network?.wifi_connected_ssid || '';
        const ip = network?.primary_ip || '—';
        const lines = [];
        if (debugMode) {
            lines.push('Режим отладки: экран сети при старте');
        }
        lines.push(online ? 'Интернет: подключён' : 'Интернет: нет соединения');
        if (ssid) {
            lines.push(`Wi-Fi: ${ssid}`);
        }
        lines.push(`IP: ${ip}`);
        if (debugMode) {
            lines.push('Автозакрытие через 10 сек');
        } else if (!online) {
            lines.push('Выберите Wi-Fi и подключитесь');
        }
        return lines.join('\n');
    }

    async showNetworkOverlay(network, { debugMode = false, autoHideMs = null } = {}) {
        const root = this.getElement('startup-network-overlay');
        if (!root) return;
        root.hidden = false;
        this.overlayVisible = true;

        const retryBtn = this.getElement('startup-network-retry');
        const scanBtn = this.getElement('startup-network-scan');
        const connectBtn = this.getElement('startup-network-connect');
        const hiddenToggle = this.getElement('startup-network-hidden-toggle');
        const closeBtn = this.getElement('startup-network-close');
        const ssidSelect = this.getElement('startup-network-ssid');
        const ssidManualWrap = this.getElement('startup-network-manual-wrap');
        const ssidManualInput = this.getElement('startup-network-manual-ssid');
        const status = this.getElement('startup-network-status');
        const passwordInput = this.getElement('startup-network-password');
        const controls = this.getElement('startup-network-controls');
        const title = this.getElement('startup-network-title');

        if (title) {
            title.textContent = debugMode ? 'Сеть (отладка)' : 'Подключение к сети';
        }
        if (status) {
            status.textContent = this.formatNetworkStatusMessage(network, debugMode);
            status.style.whiteSpace = 'pre-line';
        }
        if (controls) {
            controls.hidden = Boolean(debugMode && network?.internet_online);
        }

        if (retryBtn) {
            retryBtn.onclick = async () => {
                const latest = await this.requestJson('/api/system/network/status', { method: 'GET' });
                const latestNetwork = latest?.network;
                if (latestNetwork?.internet_online) {
                    this.onInternetConnected(latestNetwork);
                } else if (status) {
                    status.textContent = this.formatNetworkStatusMessage(latestNetwork, debugMode);
                }
            };
        }

        if (scanBtn) {
            scanBtn.onclick = async () => {
                await this.refreshWifiList();
            };
        }

        if (hiddenToggle) {
            hiddenToggle.onchange = () => {
                const hidden = Boolean(hiddenToggle.checked);
                if (ssidManualWrap) ssidManualWrap.hidden = !hidden;
                if (ssidSelect) ssidSelect.disabled = hidden;
                if (hidden && ssidManualInput) {
                    ssidManualInput.focus();
                }
            };
        }

        if (connectBtn) {
            connectBtn.onclick = async () => {
                if (this.connecting) return;
                this.connecting = true;
                connectBtn.disabled = true;
                const hidden = Boolean(hiddenToggle?.checked);
                const selected = hidden
                    ? String(ssidManualInput?.value || '').trim()
                    : String(ssidSelect?.value || '').trim();
                const password = String(passwordInput?.value || '');
                if (!selected) {
                    if (status) status.textContent = 'Enter Wi-Fi name (SSID).';
                    this.connecting = false;
                    connectBtn.disabled = false;
                    return;
                }

                if (status) status.textContent = `Connecting to ${selected}...`;
                const response = await this.requestJson('/api/system/network/wifi/connect', {
                    method: 'POST',
                    body: JSON.stringify({
                        ssid: selected,
                        password,
                        hidden,
                    }),
                });

                if (!response?.success) {
                    if (status) {
                        status.textContent = response?.error
                            ? `Connection failed: ${response.error}`
                            : 'Connection failed. Please verify SSID/password.';
                    }
                    this.connecting = false;
                    connectBtn.disabled = false;
                    return;
                }

                const latestNetwork = response.network || (await this.requestJson('/api/system/network/status', { method: 'GET' }))?.network;
                if (latestNetwork?.internet_online) {
                    this.onInternetConnected(latestNetwork);
                } else if (status) {
                    status.textContent = this.formatNetworkStatusMessage(latestNetwork, debugMode);
                }
                this.connecting = false;
                connectBtn.disabled = false;
            };
        }

        if (closeBtn) {
            closeBtn.onclick = () => {
                this.hideNetworkOverlay();
            };
        }

        const hideMs = Math.max(5000, Number(autoHideMs) || this.promptAutoHideMs);
        this.hideTimerId = window.setTimeout(() => {
            if (this.overlayVisible) {
                this.hideNetworkOverlay();
            }
        }, hideMs);
    }

    hideNetworkOverlay() {
        const root = this.getElement('startup-network-overlay');
        if (root) root.hidden = true;
        this.overlayVisible = false;
        if (this.hideTimerId) {
            clearTimeout(this.hideTimerId);
            this.hideTimerId = null;
        }
    }

    onInternetConnected(network) {
        const ip = network?.primary_ip || '';
        if (ip) this.showStartupIpBadge(ip);
        this.hideNetworkOverlay();
        const status = this.getElement('startup-network-status');
        if (status) status.textContent = 'Internet connected.';
    }

    async refreshWifiList() {
        const select = this.getElement('startup-network-ssid');
        const status = this.getElement('startup-network-status');
        if (!select) return;
        select.disabled = true;
        select.innerHTML = '';
        if (status && !this.overlayVisible) {
            status.textContent = 'Scanning Wi-Fi networks...';
        }

        const data = await this.requestJson('/api/system/network/wifi/scan', { method: 'GET' });
        if (!data?.success) {
            if (status && !String(status.textContent || '').includes('Интернет:')) {
                status.textContent = data?.error || 'Failed to scan Wi-Fi networks.';
            }
            select.disabled = false;
            return;
        }

        const networks = Array.isArray(data.networks) ? data.networks : [];
        if (!networks.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No Wi-Fi networks found';
            select.appendChild(option);
            select.disabled = false;
            return;
        }

        networks.forEach((network) => {
            const ssid = String(network.ssid || '').trim();
            if (!ssid) return;
            const option = document.createElement('option');
            option.value = ssid;
            const lock = network.security && network.security !== 'open' ? '🔒' : '🔓';
            const signal = Number(network.signal || 0);
            option.textContent = `${ssid} (${signal}%) ${lock}`;
            if (network.in_use) option.selected = true;
            select.appendChild(option);
        });

        if (select.options.length && !select.value) {
            select.selectedIndex = 0;
        }
        select.disabled = false;
    }

    showStartupIpBadge(ip) {
        const badge = this.getElement('startup-ip-badge');
        const valueNode = this.getElement('startup-ip-value');
        if (!badge || !valueNode) return;
        this.setSessionFlag(this.storageKeys.ipBadgeShown);
        valueNode.textContent = ip;
        badge.hidden = false;
        window.setTimeout(() => {
            badge.hidden = true;
        }, this.ipDisplayMs);
    }
}
