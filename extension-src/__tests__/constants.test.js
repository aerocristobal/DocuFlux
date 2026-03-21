import { describe, it, expect } from 'vitest';

require('./helpers/chrome-mock');

const bg = require('../background.js');

describe('background.js constants', () => {
  it('exports expected constants', () => {
    expect(bg.DEFAULT_SERVER_URL).toBe('http://localhost:5000');
    expect(bg.OUTBOX_DB_NAME).toBe('docuflux_outbox');
    expect(bg.OUTBOX_DB_VERSION).toBe(2);
    expect(bg.OUTBOX_STORE).toBe('pending_pages');
    expect(bg.IMAGE_BLOB_STORE).toBe('image_blobs');
    expect(bg.MAX_IMAGE_SIZE_KB).toBe(2048);
    expect(bg.SEPARATE_UPLOAD_THRESHOLD_KB).toBe(500);
    expect(bg.OUTBOX_MAX_RETRIES).toBe(5);
  });
});
