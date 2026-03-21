import { describe, it, expect } from 'vitest';

require('./helpers/chrome-mock');

const { base64SizeKB } = require('../background.js');

describe('base64SizeKB', () => {
  it('calculates size correctly for a data URL', () => {
    // 1024 bytes of base64 data = 1024 * 0.75 = 768 bytes = 0.75 KB
    const fakeB64 = 'data:image/png;base64,' + 'A'.repeat(1024);
    const sizeKB = base64SizeKB(fakeB64);
    expect(sizeKB).toBeCloseTo(0.75, 1);
  });

  it('calculates size for a large image', () => {
    // 1MB of base64 chars ≈ 750KB decoded
    const fakeB64 = 'data:image/jpeg;base64,' + 'A'.repeat(1024 * 1024);
    const sizeKB = base64SizeKB(fakeB64);
    expect(sizeKB).toBeCloseTo(768, 0);
  });

  it('handles raw base64 without data URL prefix', () => {
    const rawB64 = 'A'.repeat(4000);
    const sizeKB = base64SizeKB(rawB64);
    expect(sizeKB).toBeCloseTo(4000 * 0.75 / 1024, 1);
  });
});
