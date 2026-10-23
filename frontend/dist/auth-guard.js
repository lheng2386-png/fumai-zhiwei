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
      if (topbar && !document.querySelector('.fumai-account')) createAccountMenu(topbar, user);
    }).catch(() => {});
  });

  function createAccountMenu(topbar, user) {
    if (!document.querySelector('#fumai-account-style')) {
      const style = document.createElement('style');
      style.id = 'fumai-account-style';
      style.textContent = `.fumai-account{position:relative;display:flex;align-items:center}.fumai-account-button{width:38px;height:38px;border:0;border-radius:50%;display:grid;place-items:center;background:#e9efe9;color:#235444;font:700 13px/1 inherit;cursor:pointer}.fumai-account-button:hover,.fumai-account-button:focus-visible{outline:2px solid #72a88f;outline-offset:2px}.fumai-account-menu{position:absolute;z-index:30;right:0;top:calc(100% + 10px);width:210px;padding:8px;background:#fff;border:1px solid #dce3de;border-radius:13px;box-shadow:0 16px 38px #183c3026;opacity:0;visibility:hidden;transform:translateY(-5px);transition:.16s ease}.fumai-account:hover .fumai-account-menu,.fumai-account:focus-within .fumai-account-menu,.fumai-account.open .fumai-account-menu{opacity:1;visibility:visible;transform:none}.fumai-account-summary{padding:10px 11px;border-bottom:1px solid #edf0ee;margin-bottom:5px}.fumai-account-summary strong,.fumai-account-summary small{display:block}.fumai-account-summary strong{color:#173b31;font-size:14px}.fumai-account-summary small{color:#7a8983;font-size:11px;margin-top:4px}.fumai-account-logout{width:100%;border:0;border-radius:8px;padding:10px 11px;text-align:left;background:#fff;color:#4d5d57;font:inherit;font-size:13px;cursor:pointer}.fumai-account-logout:hover,.fumai-account-logout:focus-visible{background:#f0f4f1;color:#963d38;outline:none}.fumai-account-logout span{display:inline-block;margin-right:9px;font-size:17px;vertical-align:-1px}`;
      document.head.append(style);
    }
    const initials = (user.display_name || user.username).slice(0, 2).toUpperCase();
    const account = document.createElement('div');
    account.className = 'fumai-account';
    account.innerHTML = `<button class="fumai-account-button" type="button" aria-haspopup="menu" aria-expanded="false" title="账号菜单">${escapeHtml(initials)}</button><div class="fumai-account-menu" role="menu"><div class="fumai-account-summary"><strong>${escapeHtml(user.display_name)}</strong><small>${escapeHtml(user.username)} · ${escapeHtml(user.role)}</small></div><button class="fumai-account-logout" type="button" role="menuitem"><span aria-hidden="true">↪</span>退出账号</button></div>`;
    const oldAvatar = topbar.querySelector('.header-avatar');
    if (oldAvatar) oldAvatar.replaceWith(account); else topbar.append(account);
    const trigger = account.querySelector('.fumai-account-button');
    trigger.addEventListener('click', () => {
      account.classList.toggle('open');
      trigger.setAttribute('aria-expanded', String(account.classList.contains('open')));
    });
    account.querySelector('.fumai-account-logout').addEventListener('click', async () => {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
      location.replace('/login.html');
    });
    document.addEventListener('click', event => {
      if (!account.contains(event.target)) {
        account.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
      }
    });
    account.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        account.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.focus();
      }
    });
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }
})();
