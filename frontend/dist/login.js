'use strict';
(() => {
  const form = document.querySelector('#login-form');
  const error = document.querySelector('#error');
  const requested = new URLSearchParams(location.search).get('return') || '/';
  const returnTo = requested.startsWith('/') && !requested.startsWith('//') ? requested : '/';

  fetch('/api/auth/me', { credentials: 'same-origin' }).then(response => {
    if (response.ok) location.replace(returnTo);
  }).catch(() => {});

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    error.textContent = '';
    button.disabled = true;
    button.textContent = '正在登录…';
    try {
      const input = Object.fromEntries(new FormData(form));
      const response = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input)
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || '登录失败');
      location.replace(returnTo);
    } catch (cause) {
      error.textContent = cause.message;
      button.disabled = false;
      button.textContent = '进入伏脉智维';
    }
  });
})();
