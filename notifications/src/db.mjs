import { mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';

const here = dirname(fileURLToPath(import.meta.url));
const migrationRoot = resolve(here, '../migrations');

export function openDatabase(path = process.env.FUMAI_DB_PATH || './var/fumai-notifications.sqlite3') {
  if (path !== ':memory:') mkdirSync(dirname(resolve(path)), { recursive: true });
  const db = new DatabaseSync(path);
  db.exec('PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;');
  const migrations = readdirSync(migrationRoot)
    .filter(name => /^\d+_.+\.sql$/.test(name) && !name.endsWith('.down.sql'))
    .sort();
  for (const migration of migrations) db.exec(readFileSync(resolve(migrationRoot, migration), 'utf8'));
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
