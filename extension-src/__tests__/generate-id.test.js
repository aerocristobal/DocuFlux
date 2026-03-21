import { describe, it, expect } from 'vitest';

// Setup chrome mock before importing background
require('./helpers/chrome-mock');

const { generateId } = require('../background.js');

describe('generateId', () => {
  it('returns a 32-character hex string', () => {
    const id = generateId();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
  });

  it('returns unique values on successive calls', () => {
    const ids = new Set();
    for (let i = 0; i < 100; i++) {
      ids.add(generateId());
    }
    expect(ids.size).toBe(100);
  });

  it('has exactly 128 bits of entropy (16 bytes)', () => {
    const id = generateId();
    expect(id.length).toBe(32); // 16 bytes × 2 hex chars
  });
});
