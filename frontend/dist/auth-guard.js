'use strict';
(() => {
  const loginUrl = `/login.html?return=${encodeURIComponent(location.pathname + location.search + location.hash)}`;
  const ready = fetch('/api/auth/me', { credentials: 'same-origin', cache: 'no-store' })
    .then(async response => {
      if (!response.ok) throw new Error('UNAUTHENTICATED');
      const { user } = await response.json();
      document.documentElement.classList.remove('auth-pending');
      document.documentElement.dataset.role = user.role;
      window.FumaiCurrentUser = user;
      window.dispatchEvent(new CustomEvent('fumai-auth-ready', { detail: user }));
      return user;
    })
    .catch(() => { location.replace(loginUrl); throw new Error('UNAUTHENTICATED'); });
  window.FumaiAuth = ready;

  addEventListener('DOMContentLoaded', () => {
    ready.then(user => {
      document.querySelectorAll('.header-avatar,.avatar').forEach(element => {
        element.textContent = (user.display_name || user.username).slice(0, 2).toUpperCase();
        element.title = `${user.display_name} · ${user.role}`;
      });
      const profile = document.querySelector('.profile div');
      if (profile) profile.innerHTML = `<strong>${escapeHtml(user.display_name)}</strong><small>${escapeHtml(user.role)} · ${escapeHtml(user.username)}</small>`;
      const topbar = document.querySelector('.topbar');
      if (topbar && !document.querySelector('#logout-button')) {
        const button = document.createElement('button');
        button.id = 'logout-button';
        button.className = 'fumai-logout';
        button.type = 'button';
        button.textContent = '退出';
        button.addEventListener('click', async () => {
          await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
          location.replace('/login.html');
        });
        topbar.append(button);
      }
    }).catch(() => {});
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }
})();
