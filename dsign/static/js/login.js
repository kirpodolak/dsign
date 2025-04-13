document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('login-form');
    const errorDiv = document.getElementById('login-error');
    const { showAlert = () => {} } = window.App.Alerts || {};

    function showError(message) {
        errorDiv.textContent = message;
        errorDiv.style.display = 'block';
        showAlert('error', 'Login Error', message);
    }

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        errorDiv.textContent = '';
        errorDiv.style.display = 'none';
        
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value.trim();
        
        if (!username || !password) {
            showError('Please fill in all fields');
            return;
        }

        try {
            const formData = new FormData(form);
            const response = await fetch(form.action, {
                method: 'POST',
                body: formData,
                credentials: 'include',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success && data.redirect) {
                    window.location.href = data.redirect;
                } else {
                    showError('Login successful but no redirect');
                }
            } else {
                const errorData = await response.json().catch(() => ({}));
                showError(errorData.error || 'Login failed');
            }
        } catch (error) {
            showError('Connection error: ' + error.message);
        }
    });

    // Показать/скрыть пароль
    const passwordField = document.getElementById('password');
    if (passwordField) {
        const togglePassword = document.createElement('span');
        togglePassword.innerHTML = '👁️';
        togglePassword.style.cursor = 'pointer';
        togglePassword.style.marginLeft = '5px';
        passwordField.parentNode.appendChild(togglePassword);
        
        togglePassword.addEventListener('click', function() {
            const type = passwordField.getAttribute('type') === 'password' ? 'text' : 'password';
            passwordField.setAttribute('type', type);
        });
    }
});