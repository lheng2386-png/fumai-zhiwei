'use strict';
(() => {
  const loginForm = document.querySelector('#login-form');
  const registerForm = document.querySelector('#register-form');
  const error = document.querySelector('#error');
  const requested = new URLSearchParams(location.search).get('return') || '/';
  const returnTo = requested.startsWith('/') && !requested.startsWith('//') ? requested : '/';

  fetch('/api/auth/me', { credentials: 'same-origin' }).then(response => {
    if (response.ok) location.replace(returnTo);
  }).catch(() => {});

  document.querySelectorAll('[data-panel]').forEach(tab => tab.addEventListener('click', () => {
    const target = tab.dataset.panel;
    loginForm.hidden = target !== 'login-form';
    registerForm.hidden = target !== 'register-form';
    document.querySelectorAll('[data-panel]').forEach(item => item.classList.toggle('active', item === tab));
    error.textContent = '';
    document.querySelector(`#${target} input`)?.focus();
  }));

  async function submit(form, endpoint, loadingText) {
    const button = form.querySelector('button[type="submit"]');
    const original = button.textContent;
    error.textContent = '';
    button.disabled = true;
    button.textContent = loadingText;
    try {
      const input = Object.fromEntries(new FormData(form));
      if (endpoint.endsWith('/register')) {
        if (input.password !== input.password_confirm) throw new Error('两次输入的密码不一致');
        delete input.password_confirm;
      }
      const response = await fetch(endpoint, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input)
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || '操作失败');
      location.replace(returnTo);
    } catch (cause) {
      error.textContent = cause.message;
      button.disabled = false;
      button.textContent = original;
    }
  }

  loginForm.addEventListener('submit', event => {
    event.preventDefault();
    submit(loginForm, '/api/auth/login', '正在登录…');
  });
  registerForm.addEventListener('submit', event => {
    event.preventDefault();
    submit(registerForm, '/api/auth/register', '正在创建…');
  });
})();
