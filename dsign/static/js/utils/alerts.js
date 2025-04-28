const alertContainer = document.createElement('div');
alertContainer.id = 'alerts-container';
alertContainer.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 9999;
    max-width: 400px;
`;
document.body.appendChild(alertContainer);

function showAlert(type, title, message, duration = 5000) {
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
    `;
    
    alert.innerHTML = `
        <strong>${title}</strong>
        <div>${message}</div>
    `;

    alertContainer.appendChild(alert);
    
    setTimeout(() => {
        alert.style.opacity = '0';
        setTimeout(() => alert.remove(), 300);
    }, duration);
}

function showError(title, message) {
    showAlert('error', title, message);
}

function getAlertColor(type) {
    const colors = {
        success: '#28a745',
        error: '#dc3545',
        warning: '#ffc107',
        info: '#17a2b8'
    };
    return colors[type] || colors.info;
}

// Экспортируем функции для использования в модулях
export {
    showAlert,
    showError
};

// Сохраняем обратную совместимость с глобальным объектом
if (typeof window !== 'undefined') {
    window.App = window.App || {};
    window.App.Alerts = {
        showAlert,
        showError
    };
}
