import assert from 'node:assert/strict';
import fs from 'node:fs';
import '../dist/intent.js';

const router=globalThis.FumaiIntent;
const html=fs.readFileSync(new URL('../dist/index.html',import.meta.url),'utf8');
assert.ok(html.indexOf('src="intent.js"')>=0 && html.indexOf('src="intent.js"')<html.indexOf('src="agent.js"'));
const cases=JSON.parse(fs.readFileSync(new URL('../../agent/tests/intent_cases.json',import.meta.url),'utf8'));
for(const row of cases) assert.equal(router.classify(row.question,!!row.context),row.intent,row.question);
const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
for(const intent of ['identity','capabilities','help','greeting','out_of_scope']) {
    const result=router.offlineAnswer(intent);
    assert.equal(result.response_type,'direct');
    assert.deepEqual(result.events,[]);
    assert.deepEqual(result.trace,[]);
    assert.ok(router.validDirect({...result,run_id:'audit-record'}));
    assert.equal(router.validDirect({...result,run_id:'audit-record',events:[{event_id:'unexpected'}]}),false);
    const html=router.renderDirect({...result,summary:'<img src=x onerror=alert(1)>',run_id:'audit-record'},esc,'<svg></svg>');
    assert.ok(html.includes('直接回答'));
    assert.ok(html.includes('&lt;img'));
    assert.ok(!html.includes('<img'));
    assert.ok(!html.includes('引用证据'));
    assert.ok(!html.includes('agent-evidence'));
    assert.ok(!html.includes('候选区间'));
}
assert.ok(router.offlineAnswer('identity').summary.includes('无法确认服务器'));
console.log(JSON.stringify({intentCases:cases.length,directAnswerTypes:5,rendering:'passed',htmlEscaping:'passed'}));
