import { mkdirSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';

const here = dirname(fileURLToPath(import.meta.url));
const migrationFile = resolve(here, '../migrations/001_notifications.sql');

export function openDatabase(path = process.env.FUMAI_DB_PATH || './var/fumai-notifications.sqlite3') {
  if (path !== ':memory:') mkdirSync(dirname(resolve(path)), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec('PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;');
  db.exec(readFileSync(migrationFile, 'utf8'));
  db.exec(readFileSync(resolve(here, '../migrations/002_account_email.sql'), 'utf8'));
  db.exec('CREATE TABLE IF NOT EXISTS schema_migration (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)');
  transaction(db, () => {
    if (!db.prepare('SELECT 1 FROM schema_migration WHERE name=?').get('003_model_monitoring')) {
      db.exec(readFileSync(resolve(here, '../migrations/003_model_monitoring.sql'), 'utf8'));
      db.prepare('INSERT INTO schema_migration(name,applied_at) VALUES(?,?)').run('003_model_monitoring', new Date().toISOString());
    }
  });
  return db;
}

export function transaction(db, work) {
  db.exec('BEGIN IMMEDIATE');
  try {
    const value = work();
    db.exec('COMMIT');
    return value;
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

