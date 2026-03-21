import { describe, it, expect, beforeEach } from 'vitest';

// Mock DOMPurify globally (content.js expects it as a global)
globalThis.DOMPurify = {
  sanitize(html) { return html; }, // pass through for testing
};

// jsdom doesn't support innerText — polyfill it as textContent for testing
if (!('innerText' in HTMLElement.prototype)) {
  Object.defineProperty(HTMLElement.prototype, 'innerText', {
    get() { return this.textContent; },
    set(v) { this.textContent = v; },
  });
}

// Mock chrome APIs
require('./helpers/chrome-mock');

// content.js is an IIFE — require it to run, then access exports via module.exports
const content = require('../content.js');

describe('findContentElement', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('returns body when no semantic elements are found', () => {
    document.body.innerHTML = '<div>Short text</div>';
    const result = content.findContentElement();
    expect(result.element).toBe(document.body);
    expect(result.method).toBe('body');
  });

  it('finds an article element with sufficient text', () => {
    document.body.innerHTML = `<article>${'x'.repeat(200)}</article>`;
    const result = content.findContentElement();
    expect(result.element.tagName).toBe('ARTICLE');
    expect(result.method).toBe('generic');
  });

  it('finds a main element with sufficient text', () => {
    document.body.innerHTML = `<main>${'x'.repeat(200)}</main>`;
    const result = content.findContentElement();
    expect(result.element.tagName).toBe('MAIN');
    expect(result.method).toBe('generic');
  });

  it('skips generic elements with too little text', () => {
    document.body.innerHTML = '<article>short</article>';
    const result = content.findContentElement();
    expect(result.method).toBe('body');
  });
});

describe('elementToMarkdown', () => {
  it('converts headings to markdown', () => {
    const div = document.createElement('div');
    div.innerHTML = '<h1>Title</h1><h2>Subtitle</h2>';
    const md = content.elementToMarkdown(div);
    expect(md).toContain('# Title');
    expect(md).toContain('## Subtitle');
  });

  it('converts bold and italic', () => {
    const div = document.createElement('div');
    div.innerHTML = '<p><strong>bold</strong> and <em>italic</em></p>';
    const md = content.elementToMarkdown(div);
    expect(md).toContain('**bold**');
    expect(md).toContain('_italic_');
  });

  it('converts links to markdown format', () => {
    const div = document.createElement('div');
    div.innerHTML = '<a href="https://example.com">click</a>';
    const md = content.elementToMarkdown(div);
    expect(md).toContain('[click](https://example.com)');
  });

  it('converts lists to markdown', () => {
    const div = document.createElement('div');
    div.innerHTML = '<ul><li>one</li><li>two</li></ul>';
    const md = content.elementToMarkdown(div);
    expect(md).toContain('- one');
    expect(md).toContain('- two');
  });

  it('converts code elements', () => {
    const div = document.createElement('div');
    div.innerHTML = '<code>const x = 1</code>';
    const md = content.elementToMarkdown(div);
    expect(md).toContain('`const x = 1`');
  });

  it('handles empty elements', () => {
    const div = document.createElement('div');
    div.innerHTML = '';
    const md = content.elementToMarkdown(div);
    expect(md).toBe('');
  });

  it('normalises excessive newlines', () => {
    const div = document.createElement('div');
    div.innerHTML = '<p>one</p><p></p><p></p><p></p><p>two</p>';
    const md = content.elementToMarkdown(div);
    // Should not have more than 2 consecutive newlines
    expect(md).not.toMatch(/\n{3,}/);
  });
});
