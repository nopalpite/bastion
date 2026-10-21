import test from 'node:test';
import assert from 'node:assert/strict';

test('reject controls, multiline, malformed Unicode and length before sending', async () => {
  const { textKeysyms } = await import(moduleUrl);
  for (const text of ['', 'x\n', '\r', '\t', '\b', '\x1b', '\x7f', '\x85', '\u2028', '\u2029', '\ud800', 'a'.repeat(1025)]) {
    assert.throws(() => textKeysyms(text));
  }
  assert.equal(textKeysyms('😀'.repeat(1024)).length, 1024);
});
test('paced pairs, cancellation, disconnection and view-only', async () => {
  const { sendText } = await import(moduleUrl);
  for (const stop of ['none', 'cancel', 'disconnect', 'viewOnly']) {
    const events = [];
    const controller = new AbortController();
    let connected = true;
    let waits = 0;
    const rfb = { viewOnly: false, sendKey: (...args) => events.push(args) };
    const result = await sendText(rfb, 'abc', {
      signal: controller.signal, connected: () => connected,
      pause: async ms => {
        assert.equal(ms, 35); waits++;
        if (stop === 'cancel') controller.abort();
        if (stop === 'disconnect') connected = false;
        if (stop === 'viewOnly') rfb.viewOnly = true;
      }
    });
    const n = stop === 'none' ? 3 : 1;
    assert.equal(result, stop === 'none');
    assert.equal(waits, stop === 'none' ? 2 : 1);
    assert.deepEqual(events, Array.from('abc'.slice(0, n)).flatMap(c => [[c.charCodeAt(0), undefined, true], [c.charCodeAt(0), undefined, false]]));
  }
});

test('pre-abort/disconnect, invalid input and release on send failure', async () => {
  const { sendText } = await import(moduleUrl);
  const events = [];
  const rfb = { sendKey: (...args) => events.push(args) };
  assert.equal(await sendText(rfb, 'a', { connected: () => false }), false);
  assert.equal(await sendText(rfb, 'a', { connected: () => true, signal: AbortSignal.abort() }), false);
  await assert.rejects(sendText(rfb, 'a\n', { connected: () => true }));
  assert.deepEqual(events, []);
  rfb.sendKey = (...args) => { events.push(args); if (args[2]) throw Error('transport'); };
  await assert.rejects(sendText(rfb, 'a', { connected: () => true }));
  assert.deepEqual(events, [[97, undefined, true], [97, undefined, false]]);
});

const moduleUrl = new URL('../static/js/vnc-type-text.mjs', import.meta.url);
test('ASCII punctuation, Latin-1 and Unicode keysyms', async () => {
  const { textKeysyms } = await import(moduleUrl);
  assert.deepEqual(textKeysyms('Az09-_.:/+=@! éœ€😀'), [65,122,48,57,45,95,46,58,47,43,61,64,33,32,233,0x13bd,0x20ac,0x101f600]);
});
