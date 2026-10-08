const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../kb_dashboard/index.html'), 'utf8');
const selection = html.slice(html.indexOf('    const misTabs ='), html.indexOf('    wirePidControls();', html.indexOf('    const misTabs =')));

function fixture() {
  const names = ['manual', 'autonomous', 'remote_control'];
  const elements = Object.fromEntries(['missionSelectionStatus', 'misWheel', 'misWheelWin'].map(id => [id, {textContent: ''}]));
  const tabs = names.map(name => ({dataset: {m: name}, textContent: name, classList: {toggle() {}}}));
  let now = 0, selected = 0, pick;
  const sent = [];
  const context = {
    document: {querySelectorAll: () => tabs}, window: {},
    $: id => elements[id], performance: {now: () => now},
    lastData: {state: 'idle'}, wsSend: message => sent.push(message),
    rcMakeWheel: options => {
      pick = options.onPick;
      return {go: i => { selected = i; }, sel: () => selected};
    },
  };
  vm.runInNewContext(selection, context);
  context.window.rcSetCurrentMission('manual');
  return {
    context, sent, elements, advance: ms => { now += ms; },
    pick: index => { if (pick(index) !== false) selected = index; },
    selected: () => names[selected], feedback: m => context.window.rcSetCurrentMission(m),
  };
}

test('emergency mission selection stays on actual mission and explains refusal', () => {
  const f = fixture(); f.context.lastData.state = 'ebs'; f.pick(2);
  assert.equal(f.selected(), 'manual');
  assert.equal(f.sent.length, 0);
  assert.match(f.elements.missionSelectionStatus.textContent, /emergency latched/);
});

test('old feedback does not pull a pending selection back; actual acknowledgment clears it', () => {
  const f = fixture(); f.pick(2); f.feedback('manual');
  assert.equal(f.selected(), 'remote_control');
  assert.match(f.elements.missionSelectionStatus.textContent, /Waiting/);
  f.feedback('remote_control');
  assert.equal(f.elements.missionSelectionStatus.textContent, '');
  f.feedback('autonomous');
  assert.equal(f.selected(), 'autonomous');
});

test('unconfirmed selection returns to actual mission with an explanation', () => {
  const f = fixture(); f.pick(2); f.advance(2001); f.feedback('manual');
  assert.equal(f.selected(), 'manual');
  assert.match(f.elements.missionSelectionStatus.textContent, /not confirmed/);
});

test('disconnect clears an unconfirmed selection without claiming it succeeded', () => {
  const f = fixture(); f.pick(2); f.context.window.rcCancelMissionSelection();
  assert.equal(f.selected(), 'manual');
  assert.match(f.elements.missionSelectionStatus.textContent, /Connection lost/);
});
