// DOM workflow integration check using real HTTP responses from an isolated
// checkout. jsdom is a development-only dependency; no browser layout claims.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, execSync} = require('node:child_process');
const {JSDOM, VirtualConsole} = require('jsdom');
const root=path.resolve(__dirname,'../../..');
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'cadence-explorer-ui-'));
let server,dom;
const errors=[];
const themeKey='cadence-explorer-theme';
async function waitFor(predicate,message){
  const until=Date.now()+5000;
  while(Date.now()<until){if(predicate())return;await new Promise(r=>setTimeout(r,20));}
  throw Error('Timed out: '+message);
}
function themeDocument(saved,{inaccessible=false}={}){
  const themed=new JSDOM('<!doctype html><html><head></head><body></body></html>',
    {runScripts:'dangerously',url:'http://127.0.0.1:8765/'});
  if(inaccessible)Object.defineProperty(themed.window,'localStorage',{get(){throw Error('Storage denied');}});
  else if(saved!==undefined)themed.window.localStorage.setItem(themeKey,saved);
  themed.window.eval(fs.readFileSync(path.join(root,'tools/explorer/static/theme.js'),'utf8'));
  return themed;
}
async function main(){
  const indexSource=fs.readFileSync(path.join(root,'tools/explorer/static/index.html'),'utf8');
  assert.ok(indexSource.indexOf('src="theme.js"')<indexSource.indexOf('rel="stylesheet"'),
    'theme bootstrap must run before the stylesheet');
  for(const [saved,expected] of [[undefined,'dark'],['dark','dark'],['light','light'],['sepia','dark']]){
    const themed=themeDocument(saved);
    assert.equal(themed.window.document.documentElement.dataset.theme,expected);
    assert.equal(themed.window.ExplorerTheme.current(),expected);
    themed.window.close();
  }
  const inaccessibleTheme=themeDocument(undefined,{inaccessible:true});
  assert.equal(inaccessibleTheme.window.document.documentElement.dataset.theme,'dark');
  inaccessibleTheme.window.close();

  fs.cpSync(path.join(root,'recipes'),path.join(fixture,'recipes'),{recursive:true,
    filter:source=>!['.venv','venv','__pycache__','.run-state'].includes(path.basename(source))});
  fs.cpSync(path.join(root,'scripts'),path.join(fixture,'scripts'),{recursive:true});
  fs.cpSync(path.join(root,'templates'),path.join(fixture,'templates'),{recursive:true});
  execSync('bash scripts/new-recipe.sh generated-go-systemone --language go --classifier systemone --agent none',
    {cwd:fixture,stdio:'pipe',timeout:60000});
  const generic=path.join(fixture,'recipes/generic-single');
  fs.mkdirSync(path.join(generic,'python'),{recursive:true});
  fs.cpSync(path.join(root,'recipes/recurring-ai-watch/python/google-adk'),path.join(generic,'python/google-adk'),{recursive:true});
  fs.writeFileSync(path.join(generic,'README.md'),'# Generic single Workflow\n');
  fs.writeFileSync(path.join(generic,'explorer.yaml'),`version: 1
signals:
  - id: revise
    name: revise
    label: Revise
    description: Revise one version.
    intent: normal
    arguments:
      - name: request
        label: Request
        type: object
        required: true
        constraints: {}
        help: Enter revision details.
        fields:
          - name: expected_revision
            label: Expected revision
            type: integer
            required: true
            constraints:
              minimum: 0
              maximum: 1000
            help: Enter the current revision.
          - name: detail
            label: Detail
            type: string
            required: true
            constraints:
              min_length: 1
              max_length: 255
            help: Enter revision detail.
`);
  for(const file of ['README.md','models.yaml','classifiers.yaml'])fs.copyFileSync(path.join(root,file),path.join(fixture,file));
  const tools=path.join(fixture,'tools/explorer');fs.mkdirSync(tools,{recursive:true});
  for(const file of ['source.py','signal_metadata.py','explorer.py','server.py','terminal.py'])fs.copyFileSync(path.join(root,'tools/explorer',file),path.join(tools,file));
  fs.cpSync(path.join(root,'tools/explorer/static'),path.join(tools,'static'),{recursive:true});
  const python=process.env.EXPLORER_PYTHON||path.join(root,'tools/explorer/.venv/bin/python');
  server=spawn(python,[path.join(tools,'server.py'),'--no-browser','--port','0']);
  let output='';server.stdout.on('data',chunk=>output+=chunk);server.stderr.on('data',chunk=>errors.push(String(chunk)));
  await waitFor(()=>/http:\/\/127\.0\.0\.1:\d+\//.test(output),'server startup');
  const url=output.match(/http:\/\/127\.0\.0\.1:\d+\//)[0];
  const copies=[],prepareRequests=[],terminalRequests=[],terminalEvents=[],runRequests=[],signalRequests=[],signalRequestUrls=[];const watchRunIds=['run-current','run-continued'];
  let rejectClipboard=false,holdPrepare=false,releasePrepare,terminalState='prefilled',holdTerminal=false,releaseTerminal,holdRun=false,releaseRun,signalState='accepted',holdSignal=false,releaseSignal,holdCheck=false,releaseCheck,holdWarmup=false,releaseWarmup;
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
        if(target.pathname==='/api/prepare'){
          prepareRequests.push(JSON.parse(options.body));
          const response=()=>new Response(JSON.stringify({state:'prepared',detail:'Declared dependencies are prepared.',diagnostic:'Dependencies prepared.'}),{status:200,headers:{'Content-Type':'application/json'}});
          if(holdPrepare)return new Promise(resolve=>releasePrepare=()=>resolve(response()));
          return response();
        }
        if(target.pathname==='/api/workflow-run'){
          runRequests.push(target);
          const sample=target.searchParams.get('sample');
          const runId=sample==='auto-research'?'run-auto':sample==='human-in-the-loop'?'run-hitl':sample==='generic-single'?'run-generic':sample==='ticket-routing'?'run-ticket':watchRunIds.shift();
          const stale=runId==='run-continued';
          const response=()=>new Response(JSON.stringify({state:stale?'stale run':'found',run_id:runId,detail:stale?'The previous Run ID is stale. The current Run ID changed, which can happen after Continue-As-New.':'resolved'}),{status:200,headers:{'Content-Type':'application/json'}});
          if(holdRun)return new Promise(resolve=>releaseRun=()=>resolve(response()));
          return response();
        }
        if(target.pathname==='/api/workflow-signal'){
          signalRequestUrls.push(target.href);
          signalRequests.push(JSON.parse(options.body));
          const response=()=>new Response(JSON.stringify({state:signalState,detail:signalState==='accepted'?'Cadence-Web accepted the Signal; Workflow processing is not yet confirmed.':'Cadence-Web rejected the Signal.'}),{status:200,headers:{'Content-Type':'application/json'}});
          if(holdSignal)return new Promise(resolve=>releaseSignal=()=>resolve(response()));
          return response();
        }
        if(target.pathname==='/api/check'&&holdCheck)return new Promise(resolve=>releaseCheck=()=>resolve(new Response(JSON.stringify({error:'stale check failure'}),{status:500,headers:{'Content-Type':'application/json'}})));
        if(target.pathname==='/api/warmup'&&holdWarmup)return new Promise(resolve=>releaseWarmup=()=>resolve(new Response(JSON.stringify({error:'stale warm-up failure'}),{status:500,headers:{'Content-Type':'application/json'}})));
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
  await waitFor(()=>$('sample').options.length===9,'sample discovery');
  const themeToggle=$('theme-toggle');
  assert.equal(dom.window.document.documentElement.dataset.theme,'dark');
  assert.equal(themeToggle.tagName,'BUTTON');assert.equal(themeToggle.type,'button');
  assert.equal(themeToggle.getAttribute('aria-pressed'),'true');
  assert.equal(themeToggle.getAttribute('aria-label'),'Dark theme active. Switch to light theme.');
  themeToggle.focus();assert.equal(dom.window.document.activeElement,themeToggle);
  themeToggle.click();assert.equal(dom.window.document.documentElement.dataset.theme,'light');
  assert.equal(dom.window.localStorage.getItem(themeKey),'light');
  assert.equal(themeToggle.getAttribute('aria-pressed'),'false');
  assert.equal(themeToggle.getAttribute('aria-label'),'Light theme active. Switch to dark theme.');
  themeToggle.click();assert.equal(dom.window.document.documentElement.dataset.theme,'dark');
  assert.equal(dom.window.localStorage.getItem(themeKey),'dark');
  const storagePrototype=Object.getPrototypeOf(dom.window.localStorage);
  const originalSetItem=storagePrototype.setItem;
  storagePrototype.setItem=()=>{throw Error('Storage denied');};
  themeToggle.click();assert.equal(dom.window.document.documentElement.dataset.theme,'light');
  storagePrototype.setItem=originalSetItem;
  themeToggle.click();assert.equal(dom.window.document.documentElement.dataset.theme,'dark');
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
  assert.ok($('setup-command').textContent.endsWith('&& ./run.sh setup'));
  assert.ok($('worker-command').textContent.includes('./run.sh'));
  assert.ok($('prepare-status').textContent.startsWith('not prepared:'));
  holdPrepare=true;$('prepare').click();await waitFor(()=>typeof releasePrepare==='function','held recipe preparation');
  assert.ok($('prepare-status').textContent.startsWith('preparing:'));
  assert.equal(prepareRequests.length,1);assert.equal(prepareRequests[0].launcher_revision.length,64);
  for(const forbidden of ['command','executable','path','package','arguments'])assert.equal(forbidden in prepareRequests[0],false);
  releasePrepare();await waitFor(()=>$('prepare-status').textContent.startsWith('prepared:'),'prepared recipe feedback');
  assert.equal($('prepare-diagnostic').hidden,false);assert.ok(!$('terminal-note').textContent.includes('will prepare'));holdPrepare=false;releasePrepare=undefined;
  assert.ok($('worker-command').textContent.includes('--confirm-live'));assert.ok(!$('worker-command').textContent.includes('--agent-id'));
  assert.equal(runRequests.length,0);assert.equal($('workflow-run-panel').hidden,false);assert.equal($('resolve-run').hidden,false);
  assert.equal($('web-cluster').textContent,'cluster0');assert.ok($('web-workflow-id').value.endsWith('-demo'));assert.equal($('web-workflow-id').readOnly,true);
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-current','latest run resolution');
  assert.ok($('workflow-history').href.endsWith('/run-current/history'));assert.ok($('workflow-queries').href.endsWith('/run-current/queries'));
  assert.equal($('workflow-signals').hidden,false);
  const preservedThemeState={
    sample:$('sample').value,framework:$('framework').value,mode:$('mode').value,
    worker:$('worker-command').textContent,preparation:$('prepare-status').textContent,
    run:$('run-id').value,signals:$('signal-controls').children.length,
  };
  themeToggle.click();
  assert.deepEqual({
    sample:$('sample').value,framework:$('framework').value,mode:$('mode').value,
    worker:$('worker-command').textContent,preparation:$('prepare-status').textContent,
    run:$('run-id').value,signals:$('signal-controls').children.length,
  },preservedThemeState);
  themeToggle.click();
  const checkCard=dom.window.document.querySelector('[data-signal-id="check-now"]');
  const stopCard=dom.window.document.querySelector('[data-signal-id="stop-watch"]');
  const checkButton=checkCard.querySelector('.signal-send'),stopButton=stopCard.querySelector('.signal-send');
  holdSignal=true;checkButton.click();await waitFor(()=>typeof releaseSignal==='function','held check-now Signal');
  assert.ok([...$('signal-controls').querySelectorAll('button')].every(button=>button.disabled));assert.equal(signalRequests[0].signal_id,'check-now');assert.equal(signalRequests[0].run_id,'run-current');assert.equal(signalRequests[0].signal_metadata_revision.length,64);assert.deepEqual(signalRequests[0].arguments,{});assert.equal('signalName' in signalRequests[0],false);assert.equal('signalInput' in signalRequests[0],false);
  releaseSignal();await waitFor(()=>checkButton.textContent==='Signal sent ✓','Signal accepted feedback');await waitFor(()=>checkButton.textContent==='Send Check now','Signal accepted feedback reset');holdSignal=false;releaseSignal=undefined;
  signalState='rejected';const requestCountBeforeStop=signalRequests.length;stopButton.click();assert.equal(signalRequests.length,requestCountBeforeStop);assert.equal(stopCard.querySelector('.signal-confirmation').hidden,false);stopCard.querySelector('.signal-confirmation .danger').click();await waitFor(()=>stopButton.textContent==='Send failed','Signal failed feedback');assert.ok($('signal-status').textContent.includes('rejected'));await waitFor(()=>stopButton.textContent==='Send Stop watch','Signal failed feedback reset');
  signalState='accepted';holdSignal=true;checkButton.click();await waitFor(()=>typeof releaseSignal==='function','held stale Signal');
  input('run-id','historical run/1');releaseSignal();await waitFor(()=>$('workflow-signals').hidden&&$('signal-status').textContent==='','stale Signal response ignored');holdSignal=false;releaseSignal=undefined;
  assert.ok($('workflow-history').href.includes('/historical%20run%2F1/history'));assert.ok($('workflow-run-status').textContent.includes('entered'));
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-continued','continued run resolution');
  assert.ok($('workflow-history').href.endsWith('/run-continued/history'));assert.equal(runRequests.length,2);assert.equal(runRequests[1].searchParams.get('run_id'),'historical run/1');assert.ok($('workflow-run-status').textContent.includes('Continue-As-New'));assert.ok($('workflow-run-status').className.includes('stale'));
  stopButton.click();assert.equal(stopCard.querySelector('.signal-confirmation').hidden,false);assert.equal(stopCard.querySelector('.signal-confirmation .danger').disabled,false);stopCard.querySelector('.signal-confirmation .secondary').click();

  change('sample','human-in-the-loop');change('framework','recipes/human-in-the-loop/python/openai-agents');change('mode','mock');await ready();
  assert.equal($('workflow-run-panel').hidden,false);assert.equal($('web-workflow-id').readOnly,true);assert.ok($('web-workflow-id').value.startsWith('human-in-the-loop-'));
  input('run-id','hitl historical/1');assert.ok($('workflow-history').href.includes('/hitl%20historical%2F1/history'));assert.equal($('workflow-signals').hidden,true);
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-hitl','Human in the Loop run resolution');
  assert.ok($('workflow-history').href.includes(`/${encodeURIComponent($('web-workflow-id').value)}/run-hitl/history`));assert.equal($('workflow-signals').hidden,false);
  const privateCallId='private-call-id-123';
  const approveCard=dom.window.document.querySelector('[data-signal-id="approve-tool-call"]');
  const rejectCard=dom.window.document.querySelector('[data-signal-id="reject-tool-call"]');
  input('signal-approve-tool-call-call_id',privateCallId);
  const approveCount=signalRequests.length;approveCard.querySelector('.signal-send').click();assert.equal(signalRequests.length,approveCount);assert.equal(approveCard.querySelector('.signal-confirmation').hidden,false);
  approveCard.querySelector('.signal-confirmation button').click();await waitFor(()=>signalRequests.length===approveCount+1,'approve Signal request');
  assert.equal(signalRequests.at(-1).signal_id,'approve-tool-call');assert.equal(signalRequests.at(-1).signal_metadata_revision.length,64);assert.deepEqual(signalRequests.at(-1).arguments,{call_id:privateCallId});assert.ok(signalRequestUrls.every(requestUrl=>!requestUrl.includes(privateCallId)));assert.ok(!$('signal-status').textContent.includes(privateCallId));
  await waitFor(()=>!rejectCard.querySelector('.signal-send').disabled,'approve Signal completion');
  assert.equal($('signal-approve-tool-call-call_id').value,'');
  input('signal-reject-tool-call-call_id',privateCallId);
  const rejectCount=signalRequests.length;rejectCard.querySelector('.signal-send').click();assert.equal(signalRequests.length,rejectCount);rejectCard.querySelector('.signal-confirmation .danger').click();await waitFor(()=>signalRequests.length===rejectCount+1,'reject Signal request');
  assert.equal(signalRequests.at(-1).signal_id,'reject-tool-call');assert.deepEqual(signalRequests.at(-1).arguments,{call_id:privateCallId});assert.ok(!$('signal-status').textContent.includes(privateCallId));
  await waitFor(()=>!rejectCard.querySelector('.signal-send').disabled,'reject Signal completion');assert.equal($('signal-reject-tool-call-call_id').value,'');

  change('sample','auto-research');change('framework','recipes/auto-research/python/openai-agents');change('mode','mock');await ready();
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-auto','Auto Research run resolution');
  assert.ok($('workflow-history').href.endsWith('/run-auto/history'));assert.equal($('workflow-signals').hidden,false);
  const autoCases=[
    ['revise-report',{'request-expected_revision':'0','request-feedback':'Shorten the executive summary.'},{request:{expected_revision:0,feedback:'Shorten the executive summary.'}}],
    ['expand-report',{'request-expected_revision':'1','request-topic':'Operational recovery'},{request:{expected_revision:1,topic:'Operational recovery'}}],
    ['approve-report',{expected_revision:'2'},{expected_revision:2}],
  ];
  for(const [signalId,fields,expected] of autoCases){
    const card=dom.window.document.querySelector(`[data-signal-id="${signalId}"]`);
    for(const [name,value] of Object.entries(fields))input(`signal-${signalId}-${name}`,value);
    const count=signalRequests.length;card.querySelector('.signal-send').click();
    assert.equal(signalRequests.length,count);assert.equal(card.querySelector('.signal-confirmation').hidden,false);
    card.querySelector('.signal-confirmation button').click();await waitFor(()=>signalRequests.length===count+1,`${signalId} Signal request`);
    assert.equal(signalRequests.at(-1).signal_id,signalId);assert.equal(signalRequests.at(-1).run_id,'run-auto');assert.deepEqual(signalRequests.at(-1).arguments,expected);assert.equal(signalRequests.at(-1).signal_metadata_revision.length,64);
    await waitFor(()=>!card.querySelector('.signal-send').disabled,`${signalId} Signal completion`);
  }
  change('framework','recipes/auto-research/python/google-adk');change('mode','mock');await ready();
  assert.ok($('worker-command').textContent.includes('python/google-adk'));
  assert.ok($('client-command').textContent.includes('start --mode mock'));
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-auto','Google ADK Auto Research run resolution');
  assert.equal($('workflow-signals').hidden,false);
  assert.equal(dom.window.document.querySelectorAll('#signal-controls [data-signal-id]').length,3);

  change('sample','generic-single');change('framework','recipes/generic-single/python/google-adk');change('mode','mock');await ready();
  assert.ok($('web-workflow-id').value.startsWith('generic-single-'));assert.equal($('web-workflow-id').readOnly,true);
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-generic','generic single-Workflow resolution');
  assert.ok($('workflow-queries').href.endsWith('/run-generic/queries'));assert.equal($('workflow-signals').hidden,false);
  const reviseCard=dom.window.document.querySelector('[data-signal-id="revise"]');
  const reviseCount=signalRequests.length;
  input('signal-revise-request-expected_revision','1.5');
  reviseCard.querySelector('.signal-send').click();assert.equal(signalRequests.length,reviseCount);assert.ok($('signal-status').textContent.includes('integer'));
  const privateDetail='private structured detail';
  input('signal-revise-request-expected_revision','2');input('signal-revise-request-detail',privateDetail);
  reviseCard.querySelector('.signal-send').click();await waitFor(()=>signalRequests.length===reviseCount+1,'structured Signal request');
  assert.deepEqual(signalRequests.at(-1).arguments,{request:{expected_revision:2,detail:privateDetail}});
  assert.ok(signalRequestUrls.every(requestUrl=>!requestUrl.includes(privateDetail)));assert.ok(!$('signal-status').textContent.includes(privateDetail));
  await waitFor(()=>!reviseCard.querySelector('.signal-send').disabled,'structured Signal completion');
  assert.equal($('signal-revise-request-expected_revision').value,'');assert.equal($('signal-revise-request-detail').value,'');

  change('sample','generated-go-systemone');change('framework','recipes/generated-go-systemone/go/systemone');change('mode','mock');
  assert.equal($('classifier-field').hidden,true);await ready();
  assert.ok($('worker-command').textContent.includes('./run.sh'));assert.ok($('worker-command').textContent.includes('-mode mock'));
  assert.ok($('client-command').textContent.includes('start'));assert.ok($('client-command').textContent.includes('-mode mock'));
  assert.ok(!$('client-command').textContent.includes('-classifier-id'));assert.ok(!$('client-command').textContent.includes('-catalog-dir'));
  change('mode','live');assert.equal($('classifier-field').hidden,false);assert.equal($('worker-block').hidden,true);
  change('classifier','laya-local');await ready();
  assert.ok($('worker-command').textContent.includes('-confirm-live'));assert.ok($('worker-command').textContent.includes('-classifier-id laya-local'));
  assert.ok(!$('client-command').textContent.includes('-classifier-id'));

  change('sample','recurring-ai-watch');change('framework','recipes/recurring-ai-watch/python/google-adk');change('mode','live');await ready();
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
  holdCheck=true;$('refresh').click();await waitFor(()=>typeof releaseCheck==='function','held service check');
  change('mode','mock');releaseCheck();await ready();assert.ok(!$('check-status').textContent.includes('stale check failure'));holdCheck=false;releaseCheck=undefined;
  change('mode','live');change('classifier','laya-local');change('model','llama3.2-local');await ready();
  holdWarmup=true;$('warmup').click();await waitFor(()=>typeof releaseWarmup==='function','held warm-up');
  change('mode','mock');releaseWarmup();await ready();assert.ok(!$('warmup-status').textContent.includes('stale warm-up failure'));holdWarmup=false;releaseWarmup=undefined;
  change('mode','live');change('classifier','laya-local');change('model','llama3.2-local');await ready();
  const modelFile=path.join(fixture,'models.yaml');fs.writeFileSync(modelFile,fs.readFileSync(modelFile,'utf8').replace('llama3.2:latest','llama3.2:edited'));
  $('refresh').click();await waitFor(()=>$('service-results').textContent.includes('llama3.2:edited'),'refresh checks edited catalog');
  assert.ok($('check-status').textContent.includes('advisory'));
  const cli=path.join(fixture,'recipes/recurring-ai-watch/python/google-adk/main.py');fs.writeFileSync(cli,fs.readFileSync(cli,'utf8').replaceAll('choices=("mock", "live")','choices=("mock",)'));
  $('refresh').click();await waitFor(()=>!$('worker-block').hidden&&$('mode').value==='mock','changed mode choices');
  assert.equal($('mode').options.length,1);assert.equal($('mode-field').hidden,false);assert.equal($('warmup').disabled,true);
  const watchMetadata=path.join(fixture,'recipes/recurring-ai-watch/explorer.yaml'),watchMetadataContents=fs.readFileSync(watchMetadata,'utf8');
  fs.writeFileSync(watchMetadata,'version: 2\n');change('classifier',$('classifier').value);await waitFor(()=>$('signal-metadata-status').hidden===false,'invalid Signal metadata rendering');
  assert.equal($('signal-metadata-status').hidden,false);assert.ok($('signal-metadata-status').textContent.includes('version must be 1'));assert.equal($('signal-controls').children.length,0);assert.equal($('workflow-signals').hidden,true);
  fs.writeFileSync(watchMetadata,watchMetadataContents);
  holdRun=true;$('resolve-run').click();await waitFor(()=>typeof releaseRun==='function','held run lookup');
  change('sample','ticket-routing');releaseRun();await waitFor(()=>$('workflow-run-panel').hidden&&$('run-id').value==='','stale run response ignored');holdRun=false;releaseRun=undefined;
  assert.equal($('language').value,'go');assert.equal($('framework-field').hidden,false);assert.equal($('framework').value,'recipes/ticket-routing/go/systemone');assert.equal($('model-field').hidden,true);assert.equal($('mode').value,'');
  change('mode','mock');await ready();assert.equal($('classifier-field').hidden,true);assert.ok($('worker-command').textContent.includes('worker -mode mock'));assert.ok($('client-command').textContent.includes('start -mode mock'));assert.ok(!$('client-command').textContent.includes('-text'));assert.ok(!$('cadence-web').hidden);
  assert.equal($('workflow-run-panel').hidden,false);assert.equal($('web-workflow-id').readOnly,true);assert.equal($('web-workflow-id').value,'ticket-routing-go-systemone-mock-demo');assert.equal($('resolve-run').disabled,false);
  input('run-id','ticket historical/1');assert.ok($('workflow-history').href.includes('/ticket-routing-go-systemone-mock-demo/ticket%20historical%2F1/history'));assert.equal($('workflow-signals').hidden,true);
  $('resolve-run').click();await waitFor(()=>$('run-id').value==='run-ticket','Ticket Routing run resolution');assert.ok($('workflow-queries').href.endsWith('/run-ticket/queries'));assert.equal($('workflow-signals').hidden,true);
  assert.equal($('signal-controls').children.length,0);
  change('mode','live');assert.equal($('worker-block').hidden,true);change('classifier','laya-local');await ready();assert.ok($('worker-command').textContent.includes('-confirm-live'));assert.ok($('client-command').textContent.includes('start -mode live'));assert.ok(!$('client-command').textContent.includes('-classifier-id'));
  rejectClipboard=true;const workerCopy=dom.window.document.querySelector('[data-copy="worker"]');workerCopy.click();await waitFor(()=>$('copy-status').textContent.includes('Clipboard unavailable'),'clipboard fallback');assert.equal(workerCopy.textContent,'Copy failed');
  fs.unlinkSync(path.join(fixture,'classifiers.yaml'));
  change('mode','mock');$('refresh').click();await waitFor(()=>!$('refresh').disabled,'missing catalog refresh');await ready();
  assert.equal($('classifier-field').hidden,true);assert.ok($('client-command').textContent.includes('-mode mock'));
  change('mode','live');await failed();assert.ok($('selection-status').textContent.includes('classifiers.yaml'));
  const incomplete=path.join(fixture,'recipes/new-recipe/java');fs.mkdirSync(incomplete,{recursive:true});fs.writeFileSync(path.join(incomplete,'../README.md'),'# New incomplete recipe\n');
  $('refresh').click();await waitFor(()=>$('sample').options.length===9,'new recipe discovery');change('sample','new-recipe');
  assert.equal($('language').value,'java');assert.equal($('language-field').hidden,false);assert.equal($('readme').hidden,false);await failed();assert.ok($('selection-status').textContent.includes('adapter'));
  assert.deepEqual(errors,[]);
  console.log('PASS: pre-paint dark/light theme persistence and failure handling, state-preserving accessible toggle, real HTTP + six-step DOM flow, generic/Human in the Loop latest-run links, generated Go SystemOne mock/live classifier visibility, Ticket Routing generic commands and Cadence Web Queries link, manual Run ID deep links, metadata-driven Watch, scalar Human in the Loop Signals, structured object Signals, invalid metadata feedback, confirmations, stale/Continue-As-New feedback, visible single-choice selectors, explicit modes, manual checks, local warm-up availability, Mac terminal handoff/fallback, individual controls, catalog refresh, clipboard fallback, and incomplete recipes.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(dom)dom.window.close();
  if(server){server.kill('SIGINT');await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill('SIGTERM');resolve();},2000);server.once('exit',()=>{clearTimeout(timer);resolve();});});}
  fs.rmSync(fixture,{recursive:true,force:true});
});
