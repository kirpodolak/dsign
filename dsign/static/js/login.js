import { showAlert, showError } from './utils/alerts.js';

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('login-form');
    const errorDiv = document.getElementById('login-error');
    const passwordField = document.getElementById('password');

    if (!form || !errorDiv) {
        console.error('Login form elements not found');
        return;
    }

    function displayError(message) {
        errorDiv.textContent = message;
        errorDiv.style.display = 'block';
        showError('Login Error', message);
    }

    form.addEventListener('submit', async function(e) {
        e.preventDefault();
        errorDiv.textContent = '';
        errorDiv.style.display = 'none';
        
        const username = document.getElementById('username').value.trim();
        const password = passwordField.value.trim();
        
        if (!username || !password) {
            displayError('Please fill in all fields');
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
                    displayError(data.message || 'Login successful but no redirect');
                }
            } else {
                const errorData = await response.json().catch(() => ({}));
                displayError(errorData.error || 'Login failed. Please try again.');
            }
        } catch (error) {
            displayError('Connection error: ' + error.message);
            console.error('Login error:', error);
        }
    });

    // Add show/hide password toggle
    if (passwordField) {
        const togglePassword = document.createElement('button');
        togglePassword.type = 'button';
        togglePassword.innerHTML = '👁️';
        togglePassword.style.cssText = `
            background: none;
            border: none;
            cursor: pointer;
            margin-left: 5px;
            padding: 0;
            font-size: 1em;
        `;
        
        passwordField.insertAdjacentElement('afterend', togglePassword);
        
        togglePassword.addEventListener('click', function() {
            const type = passwordField.getAttribute('type') === 'password' ? 'text' : 'password';
            passwordField.setAttribute('type', type);
            togglePassword.setAttribute('aria-label', 
                type === 'password' ? 'Show password' : 'Hide password');
        });
    }
});

export function initializeLogin() {
    document.addEventListener('DOMContentLoaded', () => {
        console.log('Login module initialized');
    });
}
