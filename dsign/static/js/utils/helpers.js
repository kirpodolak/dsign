(function() {
    const cache = {
        data: {},
        timestamps: {},
        TTL: 300000 // 5 minutes
    };

    function debounce(func, wait, immediate = false) {
        let timeout;
        return function() {
            const context = this, args = arguments;
            const later = function() {
                timeout = null;
                if (!immediate) func.apply(context, args);
            };
            const callNow = immediate && !timeout;
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
            if (callNow) func.apply(context, args);
        };
    }

    function toggleButtonState(button, isLoading) {
        if (!button) return;
        
        const originalHTML = button.dataset.originalHtml || button.innerHTML;
        if (isLoading) {
            button.dataset.originalHtml = originalHTML;
            button.innerHTML = `
                <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>
                Loading...
            `;
        } else {
            button.innerHTML = originalHTML;
        }
        button.disabled = isLoading;
    }

    function showPageLoader() {
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
            `;
            loader.innerHTML = `
                <div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            `;
            document.body.appendChild(loader);
        }
        loader.style.display = 'flex';
    }

    function hidePageLoader() {
        const loader = document.getElementById('page-loader');
        if (loader) loader.style.display = 'none';
    }

    function getCachedData(key) {
        if (cache.timestamps[key] && Date.now() - cache.timestamps[key] < cache.TTL) {
            return cache.data[key];
        }
        return null;
    }

    function setCachedData(key, data) {
        cache.data[key] = data;
        cache.timestamps[key] = Date.now();
    }

    window.App = window.App || {};
    window.App.Helpers = {
        debounce,
        toggleButtonState,
        showPageLoader,
        hidePageLoader,
        getCachedData,
        setCachedData
    };
})();