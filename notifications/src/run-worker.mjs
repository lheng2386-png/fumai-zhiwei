import { openDatabase } from './db.mjs';
import { createProviders } from './providers.mjs';
import { NotificationWorker } from './worker.mjs';
import { OperationsService } from './operations.mjs';

const db = openDatabase();
const worker = new NotificationWorker(db, createProviders());
const operations = new OperationsService(db);
const once = process.argv.includes('--once');
const maintenanceIntervalMs = Math.max(60_000, Number(process.env.FUMAI_MAINTENANCE_INTERVAL_MS || 6 * 60 * 60 * 1000));
let nextMaintenanceAt = 0;

do {
  if (Date.now() >= nextMaintenanceAt) {
    const maintenance = operations.purgeExpired();
    console.log(JSON.stringify({ at: new Date().toISOString(), maintenance }));
    nextMaintenanceAt = Date.now() + maintenanceIntervalMs;
  }
  const result = await worker.runUntilIdle();
  if (result.processed) console.log(JSON.stringify({ at: new Date().toISOString(), ...result }));
  if (once) break;
  await new Promise(resolve => setTimeout(resolve, 1000));
} while (true);

db.close();

