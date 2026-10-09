'use strict';

// The session token lives in sessionStorage and is sent as X-Admin-Session: no cookie, so no CSRF surface.
const form = document.getElementById('loginForm');
const button = document.getElementById('loginButton');

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    button.disabled = true;
    button.textContent = 'Signing in...';
    try {
        const data = new FormData(form);
        const username = String(data.get('username') || '');
        const session = await api('/login', {
            method: 'POST', noRedirect: true,
            body: { username, password: String(data.get('password') || '') },
        });
        sessionStorage.setItem(TOKEN_KEY, session.token);
        sessionStorage.setItem(USER_KEY, username);
        location.href = '/manage';
    } catch (err) {
        toast(err instanceof ApiError && err.status === 401 ? 'Wrong username or password' : errorText(err), 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Sign in';
    }
});

window.addEventListener('DOMContentLoaded', () => {
    if (!token()) return;
    api('/me', { noRedirect: true }).then(() => { location.href = '/manage'; }).catch(() => clearSession());
});
