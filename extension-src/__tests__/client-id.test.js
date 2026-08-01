import { describe, it, expect, beforeEach } from 'vitest';

require('./helpers/chrome-mock');

const { getClientId } = require('../background.js');

describe('getClientId', () => {
  beforeEach(() => {
    chrome.storage.local._clear();
  });

  it('generates a 32-character hex id on first use', async () => {
    const id = await getClientId();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
  });

  it('persists the id so the browser keeps one stable capture identity', async () => {
    const first = await getClientId();
    const second = await getClientId();
    expect(second).toBe(first);
  });

  it('writes the id to storage so other call sites read the same value', async () => {
    const id = await getClientId();
    const stored = await chrome.storage.local.get({ clientId: null });
    expect(stored.clientId).toBe(id);
  });
});
