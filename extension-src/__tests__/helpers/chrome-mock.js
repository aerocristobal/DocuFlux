/**
 * Mock chrome.* APIs for testing extension code in Node.js.
 */

const storage = {};

const chrome = {
  storage: {
    local: {
      get(keys, callback) {
        const result = {};
        if (typeof keys === 'string') {
          result[keys] = storage[keys];
        } else if (Array.isArray(keys)) {
          for (const k of keys) result[k] = storage[k];
        } else if (typeof keys === 'object') {
          for (const [k, def] of Object.entries(keys)) {
            result[k] = storage[k] !== undefined ? storage[k] : def;
          }
        }
        if (callback) callback(result);
        return Promise.resolve(result);
      },
      set(items, callback) {
        Object.assign(storage, items);
        if (callback) callback();
        return Promise.resolve();
      },
      _clear() { for (const k of Object.keys(storage)) delete storage[k]; },
    },
  },
  runtime: {
    lastError: null,
    sendMessage(msg, callback) { if (callback) callback({}); },
    onMessage: { addListener() {} },
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
  },
  tabs: {
    query(opts, callback) { callback([{ id: 1, url: 'https://example.com' }]); },
    sendMessage(tabId, msg, callback) { if (callback) callback({}); },
    captureVisibleTab(windowId, opts, callback) {
      callback('data:image/jpeg;base64,/9j/mock');
    },
  },
  scripting: {
    executeScript() { return Promise.resolve([{}]); },
  },
  action: {
    onClicked: { addListener() {} },
  },
};

if (typeof globalThis !== 'undefined') {
  globalThis.chrome = chrome;
}

module.exports = chrome;
