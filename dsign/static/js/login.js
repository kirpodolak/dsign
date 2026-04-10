document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('login-form');
    const errorEl = document.getElementById('login-error');
    const passwordField = document.getElementById('password');
    const toggleBtn = document.getElementById('password-toggle');

    if (!form || !errorEl) return;

    function setInlineError(message) {
        if (!message) {
            errorEl.textContent = '';
            errorEl.hidden = true;
            return;
        }
        errorEl.textContent = message;
        errorEl.hidden = false;
    }

    function setPasswordVisible(visible) {
        if (!passwordField || !toggleBtn) return;
        passwordField.setAttribute('type', visible ? 'text' : 'password');
        toggleBtn.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
        toggleBtn.setAttribute('aria-pressed', visible ? 'true' : 'false');
        const icon = toggleBtn.querySelector('i');
        if (icon) {
            icon.classList.toggle('fa-eye', !visible);
            icon.classList.toggle('fa-eye-slash', visible);
        }
    }

    if (toggleBtn && passwordField) {
        toggleBtn.addEventListener('click', () => {
            const next = passwordField.getAttribute('type') === 'password';
            setPasswordVisible(next);
        });
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        setInlineError('');

        const username = document.getElementById('username')?.value.trim() ?? '';
        const password = passwordField?.value.trim() ?? '';

        if (!username || !password) {
            setInlineError('Please fill in all fields.');
            return;
        }

        try {
            const formData = new FormData(form);
            const response = await fetch(form.action, {
                method: 'POST',
                body: formData,
                credentials: 'include',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success && data.redirect) {
                    window.location.href = data.redirect;
                } else {
                    setInlineError(data.message || 'Login succeeded but no redirect target.');
                }
            } else {
                const errorData = await response.json().catch(() => ({}));
                setInlineError(errorData.error || 'Login failed. Please try again.');
            }
        } catch (error) {
            setInlineError('Connection error: ' + (error?.message || 'unknown'));
        }
    });
});
