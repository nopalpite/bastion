import keysyms from '../novnc/core/input/keysymdef.js';

export function textKeysyms(text) {
  // Validate the entire payload before emitting any event. Never trim a token.
  if (typeof text !== 'string' || !text || text.length > 2048 ||
      /[\p{Cc}\p{Cf}\p{Cs}\u2028\u2029]/u.test(text)) {
    throw new Error('Une seule ligne sans caractères de contrôle est autorisée.');
  }
  const chars = Array.from(text);
  if (chars.length > 1024) throw new Error('Maximum : 1024 caractères.');
  return chars.map(char => keysyms.lookup(char.codePointAt(0)));
}

export async function sendText(rfb, text, {
  connected, signal, pause = ms => new Promise(resolve => setTimeout(resolve, ms)),
}) {
  const symbols = textKeysyms(text);
  text = '';
  try {
    for (let i = 0; i < symbols.length; i++) {
      if (signal?.aborted || !connected() || rfb.viewOnly) return false;
      // No physical code: standard RFB keysyms, even on QEMU-capable servers.
      // Synchronous pair: cancellation never leaves a key down between waits.
      try {
        rfb.sendKey(symbols[i], undefined, true);
      } finally {
        rfb.sendKey(symbols[i], undefined, false);
      }
      if (i + 1 < symbols.length) await pause(35);
    }
    return true; // Sent, not acknowledged by the remote application.
  } finally {
    symbols.fill(0);
  }
}
