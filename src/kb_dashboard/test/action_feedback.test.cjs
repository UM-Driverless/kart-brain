const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(__dirname + '/../kb_dashboard/index.html','utf8');
const start = html.includes('// Discrete actions report') ? html.indexOf('// Discrete actions report') : html.indexOf('function wsSend(obj)');
const source = html.slice(start, html.indexOf('// Stop the EBS compressor'));
function fixture({connected=true, mission='autonomous', state='idle'}={}) {
  const classes = () => { const values = new Set(); return {add:x=>values.add(x),remove:x=>values.delete(x),toggle:(x,on)=>on?values.add(x):values.delete(x),contains:x=>values.has(x)}; };
  const button = {classList:classes()};
  const feedback = {classList:classes(),textContent:'',hidden:true};
  const sent=[]; let timeout;
  const context={
    $:()=>feedback, document:{querySelectorAll:()=>[button],addEventListener:(_,fn)=>fn({target:{closest:()=>button}})},
    performance:{now:()=>100},lastTelemetryAt:100,benchStatusFresh:()=>true,
    lastData:{state,as_state:state==='ebs'?'AS_EMERGENCY':'AS_READY',safety_reason:'Tank pressure too low',bench_mode_available:true,bench_mode_enabled:false},
    currentMission:mission,AUTONOMOUS_MISSIONS:new Set(['autonomous']),DEMO:false,
    ws:connected?{readyState:1,send:x=>sent.push(JSON.parse(x))}:null,
    setTimeout:fn=>{timeout=fn;return 1}, clearTimeout:()=>{},
  };
  vm.runInNewContext(source,context);
  const handler = html.slice(html.indexOf('      if (message.action_error)'),html.indexOf('      if (message.your_id)'));
  vm.runInNewContext('function receiveError(message) {' + handler + '}',context);
  return {context,button,feedback,sent,expire:()=>timeout(),send:cmd=>context.wsSend(cmd)};
}
test('blocked Start lights red and names how to use the selected mission without sending',()=>{
  const f=fixture({mission:'remote_control'});
  assert.equal(f.send({action:'set_state',state:'running'}),false);
  assert.equal(f.sent.length,0);assert.ok(f.button.classList.contains('action-rejected'));
  assert.match(f.feedback.textContent,/Remote uses the driving pad/);
});
test('emergency Start reports actual fault and does not send',()=>{
  const f=fixture({state:'ebs'});f.send({action:'set_state',state:'running'});
  assert.equal(f.sent.length,0);assert.match(f.feedback.textContent,/Tank pressure too low/);
});
test('unavailable bypass is clickable for red feedback without sending',()=>{
  const f=fixture();f.context.lastData.bench_mode_available=false;
  f.send({action:'set_bench_mode',enabled:true});
  assert.equal(f.sent.length,0);assert.ok(f.button.classList.contains('action-rejected'));
  assert.match(f.feedback.textContent,/bypass unavailable/);
});
test('disconnected command reports failure and returns false for PID panel handling',()=>{
  const f=fixture({connected:false});
  assert.equal(f.send({action:'set_steer_pid'}),false);
  assert.match(f.feedback.textContent,/Connection lost/);assert.equal(f.sent.length,0);
});
test('unconfirmed Reset reports refusal, while actual confirmation clears red',()=>{
  const f=fixture({state:'ebs'});f.send({action:'set_state',state:'reset'});f.expire();
  assert.ok(f.button.classList.contains('action-rejected'));assert.match(f.feedback.textContent,/not confirmed.*Tank pressure too low/);
  f.send({action:'set_state',state:'reset'});
  f.context.observeAction({state:'idle',as_state:'AS_OFF'});
  assert.equal(f.button.classList.contains('action-rejected'),false);
  assert.match(f.feedback.textContent,/Kart confirmed/);
});
test('already latched EBS and unnecessary Reset explain why nothing changes',()=>{
  const f=fixture({state:'ebs'});f.send({action:'set_state',state:'ebs'});
  assert.match(f.feedback.textContent,/already latched/);assert.equal(f.sent.length,0);
  f.context.lastData.state='idle';f.context.lastData.as_state='AS_READY';
  f.send({action:'set_state',state:'reset'});assert.match(f.feedback.textContent,/no emergency/);
});

test('emergency command remains available without fresh telemetry',()=>{
  const f=fixture();f.context.lastData=null;
  assert.equal(f.send({action:'set_state',state:'ebs'}),true);
  assert.equal(f.sent[0].state,'ebs');
});

test('Stop remains sendable without fresh telemetry',()=>{
  const f=fixture();f.context.lastData=null;
  assert.equal(f.send({action:'set_state',state:'idle'}),true);
  assert.equal(f.sent[0].state,'idle');
});

test('delayed unrelated error preserves safety acknowledgment and authoritative telemetry',()=>{
  const f=fixture({state:'ebs'});const telemetry=f.context.lastData;
  f.send({action:'set_state',state:'reset'});
  f.context.receiveError({action_error:{action:'set_steer_pid',reason:'Invalid tuning'}});
  assert.equal(f.context.lastData,telemetry);
  f.context.observeAction({state:'idle',as_state:'AS_OFF'});
  assert.match(f.feedback.textContent,/Kart confirmed/);
});
test('matching error cancels its pending action and keeps the refusal visible',()=>{
  const f=fixture({state:'ebs'});f.send({action:'set_state',state:'reset'});
  f.context.receiveError({action_error:{action:'set_state',reason:'Reset refused'}});
  f.context.observeAction({state:'idle',as_state:'AS_OFF'});
  assert.equal(f.feedback.textContent,'Reset refused');
  assert.ok(f.button.classList.contains('action-rejected'));
});

test('unconfirmed bypass names its eligibility reason rather than the general pressure fault',()=>{
  const f=fixture({state:'ebs'});f.context.lastData.bench_mode_reason='Select Autonomous and steering None';
  f.send({action:'set_bench_mode',enabled:true});f.expire();
  assert.match(f.feedback.textContent,/Select Autonomous and steering None/);
  assert.ok(f.button.classList.contains('action-rejected'));
});
