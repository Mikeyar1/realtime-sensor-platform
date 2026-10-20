const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const events = [];
const sent = [];
let socket;
class WebSocketMock {
  static OPEN = 1;
  constructor() { this.readyState = 1; socket = this; }
  send(message) { sent.push(JSON.parse(message)); }
}
const context = {
  console,
  WebSocket: WebSocketMock,
  location: { hostname: 'localhost' },
  document: { getElementById: () => null },
  setTimeout: () => {},
};
context.window = context;
context.LabReview = {
  EventBus: { emit: (name, value) => events.push({ name, value }) },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../js/core/stream-router.js'), 'utf8'), context);

const router = context.LabReview.StreamRouter;
router.connect();
socket.onopen();
assert.equal(sent[0].type, 'system.get_state');
const receive = message => socket.onmessage({ data: JSON.stringify(message) });
receive({ type: 'session.state', state: 'idle' });
assert.equal(events.find(event => event.name === 'session-state-changed').value.state, 'idle');
receive({ type: 'streams.catalog', streams: [
  { name: 'V_300', type: 'VirTraEvents' },
  { name: 'virtra_lsl', type: 'VirTra Events' },
  { name: 'SessionInfo', type: 'Markers' },
] });
assert.equal(sent.some(message => message.type === 'streams.subscribe'), false);
receive({ type: 'session.virtra_event', event_type: 'Shot Fired' });
assert.equal(events.filter(event => event.name === 'virtra-event').length, 1);
console.log('Stream router event and subscription checks passed');
