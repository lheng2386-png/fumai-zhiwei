import { openDatabase } from './db.mjs';
import { createProviders } from './providers.mjs';
import { NotificationWorker } from './worker.mjs';

const db = openDatabase();
const worker = new NotificationWorker(db, createProviders());
const once = process.argv.includes('--once');

do {
  const result = await worker.runUntilIdle();
  if (result.processed) console.log(JSON.stringify({ at: new Date().toISOString(), ...result }));
  if (once) break;
  await new Promise(resolve => setTimeout(resolve, 1000));
} while (true);

db.close();

