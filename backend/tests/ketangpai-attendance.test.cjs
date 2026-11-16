const assert = require('node:assert/strict');
const { test } = require('node:test');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../integrations/ketangpai/node_modules/typescript');

const root = path.join(__dirname, '../integrations/ketangpai');

function loadAttendance(responses, options = {}) {
  const events = new EventEmitter();
  const requests = [];
  const results = [];
  events.on('签到success', c => results.push({ success: true, user: c.friendlyName }));
  events.on('签到failure', (c, reason) => results.push({ success: false, user: c.friendlyName, reason }));
  const credentials = [{ friendlyName: 'fixture-1' }, { friendlyName: 'fixture-2' }];
  const stubs = {
    './events': { 签到Events: events },
    './auth': { credentials },
    './runtimeConfig': { DEFAULT_LATITUDE: '', DEFAULT_LONGITUDE: '', ENABLE_IGNORE_QRCODE_EXPIRE: options.ignoreExpire !== false },
    './util': {
      fakeIp: { reset() {} }, getAuthenticatedHeaders: () => ({}), getReqtimestamp: () => 123,
      fancyFetch: async (url, options) => {
        requests.push({ url, body: options.body });
        const value = responses.shift();
        if (value instanceof Error) throw value;
        assert.notEqual(value, undefined, 'unexpected request');
        return { json: async () => value };
      },
    },
    './logger': { LabelledLogger: class { info() {} warn() {} error() {} } },
  };
  function evaluate(filename, extra = '') {
    const module = { exports: {} };
    const code = ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8') + extra, {
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
    }).outputText;
    vm.runInNewContext(code, { module, exports: module.exports, require: name => {
      assert.ok(name in stubs, `unexpected dependency ${name}`); return stubs[name];
    }, setTimeout, clearTimeout }, { filename });
    return module.exports;
  }
  stubs['./attendanceSemantics'] = evaluate('attendanceSemantics.ts');
  const impl = evaluate('签到.ts', '\nexports.testing = { process数字签到, processGps签到, process签入签出签到, process二维码签到, processQrcode签到, setPendingQrcodeId: (id) => { pendingQrcode签到Id = id; qrcodeCheckinStarted = false; }, pending: () => ({hasPending签到, 签到ProgressCount, qrcode签到Lock, pendingQrcode签到Id}) };');
  return { ...impl.testing, requests, results };
}

const course = { friendlyName: 'fixture', latitude: '31.2', longitude: '121.5' };

test('GPS and checkout send numeric semantics and accept status without data.state', async () => {
  for (const [method, unusual] of [['processGps签到', 1], ['process签入签出签到', 2]]) {
    const app = loadAttendance([{ status: 1 }, { status: 1, data: { state: 42 } }]);
    await app[method](course, 'attendance-fixture');
    assert.equal(app.requests.length, 2);
    assert.ok(app.requests.every(r => r.body.unusual === unusual));
    assert.ok(app.results.every(r => r.success));
    assert.equal(app.pending().签到ProgressCount, 0);
  }
});

test('missing or invalid location does not submit any request', async () => {
  for (const location of [{}, { latitude: '91', longitude: '121' }, { latitude: 'NaN', longitude: '10' }]) {
    const app = loadAttendance([]);
    await app.processGps签到({ friendlyName: 'fixture', ...location }, 'fixture');
    assert.equal(app.requests.length, 0);
    assert.equal(app.results.length, 2);
    assert.ok(app.results.every(r => !r.success));
  }
});

test('digit code extraction is checked before submitting checkin', async () => {
  const app = loadAttendance([{ status: 1, data: { data: { code: '0037' } } }, { status: 1 }, { status: 0, data: { state: 1 } }]);
  await app.process数字签到(course, 'fixture');
  assert.equal(app.requests[1].body.code, '0037');
  assert.equal(app.requests[1].body.unusual, 0);
  assert.equal(app.results.filter(r => r.success).length, 1);
  const missing = loadAttendance([{ status: 0, message: 'fixture rejected' }]);
  await missing.process数字签到(course, 'fixture');
  assert.equal(missing.requests.length, 1);
  assert.ok(missing.results.every(r => !r.success));
});

test('QR and non-QR network failures release batch state and QR lock', async () => {
  const app = loadAttendance([new Error('offline'), { status: 1 }, { status: 1 }, { status: 1 }]);
  await app.processQrcode签到('fixture-ticket', 'fixture-expire', 'fixture-sign');
  assert.equal(app.pending().qrcode签到Lock, false);
  assert.equal(app.pending().签到ProgressCount, 0);
  await app.processQrcode签到('fixture-ticket', 'fixture-expire', 'fixture-sign');
  assert.equal(app.requests.length, 4);
  const gps = loadAttendance([new Error('offline'), { status: 1 }]);
  await gps.processGps签到(course, 'fixture');
  assert.equal(gps.results.length, 2);
  assert.equal(gps.pending().签到ProgressCount, 0);
});

test('QR auto path uses AttenceApi/checkin without ticket fields', async () => {
  const app = loadAttendance([{ status: 1 }, { status: 1 }]);
  await app.process二维码签到(course, 'qr-attendance-1');
  assert.equal(app.requests.length, 2);
  assert.ok(app.requests.every(r => r.url.endsWith('/AttenceApi/checkin')));
  assert.ok(app.requests.every(r => r.body.id === 'qr-attendance-1'));
  assert.ok(app.requests.every(r => r.body.code === ''));
  assert.ok(app.requests.every(r => r.body.unusual === 0));
  assert.ok(app.requests.every(r => r.body.appid === ''));
  assert.ok(app.requests.every(r => !('ticketid' in r.body)));
  assert.ok(app.results.every(r => r.success));
  assert.equal(app.pending().pendingQrcode签到Id, null);
});

test('manual QR submit uses checkin when pending attendance id exists', async () => {
  const app = loadAttendance([{ status: 1 }, { status: 1 }]);
  app.setPendingQrcodeId('pending-qr-9');
  await app.processQrcode签到('ticket', 'expire', 'sign');
  assert.equal(app.requests.length, 2);
  assert.ok(app.requests.every(r => r.url.endsWith('/AttenceApi/checkin')));
  assert.ok(app.requests.every(r => r.body.id === 'pending-qr-9'));
  assert.ok(app.requests.every(r => !('ticketid' in r.body)));
  assert.equal(app.pending().qrcode签到Lock, false);
});

test('manual QR submit falls back to AttenceResult when no pending id', async () => {
  const app = loadAttendance([{ status: 1 }, { status: 1 }]);
  await app.processQrcode签到('ticket-a', 'expire-b', 'sign-c');
  assert.equal(app.requests.length, 2);
  assert.ok(app.requests.every(r => r.url.endsWith('/AttenceApi/AttenceResult')));
  assert.equal(app.requests[0].body.ticketid, 'ticket-a');
});

test('manual QR submit keeps AttenceResult when ignore-expire disabled', async () => {
  const app = loadAttendance([{ status: 1 }, { status: 1 }], { ignoreExpire: false });
  app.setPendingQrcodeId('pending-qr-off');
  await app.processQrcode签到('ticket', 'expire', 'sign');
  assert.equal(app.requests.length, 2);
  assert.ok(app.requests.every(r => r.url.endsWith('/AttenceApi/AttenceResult')));
  assert.ok(app.requests.every(r => r.body.ticketid === 'ticket'));
});
