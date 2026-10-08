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
  fs.cpSync(path.join(root,'recipes'),path.join(fixture,'recipes'),{recursive:true,
    filter:source=>!['.venv','venv','__pycache__'].includes(path.basename(source))});
  for(const file of ['README.md','models.yaml','classifiers.yaml'])fs.copyFileSync(path.join(root,file),path.join(fixture,file));
  const tools=path.join(fixture,'tools/explorer');fs.mkdirSync(tools,{recursive:true});
  for(const file of ['source.py','explorer.py','server.py','terminal.py'])fs.copyFileSync(path.join(root,'tools/explorer',file),path.join(tools,file));
  fs.cpSync(path.join(root,'tools/explorer/static'),path.join(tools,'static'),{recursive:true});
  const python=process.env.EXPLORER_PYTHON||path.join(root,'tools/explorer/.venv/bin/python');
  server=spawn(python,[path.join(tools,'server.py'),'--no-browser','--port','0']);
  let output='';server.stdout.on('data',chunk=>output+=chunk);server.stderr.on('data',chunk=>errors.push(String(chunk)));
  await waitFor(()=>/http:\/\/127\.0\.0\.1:\d+\//.test(output),'server startup');
  const url=output.match(/http:\/\/127\.0\.0\.1:\d+\//)[0];
  const copies=[],terminalRequests=[],terminalEvents=[],runRequests=[],signalRequests=[];const runIds=['run-current','run-continued'];
  let rejectClipboard=false,terminalState='prefilled',holdTerminal=false,releaseTerminal,holdRun=false,releaseRun,signalState='accepted',holdSignal=false,releaseSignal;
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error.message));
  dom=await JSDOM.fromURL(url,{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,virtualConsole,
    beforeParse(window){
      window.fetch=async(resource,options)=>{
        const target=new URL(resource,url);assert.equal(target.hostname,'127.0.0.1');
        if(target.pathname==='/api/terminal'){
          terminalEvents.push('terminal');terminalRequests.push(JSON.parse(options.body));
          const response=()=>new Response(JSON.stringify({state:terminalState,detail:terminalState==='opened-copy-only'?'Terminal opened, but macOS blocked automatic prefill. Press Cmd+V to paste the copied command.':'test terminal result'}),{status:200,headers:{'Content-Type':'application/json'}});
          if(holdTerminal)return new Promise(resolve=>releaseTerminal=()=>resolve(response()));
          return response();
        }
        if(target.pathname==='/api/workflow-run'){
          runRequests.push(target);
          const response=()=>new Response(JSON.stringify({state:'found',run_id:runIds.shift(),detail:'resolved'}),{status:200,headers:{'Content-Type':'application/json'}});
          if(holdRun)return new Promise(resolve=>releaseRun=()=>resolve(response()));
          return response();
        }
        if(target.pathname==='/api/workflow-signal'){
          signalRequests.push(JSON.parse(options.body));
          const response=()=>new Response(JSON.stringify({state:signalState,detail:signalState==='accepted'?'Cadence-Web accepted the Signal; Workflow processing is not yet confirmed.':'Cadence-Web rejected the Signal.'}),{status:200,headers:{'Content-Type':'application/json'}});
          if(holdSignal)return new Promise(resolve=>releaseSignal=()=>resolve(response()));
          return response();
        }
        const response=await fetch(target,options);
        if(target.pathname==='/api/state'){
          const body=await response.json();body.terminal_handoff=true;
          return new Response(JSON.stringify(body),{status:response.status,headers:{'Content-Type':'application/json'}});
        }
        return response;
      };
      window.confirm=()=>{throw Error('Signal controls must not use confirm()');};
      window.navigator.clipboard={writeText:async text=>{if(rejectClipboard)throw Error('Denied');copies.push(text);terminalEvents.push('copy');}};
    }});
  const $=id=>dom.window.document.getElementById(id);
  const change=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
  const input=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new dom.window.Event('input',{bubbles:true}));};
  const ready=()=>waitFor(()=>!$('worker-block').hidden,'resolved commands');
  const failed=()=>waitFor(()=>$('selection-status').className.includes('error')&&$('worker-block').hidden,'selection rejection');
  await waitFor(()=>$('sample').options.length===3,'sample discovery');
  assert.equal(dom.window.document.querySelectorAll('.step').length,6);
  assert.equal($('resolved-panel'),null);assert.equal($('evidence-panel'),null);
  assert.equal(dom.window.document.querySelector('.teaching'),null);
  assert.equal($('service-results').children.length,0);
  change('sample','recurring-ai-watch');
  assert.equal($('language-field').hidden,false);assert.equal($('language').value,'python');assert.equal($('language').options.length,1);
  assert.equal($('framework-field').hidden,false);assert.equal($('worker-block').hidden,true);
  change('framework','recipes/recurring-ai-watch/python/google-adk');
  assert.equal($('mode').value,'');assert.equal($('worker-block').hidden,true);
  assert.equal($('model').value,'gemini-flash-lite');assert.equal($('classifier').value,'jev-default');
  change('mode','live');await ready();
  assert.ok($('worker-command').textContent.includes('--confirm-live'));assert.ok(!$('worker-command').textContent.includes('--agent-id'));
  assert.equal(runRequests.length,0);assert.equal($('workflow-run-panel').hidden,false);assert.equal($('resolve-run').hidden,false);
  assert.equal($('web-cluster').textContent,'cluster0');assert.ok($('web-workflow-id').textContent.endsWith('-demo'));
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-current','latest run resolution');
  assert.ok($('workflow-history').href.endsWith('/run-current/history'));assert.ok($('workflow-queries').href.endsWith('/run-current/queries'));
  assert.equal($('watch-signals').hidden,false);holdSignal=true;$('signal-check-now').click();await waitFor(()=>typeof releaseSignal==='function','held check-now Signal');
  assert.equal($('signal-check-now').disabled,true);assert.equal($('signal-stop-watch').disabled,true);assert.equal(signalRequests[0].action,'check-now');assert.equal(signalRequests[0].run_id,'run-current');assert.equal('signalName' in signalRequests[0],false);assert.equal('signalInput' in signalRequests[0],false);
  releaseSignal();await waitFor(()=>$('signal-check-now').textContent==='Signal sent ✓','Signal accepted feedback');await waitFor(()=>$('signal-check-now').textContent==='Send check-now','Signal accepted feedback reset');holdSignal=false;releaseSignal=undefined;
  signalState='rejected';$('signal-stop-watch').click();await waitFor(()=>$('signal-stop-watch').textContent==='Send failed','Signal failed feedback');assert.ok($('signal-status').textContent.includes('rejected'));await waitFor(()=>$('signal-stop-watch').textContent==='Send stop-watch','Signal failed feedback reset');
  signalState='accepted';holdSignal=true;$('signal-check-now').click();await waitFor(()=>typeof releaseSignal==='function','held stale Signal');
  input('run-id','historical run/1');releaseSignal();await waitFor(()=>$('watch-signals').hidden&&$('signal-status').textContent==='','stale Signal response ignored');holdSignal=false;releaseSignal=undefined;
  assert.ok($('workflow-history').href.includes('/historical%20run%2F1/history'));assert.ok($('workflow-run-status').textContent.includes('entered'));
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-continued','continued run resolution');
  assert.ok($('workflow-history').href.endsWith('/run-continued/history'));assert.equal(runRequests.length,2);
  assert.equal($('open-worker').hidden,false);assert.equal($('open-start').hidden,false);assert.equal($('terminal-note').hidden,false);
  terminalEvents.length=0;$('open-worker').click();await waitFor(()=>$('worker-terminal-status').textContent.includes('Ready in Terminal'),'worker terminal handoff');
  assert.deepEqual(terminalEvents,['copy','terminal']);assert.equal(terminalRequests[0].action,'worker');assert.equal('command' in terminalRequests[0],false);
  terminalState='opened-copy-only';terminalEvents.length=0;$('open-start').click();await waitFor(()=>$('start-terminal-status').textContent.includes('Cmd+V'),'start terminal fallback');
  assert.deepEqual(terminalEvents,['copy','terminal']);assert.equal(terminalRequests[1].action,'start');
  terminalState='prefilled';holdTerminal=true;$('open-start').click();await waitFor(()=>typeof releaseTerminal==='function','held terminal request');
  change('mode','mock');releaseTerminal();await waitFor(()=>$('start-terminal-status').textContent===''&&!$('open-start').disabled,'stale terminal response ignored');holdTerminal=false;releaseTerminal=undefined;
  change('mode','live');await ready();
  copies.length=0;
  const startCopy=dom.window.document.querySelector('[data-copy="start"]');startCopy.click();await waitFor(()=>startCopy.textContent==='Copied ✓','copy confirmation');
  assert.ok(copies[0].includes('start --mode live'));assert.ok(!copies[0].includes(' check-now'));assert.ok(!copies[0].includes(' stop'));
  assert.equal($('control-commands').querySelectorAll('button').length,3);
  assert.equal($('classifier').options.length,3);
  assert.deepEqual([...$('classifier').options].map(option=>option.value),['','jev-default','laya-local']);
  change('classifier','laya-local');change('model','llama3.2-local');await ready();
  assert.equal($('warmup').disabled,false);assert.ok($('warmup-status').textContent.includes('Ollama'));
  assert.ok($('credentials').textContent.includes('no AI API keys'));
  const modelFile=path.join(fixture,'models.yaml');fs.writeFileSync(modelFile,fs.readFileSync(modelFile,'utf8').replace('llama3.2:latest','llama3.2:edited'));
  $('refresh').click();await waitFor(()=>$('service-results').textContent.includes('llama3.2:edited'),'refresh checks edited catalog');
  assert.ok($('check-status').textContent.includes('advisory'));
  const cli=path.join(fixture,'recipes/recurring-ai-watch/python/google-adk/main.py');fs.writeFileSync(cli,fs.readFileSync(cli,'utf8').replaceAll('choices=("mock", "live")','choices=("mock",)'));
  $('refresh').click();await waitFor(()=>!$('worker-block').hidden&&$('mode').value==='mock','changed mode choices');
  assert.equal($('mode').options.length,1);assert.equal($('mode-field').hidden,false);assert.equal($('warmup').disabled,true);
  holdRun=true;$('resolve-run').click();await waitFor(()=>typeof releaseRun==='function','held run lookup');
  change('sample','ticket-routing');releaseRun();await waitFor(()=>$('workflow-run-panel').hidden&&$('run-id').value==='','stale run response ignored');holdRun=false;releaseRun=undefined;
  assert.equal($('language').value,'go');assert.equal($('framework-field').hidden,true);assert.equal($('model-field').hidden,true);assert.equal($('mode').value,'');
  change('mode','demo');await ready();assert.equal($('classifier').options.length,1);assert.equal($('classifier').value,'mock');assert.ok($('client-command').textContent.includes('-mode demo'));assert.ok($('workflow-run-scope').textContent.includes('multiple Workflows'));assert.ok(!$('cadence-web').hidden);
  change('mode','live-demo');assert.equal($('worker-block').hidden,true);change('classifier','laya-local');await ready();assert.ok($('worker-command').textContent.includes('go run .'));assert.ok($('client-command').textContent.includes('-mode live-demo'));
  change('mode','acknowledge');await failed();assert.ok($('selection-status').textContent.includes('identifiers'));
  change('mode','live-batch');change('classifier','laya-local');await failed();assert.equal($('batch-fields').hidden,false);assert.equal($('count').max,'10');assert.equal($('concurrency').max,'5');
  input('count','3');input('concurrency','1');await ready();assert.ok($('client-command').textContent.includes('-count 3 -concurrency 1 -confirm-live'));
  rejectClipboard=true;const workerCopy=dom.window.document.querySelector('[data-copy="worker"]');workerCopy.click();await waitFor(()=>$('copy-status').textContent.includes('Clipboard unavailable'),'clipboard fallback');assert.equal(workerCopy.textContent,'Copy failed');
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
  console.log('PASS: real HTTP + six-step DOM flow, visible single-choice selectors, explicit modes, manual checks, local warm-up availability, Mac terminal handoff/fallback/stale state, latest/manual Run ID deep links, allowlisted Signal feedback/stale state, individual controls, catalog refresh, Go live batches, clipboard fallback, and incomplete recipes.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(dom)dom.window.close();
  if(server){server.kill('SIGINT');await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill('SIGTERM');resolve();},2000);server.once('exit',()=>{clearTimeout(timer);resolve();});});}
  fs.rmSync(fixture,{recursive:true,force:true});
});
