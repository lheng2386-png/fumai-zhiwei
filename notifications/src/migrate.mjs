import { openDatabase } from './db.mjs';

const db = openDatabase();
const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all();
console.log(JSON.stringify({ ok: true, tables: tables.map(row => row.name) }, null, 2));
db.close();

