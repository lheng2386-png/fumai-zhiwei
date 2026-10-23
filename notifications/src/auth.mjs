import { createHash, randomBytes, randomUUID, scryptSync, timingSafeEqual } from 'node:crypto';

const USERNAME = /^[a-zA-Z0-9._-]{3,40}$/;
const ROLES = new Set(['admin', 'operator', 'viewer']);
const SESSION_COOKIE = 'fumai_session';

const utcNow = () => new Date().toISOString();

function required(value, name) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${name}不能为空`);
  return value.trim();
}

function hashPassword(password, salt) {
  return scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 }).toString('hex');
}

function hashToken(token) {
  return createHash('sha256').update(token).digest('hex');
}

function safeUser(row) {
  if (!row) return null;
  return {
    id: row.id,
    username: row.username,
    display_name: row.display_name,
    role: row.role,
    enabled: Boolean(row.enabled),
    created_at: row.created_at,
    updated_at: row.updated_at,
    last_login_at: row.last_login_at
  };
}

export function parseCookies(header = '') {
  return String(header).split(';').reduce((cookies, part) => {
    const index = part.indexOf('=');
    if (index < 1) return cookies;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    try { cookies[key] = decodeURIComponent(value); } catch { cookies[key] = value; }
    return cookies;
  }, {});
}

export class AuthService {
  constructor(db, options = {}) {
    this.db = db;
    this.sessionHours = Number(options.sessionHours || process.env.FUMAI_SESSION_HOURS || 12);
    this.secureCookie = options.secureCookie ?? /^https:\/\//i.test(process.env.FUMAI_PUBLIC_ORIGIN || '');
  }

  userCount() {
    return this.db.prepare('SELECT count(*) count FROM app_user').get().count;
  }

  ensureBootstrapFromEnv() {
    if (this.userCount()) return null;
    const username = process.env.FUMAI_BOOTSTRAP_ADMIN_USERNAME;
    const password = process.env.FUMAI_BOOTSTRAP_ADMIN_PASSWORD;
    if (!username || !password) return null;
    return this.saveUser({ username, password, display_name: '系统管理员', role: 'admin', enabled: true });
  }

  saveUser(input) {
    const username = required(input.username, '用户名');
    if (!USERNAME.test(username)) throw new Error('用户名只能包含字母、数字、点、下划线或短横线，长度 3–40');
    const normalized = username.toLowerCase();
    const displayName = required(input.display_name || username, '显示名称');
    const role = input.role || 'viewer';
    if (!ROLES.has(role)) throw new Error('用户角色无效');
    const existing = this.db.prepare('SELECT * FROM app_user WHERE username_normalized=?').get(normalized);
    const enabled = input.enabled === false ? 0 : 1;
    const timestamp = utcNow();

    if (existing) {
      if (existing.role === 'admin' && (!enabled || role !== 'admin')) {
        const activeAdmins = this.db.prepare("SELECT count(*) count FROM app_user WHERE role='admin' AND enabled=1").get().count;
        if (activeAdmins <= 1) throw new Error('不能停用或降级最后一个启用的管理员');
      }
      let salt = existing.password_salt;
      let passwordHash = existing.password_hash;
      if (input.password != null && input.password !== '') {
        this.validatePassword(input.password);
        salt = randomBytes(16).toString('hex');
        passwordHash = hashPassword(input.password, salt);
      }
      this.db.prepare(`UPDATE app_user SET username=?, display_name=?, role=?, enabled=?,
        password_salt=?, password_hash=?, updated_at=? WHERE id=?`)
        .run(username, displayName, role, enabled, salt, passwordHash, timestamp, existing.id);
      if (!enabled) this.db.prepare('DELETE FROM auth_session WHERE user_id=?').run(existing.id);
      return safeUser(this.db.prepare('SELECT * FROM app_user WHERE id=?').get(existing.id));
    }

    this.validatePassword(input.password);
    const id = input.id || randomUUID();
    const salt = randomBytes(16).toString('hex');
    this.db.prepare(`INSERT INTO app_user(
      id, username, username_normalized, display_name, role, password_salt,
      password_hash, enabled, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(id, username, normalized, displayName, role, salt, hashPassword(input.password, salt), enabled, timestamp, timestamp);
    return safeUser(this.db.prepare('SELECT * FROM app_user WHERE id=?').get(id));
  }

  validatePassword(password) {
    if (typeof password !== 'string' || password.length < 10 || password.length > 128) {
      throw new Error('密码长度必须为 10–128 个字符');
    }
  }

  listUsers() {
    return this.db.prepare('SELECT * FROM app_user ORDER BY enabled DESC, role, username_normalized').all().map(safeUser);
  }

  authenticate(username, password) {
    const normalized = String(username || '').trim().toLowerCase();
    const row = this.db.prepare('SELECT * FROM app_user WHERE username_normalized=?').get(normalized);
    const fallbackSalt = '00000000000000000000000000000000';
    const expectedHex = row?.password_hash || hashPassword('invalid-password', fallbackSalt);
    const actualHex = hashPassword(String(password || ''), row?.password_salt || fallbackSalt);
    const valid = timingSafeEqual(Buffer.from(expectedHex, 'hex'), Buffer.from(actualHex, 'hex'));
    if (!row || !valid || !row.enabled) return null;
    return safeUser(row);
  }

  createSession(userId) {
    const token = randomBytes(32).toString('base64url');
    const timestamp = utcNow();
    const expiresAt = new Date(Date.now() + this.sessionHours * 60 * 60 * 1000).toISOString();
    this.db.prepare('DELETE FROM auth_session WHERE expires_at<=?').run(timestamp);
    this.db.prepare(`INSERT INTO auth_session(token_hash,user_id,expires_at,created_at,last_seen_at)
      VALUES (?, ?, ?, ?, ?)`).run(hashToken(token), userId, expiresAt, timestamp, timestamp);
    this.db.prepare('UPDATE app_user SET last_login_at=?,updated_at=? WHERE id=?').run(timestamp, timestamp, userId);
    return { token, expires_at: expiresAt };
  }

  resolveRequest(req) {
    const token = parseCookies(req.headers.cookie)[SESSION_COOKIE];
    if (!token) return null;
    const timestamp = utcNow();
    const row = this.db.prepare(`SELECT u.*, s.expires_at FROM auth_session s
      JOIN app_user u ON u.id=s.user_id
      WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1`).get(hashToken(token), timestamp);
    if (!row) return null;
    this.db.prepare('UPDATE auth_session SET last_seen_at=? WHERE token_hash=?').run(timestamp, hashToken(token));
    return safeUser(row);
  }

  revokeRequest(req) {
    const token = parseCookies(req.headers.cookie)[SESSION_COOKIE];
    if (token) this.db.prepare('DELETE FROM auth_session WHERE token_hash=?').run(hashToken(token));
  }

  sessionCookie(token) {
    const maxAge = Math.max(60, Math.floor(this.sessionHours * 60 * 60));
    return `${SESSION_COOKIE}=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${this.secureCookie ? '; Secure' : ''}`;
  }

  clearCookie() {
    return `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${this.secureCookie ? '; Secure' : ''}`;
  }
}
