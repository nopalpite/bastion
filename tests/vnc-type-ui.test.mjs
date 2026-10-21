import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { sendText, textKeysyms } from '../static/js/vnc-type-text.mjs';

async function fixture() {
  const html = await readFile(new URL('../templates/vnc.html', import.meta.url), 'utf8');
  const nodes = new Map();
  class Element {
    style = {}; value = ''; disabled = false; textContent = ''; listeners = {};
    addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
    fire(name, detail = {}) { return Promise.all((this.listeners[name] || []).map(fn => fn({ detail }))); }
  }
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); } };
  let rfb;
  class RFB extends Element {
    events = []; viewOnly = false;
    constructor() { super(); rfb = this; }
    sendKey(...args) { this.events.push(args); }
    clipboardPasteFrom(text) { this.clipboard = text; }
  }
  const window = new Element();
  window.location = { protocol: 'https:', host: 'example.invalid', hostname: 'example.invalid' };
  const source = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
    .replace(/^\s*import .*;$/gm, '').replace(/{{[\s\S]*?}}/g, '""');
  new Function('RFB', 'sendText', 'textKeysyms', 'document', 'window', source)(RFB, sendText, textKeysyms, document, window);
  return { get: id => document.getElementById(id), rfb, window };
}

test('UI busy, duplicate click, success clearing and clipboard preserved', async () => {
  const { get, rfb } = await fixture();
  const button = get('vnc-type-send');
  assert.equal(button.disabled, true);
  rfb.fire('connect');
  get('vnc-clipboard-to-remote').value = 'ab';
  const pending = button.fire('click');
  assert.equal(button.disabled, true);
  assert.equal(get('vnc-clipboard-send').disabled, true);
  await button.fire('click');
  await pending;
  assert.equal(rfb.events.length, 4);
  assert.equal(get('vnc-clipboard-to-remote').value, '');
  assert.equal(button.disabled, false);
  get('vnc-clipboard-to-remote').value = 'clipboard\n';
  get('vnc-clipboard-send').fire('click');
  assert.equal(rfb.clipboard, 'clipboard\n');
});

test('UI rejects newline before sending and clears after transport failure', async () => {
  const { get, rfb } = await fixture();
  rfb.fire('connect');
  get('vnc-clipboard-to-remote').value = 'ab\n';
  await get('vnc-type-send').fire('click');
  assert.equal(rfb.events.length, 0);
  assert.match(get('vnc-clipboard-status').textContent, /contrôle/);
  get('vnc-clipboard-to-remote').value = 'ab';
  rfb.sendKey = () => { throw Error('must not be displayed'); };
  await get('vnc-type-send').fire('click');
  assert.equal(get('vnc-clipboard-to-remote').value, '');
  assert.match(get('vnc-clipboard-status').textContent, /partielle/);
  assert.equal(get('vnc-type-send').disabled, false);
});

test('UI cancellation on cancel, panel close/toggle, disconnect and pagehide', async () => {
  for (const trigger of ['vnc-type-cancel', 'vnc-clipboard-close', 'vnc-clipboard-toggle', 'disconnect', 'pagehide']) {
    const { get, rfb, window } = await fixture();
    rfb.fire('connect');
    get('vnc-clipboard-panel').style.display = 'block';
    get('vnc-clipboard-to-remote').value = 'abc';
    const pending = get('vnc-type-send').fire('click');
    if (trigger === 'disconnect') rfb.fire(trigger);
    else if (trigger === 'pagehide') window.fire(trigger);
    else get(trigger).fire('click');
    await pending;
    assert.equal(rfb.events.length, 2, trigger);
    assert.equal(get('vnc-clipboard-to-remote').value, '', trigger);
    assert.match(get('vnc-clipboard-status').textContent, /partielle/);
  }
});
