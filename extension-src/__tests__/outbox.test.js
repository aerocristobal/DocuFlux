import { describe, it, expect, beforeEach } from 'vitest';
import 'fake-indexeddb/auto';

// Mock chrome APIs
require('./helpers/chrome-mock');

// Import the module to get access to IndexedDB constants
const bg = require('../background.js');

// The outbox functions aren't exported yet, so we test via IndexedDB directly
// using the same DB name and version the extension uses.
describe('IndexedDB outbox schema', () => {
  it('creates the outbox database with correct version', () => {
    expect(bg.OUTBOX_DB_VERSION).toBe(2);
    expect(bg.OUTBOX_STORE).toBe('pending_pages');
    expect(bg.IMAGE_BLOB_STORE).toBe('image_blobs');
  });

  it('opens the database with expected object stores', async () => {
    const db = await new Promise((resolve, reject) => {
      const req = indexedDB.open(bg.OUTBOX_DB_NAME, bg.OUTBOX_DB_VERSION);
      req.onupgradeneeded = e => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(bg.OUTBOX_STORE)) {
          const store = db.createObjectStore(bg.OUTBOX_STORE, { keyPath: 'sequence', autoIncrement: true });
          store.createIndex('sessionId', 'sessionId', { unique: false });
        }
        if (!db.objectStoreNames.contains(bg.IMAGE_BLOB_STORE)) {
          db.createObjectStore(bg.IMAGE_BLOB_STORE, { keyPath: 'key' });
        }
      };
      req.onsuccess = e => resolve(e.target.result);
      req.onerror = e => reject(e.target.error);
    });

    expect(db.objectStoreNames.contains(bg.OUTBOX_STORE)).toBe(true);
    expect(db.objectStoreNames.contains(bg.IMAGE_BLOB_STORE)).toBe(true);
    db.close();
  });

  it('can write and read from pending_pages store', async () => {
    const db = await new Promise((resolve, reject) => {
      const req = indexedDB.open(bg.OUTBOX_DB_NAME + '_write_test', bg.OUTBOX_DB_VERSION);
      req.onupgradeneeded = e => {
        const db = e.target.result;
        const store = db.createObjectStore(bg.OUTBOX_STORE, { keyPath: 'sequence', autoIncrement: true });
        store.createIndex('sessionId', 'sessionId', { unique: false });
      };
      req.onsuccess = e => resolve(e.target.result);
      req.onerror = e => reject(e.target.error);
    });

    // Write a record
    await new Promise((resolve, reject) => {
      const tx = db.transaction(bg.OUTBOX_STORE, 'readwrite');
      const req = tx.objectStore(bg.OUTBOX_STORE).add({
        sessionId: 'test-session-1',
        pageData: { text: 'hello world' },
        addedAt: Date.now(),
        retryCount: 0,
      });
      req.onsuccess = () => resolve(req.result);
      req.onerror = e => reject(e.target.error);
    });

    // Read it back
    const records = await new Promise((resolve, reject) => {
      const tx = db.transaction(bg.OUTBOX_STORE, 'readonly');
      const idx = tx.objectStore(bg.OUTBOX_STORE).index('sessionId');
      const req = idx.getAll('test-session-1');
      req.onsuccess = () => resolve(req.result);
      req.onerror = e => reject(e.target.error);
    });

    expect(records).toHaveLength(1);
    expect(records[0].pageData.text).toBe('hello world');
    expect(records[0].retryCount).toBe(0);
    db.close();
  });

  it('can write and read from image_blobs store', async () => {
    const db = await new Promise((resolve, reject) => {
      const req = indexedDB.open(bg.OUTBOX_DB_NAME + '_blob_test', bg.OUTBOX_DB_VERSION);
      req.onupgradeneeded = e => {
        const db = e.target.result;
        db.createObjectStore(bg.IMAGE_BLOB_STORE, { keyPath: 'key' });
      };
      req.onsuccess = e => resolve(e.target.result);
      req.onerror = e => reject(e.target.error);
    });

    const key = 'img_test_123';
    const b64Data = 'data:image/jpeg;base64,/9j/fakedata';

    // Write
    await new Promise((resolve, reject) => {
      const tx = db.transaction(bg.IMAGE_BLOB_STORE, 'readwrite');
      const req = tx.objectStore(bg.IMAGE_BLOB_STORE).put({ key, b64: b64Data, addedAt: Date.now() });
      req.onsuccess = () => resolve();
      req.onerror = e => reject(e.target.error);
    });

    // Read
    const record = await new Promise((resolve, reject) => {
      const tx = db.transaction(bg.IMAGE_BLOB_STORE, 'readonly');
      const req = tx.objectStore(bg.IMAGE_BLOB_STORE).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = e => reject(e.target.error);
    });

    expect(record.b64).toBe(b64Data);

    // Delete
    await new Promise((resolve, reject) => {
      const tx = db.transaction(bg.IMAGE_BLOB_STORE, 'readwrite');
      const req = tx.objectStore(bg.IMAGE_BLOB_STORE).delete(key);
      req.onsuccess = () => resolve();
      req.onerror = e => reject(e.target.error);
    });

    // Verify deleted
    const deleted = await new Promise((resolve, reject) => {
      const tx = db.transaction(bg.IMAGE_BLOB_STORE, 'readonly');
      const req = tx.objectStore(bg.IMAGE_BLOB_STORE).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = e => reject(e.target.error);
    });

    expect(deleted).toBeUndefined();
    db.close();
  });
});
