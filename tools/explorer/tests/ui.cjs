// DOM workflow integration check using real HTTP responses from an isolated
// checkout. jsdom is a development-only dependency; no browser layout claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {JSDOM, VirtualConsole} = require('jsdom');
const root=path.resolve(__dirname,'../../..');
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'cadence-explorer-ui-'));
let server,dom;
const errors=[];
async function waitFor(predicate,message){
  const until=Date.now()+5000;
  while(Date.now()<until){if(predicate())return;await new Promise(r=>setTimeout(r,20));}
  throw Error('Timed out: '+message);
}
async function main(){
  fs.cpSync(path.join(root,'recipes'),path.join(fixture,'recipes'),{recursive:true});
  for(const file of ['README.md','models.yaml','classifiers.yaml'])fs.copyFileSync(path.join(root,file),path.join(fixture,file));
  const tools=path.join(fixture,'tools/explorer');fs.mkdirSync(tools,{recursive:true});
  for(const file of ['source.py','explorer.py','server.py'])fs.copyFileSync(path.join(root,'tools/explorer',file),path.join(tools,file));
  fs.cpSync(path.join(root,'tools/explorer/static'),path.join(tools,'static'),{recursive:true});
  const python=process.env.EXPLORER_PYTHON||path.join(root,'tools/explorer/.venv/bin/python');
  server=spawn(python,[path.join(tools,'server.py'),'--no-browser','--port','0']);
  let output='';server.stdout.on('data',chunk=>output+=chunk);server.stderr.on('data',chunk=>errors.push(String(chunk)));
  await waitFor(()=>/http:\/\/127\.0\.0\.1:\d+\//.test(output),'server startup');
  const url=output.match(/http:\/\/127\.0\.0\.1:\d+\//)[0];
  const copies=[];let rejectClipboard=false;
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error.message));
  dom=await JSDOM.fromURL(url,{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,virtualConsole,
    beforeParse(window){window.fetch=(resource,options)=>{const target=new URL(resource,url);assert.equal(target.hostname,'127.0.0.1');return fetch(target,options);};window.navigator.clipboard={writeText:async text=>{if(rejectClipboard)throw Error('Denied');copies.push(text);}};}});
  const $=id=>dom.window.document.getElementById(id);
  const change=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
  const input=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new dom.window.Event('input',{bubbles:true}));};
  const ready=()=>waitFor(()=>!$('result').hidden,'resolved commands');
  const failed=()=>waitFor(()=>$('selection-status').className.includes('error')&&$('result').hidden,'selection rejection');
  await waitFor(()=>$('sample').options.length===3,'sample discovery');
  change('sample','recurring-ai-watch');
  assert.equal($('language-field').hidden,false);assert.equal($('language').value,'python');assert.equal($('language').options.length,1);
  assert.equal($('framework-field').hidden,false);assert.equal($('result').hidden,true);
  change('framework','recipes/recurring-ai-watch/python/google-adk');
  assert.equal($('mode').value,'');assert.equal($('result').hidden,true);
  assert.equal($('model').value,'gemini-flash-lite');assert.equal($('classifier').value,'jev-default');
  change('mode','live');await ready();
  assert.ok($('worker-command').textContent.includes('--confirm-live'));assert.ok(!$('worker-command').textContent.includes('--agent-id'));
  assert.equal($('evidence-title').textContent,'Recorded live evidence');assert.ok($('evidence-ids').textContent.includes('c2deb01e-ce5f-44c2-84b8-99bfa6ad8af1'));
  dom.window.document.querySelector('[data-copy="start"]').click();await waitFor(()=>copies.length===1,'copy run');
  assert.ok(copies[0].includes('start --mode live'));assert.ok(!copies[0].includes(' check-now'));assert.ok(!copies[0].includes(' stop'));
  assert.equal($('control-commands').querySelectorAll('button').length,3);
  change('classifier','kev-local');await failed();assert.equal($('worker-command').textContent,'');
  change('classifier','laya-local');change('model','llama3.2-local');await ready();
  assert.equal($('local-panel').hidden,false);assert.equal($('local-commands').querySelectorAll('article').length,2);
  assert.ok($('credentials').textContent.includes('no AI API keys'));
  const modelFile=path.join(fixture,'models.yaml');fs.writeFileSync(modelFile,fs.readFileSync(modelFile,'utf8').replace('llama3.2:latest','llama3.2:edited'));
  $('refresh').click();await waitFor(()=>!$('result').hidden&&$('resolution').textContent.includes('llama3.2:edited'),'refresh picks up catalog edits');
  assert.ok($('local-commands').textContent.includes('llama3.2:edited'));
  const cli=path.join(fixture,'recipes/recurring-ai-watch/python/google-adk/main.py');fs.writeFileSync(cli,fs.readFileSync(cli,'utf8').replaceAll('choices=("mock", "live")','choices=("mock",)'));
  $('refresh').click();await waitFor(()=>!$('result').hidden&&$('mode').value==='mock','changed mode choices');
  assert.equal($('mode').options.length,1);assert.equal($('mode-field').hidden,false);assert.equal($('local-panel').hidden,true);
  change('sample','ticket-routing');assert.equal($('language').value,'go');assert.equal($('framework-field').hidden,true);assert.equal($('model-field').hidden,true);assert.equal($('mode').value,'');
  change('mode','demo');await ready();assert.equal($('classifier').options.length,1);assert.equal($('classifier').value,'mock');assert.ok($('client-command').textContent.includes('-mode demo'));
  change('mode','live-demo');assert.equal($('result').hidden,true);change('classifier','laya-local');await ready();assert.ok($('worker-command').textContent.includes('go run .'));assert.ok($('client-command').textContent.includes('-mode live-demo'));
  change('mode','acknowledge');await failed();assert.ok($('selection-status').textContent.includes('identifiers'));
  change('mode','live-batch');change('classifier','laya-local');await failed();assert.equal($('batch-fields').hidden,false);assert.equal($('count').max,'10');assert.equal($('concurrency').max,'5');
  input('count','3');input('concurrency','1');await ready();assert.ok($('client-command').textContent.includes('-count 3 -concurrency 1 -confirm-live'));
  rejectClipboard=true;dom.window.document.querySelector('[data-copy="worker"]').click();await waitFor(()=>$('copy-status').textContent.includes('Clipboard unavailable'),'clipboard fallback');
  input('count','11');await failed();assert.equal($('worker-command').textContent,'');
  fs.unlinkSync(path.join(fixture,'classifiers.yaml'));
  change('mode','demo');$('refresh').click();await waitFor(()=>!$('refresh').disabled,'missing catalog refresh');await ready();
  assert.equal($('classifier').value,'mock');assert.ok($('client-command').textContent.includes('-mode demo'));
  change('mode','worker');await ready();assert.ok($('worker-command').textContent.includes('-classifier-id mock'));
  change('mode','live-demo');await failed();assert.ok($('selection-status').textContent.includes('classifiers.yaml'));
  const incomplete=path.join(fixture,'recipes/new-recipe/java');fs.mkdirSync(incomplete,{recursive:true});fs.writeFileSync(path.join(incomplete,'../README.md'),'# New incomplete recipe\n');
  $('refresh').click();await waitFor(()=>$('sample').options.length===4,'new recipe discovery');change('sample','new-recipe');
  assert.equal($('language').value,'java');assert.equal($('language-field').hidden,false);assert.equal($('readme').hidden,false);await failed();assert.ok($('selection-status').textContent.includes('adapter'));
  assert.deepEqual(errors,[]);
  console.log('PASS: real HTTP + DOM flow, visible single-choice selectors, explicit modes, historical evidence, individual controls, keyless prerequisites, file refresh, source-mode edits, rejected candidates, Go live batches, clipboard fallback, and incomplete recipes.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(dom)dom.window.close();
  if(server){server.kill('SIGINT');await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill('SIGTERM');resolve();},2000);server.once('exit',()=>{clearTimeout(timer);resolve();});});}
  fs.rmSync(fixture,{recursive:true,force:true});
});
