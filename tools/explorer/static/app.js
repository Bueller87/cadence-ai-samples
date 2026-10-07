"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let state, result, recipe, variant, requestVersion = 0, runRequestVersion = 0, checkedSelection = "";
  const node = (tag,text) => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};

  function options(id, entries, {placeholder, defaultValue}={}) {
    const control=$(id), previous=control.value;
    control.replaceChildren();
    if(entries.length>1 || !entries.length) {const option=node("option",placeholder||"Choose…");option.value="";control.append(option);}
    for(const entry of entries){const option=node("option",entry.label||entry.id);option.value=entry.id;control.append(option);}
    control.value=entries.some(e=>e.id===previous)?previous:entries.length===1?entries[0].id:entries.some(e=>e.id===defaultValue)?defaultValue:"";
  }

  function clear(message, isError=false) {
    requestVersion++;result=null;
    resetWorkflowRun("Complete a supported selection to resolve a run.");
    for(const id of ["setup-block","worker-block","client-block","controls-block"])$(id).hidden=true;
    for(const id of ["setup-command","worker-command","client-command"])$(id).textContent="";
    $("control-commands").replaceChildren();$("warmup").disabled=true;
    for(const id of ["worker-terminal-status","start-terminal-status"])$(id).textContent="";
    for(const id of ["open-worker","open-start"]){$(id).hidden=true;$(id).disabled=false;}
    $("terminal-note").hidden=true;
    $("warmup-results").replaceChildren();$("warmup-status").textContent="Complete a selection to see whether warm-up applies.";
    $("selection-status").textContent=message;$("selection-status").className=isError?"notice error":"notice";
    $("copy-status").textContent="";
  }

  function resetWorkflowRun(message) {
    runRequestVersion++;
    $("resolve-run").hidden=true;$("resolve-run").disabled=true;
    $("workflow-run-panel").hidden=true;$("run-id").value="";
    $("web-workflow-id").textContent="";$("web-cluster").textContent="";
    $("workflow-run-status").textContent="Run not resolved.";$("workflow-run-status").className="small";
    for(const id of ["workflow-history","workflow-queries"]){$(id).hidden=true;$(id).removeAttribute("href");}
    $("workflow-run-scope").textContent=message;
  }

  function markChecksStale() {
    if(checkedSelection){
      $("check-status").textContent="Selection changed. These service results are stale; refresh to check again.";
      $("check-status").className="small stale";
      for(const badge of $("service-results").querySelectorAll(".status"))badge.textContent="stale";
    }
    $("warmup-results").replaceChildren();
  }

  function field(id,show){$(id+"-field").hidden=!show;}
  function query() {
    return new URLSearchParams({sample:recipe.id,implementation:variant.id,
      mode:variant.modes.length?$("mode").value:"",model:$("model").value,
      classifier:$("classifier").value,count:$("count").value,concurrency:$("concurrency").value});
  }

  async function flow() {
    markChecksStale();
    clear("Complete the selections to generate matching commands.");
    recipe=state?.recipes.find(r=>r.id===$("sample").value);variant=null;
    for(const id of ["language","framework","mode","model","classifier"])field(id,false);
    $("batch-fields").hidden=true;$("readme").hidden=!recipe?.readme;$("implementation").textContent="";
    $("scenario").textContent=recipe?.description||"Choose a sample to see its scenario.";
    if(recipe?.readme)$("readme").href=recipe.readme;
    if(!recipe){clear("Choose a sample to begin.");return;}
    if(!recipe.variants.length){clear("This recipe has no discovered implementation directories. Read its README.",true);return;}
    const languages=[...new Set(recipe.variants.map(v=>v.language))];field("language",true);
    options("language",languages.map(id=>({id,label:id})),{placeholder:"Choose a language"});
    const implementations=recipe.variants.filter(v=>v.language===$("language").value);
    if(!implementations.length)return;
    const hasFramework=implementations.some(v=>v.framework!==null);
    if(hasFramework){field("framework",true);options("framework",implementations.map(v=>({id:v.id,label:v.framework||"Bare implementation"})),{placeholder:"Choose a framework"});variant=implementations.find(v=>v.id===$("framework").value);}
    else variant=implementations[0];
    if(!variant)return;
    $("implementation").append(node("span","Implementation: "),node("code",variant.id));
    if(!variant.supported){clear(variant.reason,true);return;}
    if(variant.modes.length){field("mode",true);$("mode-label").textContent=variant.kind==="go"?"Run action":"Execution mode";options("mode",variant.modes.map(id=>({id})),{placeholder:"Choose a mode explicitly"});}
    const mode=$("mode").value;
    if(variant.model){field("model",true);options("model",state.models.map(e=>({id:e.id})),{defaultValue:variant.defaults["--model-id"]||variant.defaults["-model-id"]});}
    if(variant.classifier){
      field("classifier",true);
      let entries=state.classifiers.map(e=>({id:e.id}));
      if(variant.kind==="go"&&!variant.live_modes.includes(mode))entries=mode==="worker"?[{id:variant.defaults["-classifier-id"],label:variant.defaults["-classifier-id"]+" (built-in)"},...entries]:[{id:variant.defaults["-classifier-id"],label:variant.defaults["-classifier-id"]+" (built-in)"}];
      options("classifier",entries,{defaultValue:variant.defaults["--classifier-id"]||variant.defaults["-classifier-id"]});
    }
    if(mode==="live-batch"&&variant.kind==="go"){$("batch-fields").hidden=false;const limits=variant.batch_limits;$("count").max=limits.maximumLiveBatchCount||"";$("concurrency").max=limits.maximumLiveConcurrency||"";$("batch-limits").textContent=`Recipe limits: count ≤ ${limits.maximumLiveBatchCount}, concurrency ≤ ${limits.maximumLiveConcurrency}.`;}
    const usesClassifierCatalog=variant.classifier&&!(variant.kind==="go"&&!variant.live_modes.includes(mode)&&(mode!=="worker"||$("classifier").value===variant.defaults["-classifier-id"]));
    for(const [kind,needed] of [["models",variant.model],["classifiers",usesClassifierCatalog]])if(state.catalog_errors[kind]&&needed){clear(state.catalog_errors[kind],true);return;}
    if((variant.modes.length&&!mode)||(variant.model&&!$("model").value)||(variant.classifier&&!$("classifier").value))return;
    await resolve();
  }

  async function resolve() {
    const version=++requestVersion;
    $("selection-status").textContent="Reading the selected recipe and validating its options…";
    try{const response=await fetch("/api/resolve?"+query(),{cache:"no-store"});const body=await response.json();if(version!==requestVersion)return;if(!response.ok)throw Error(body.error||"Could not resolve this selection.");result=body;render();}
    catch(error){if(version===requestVersion)clear(error.message,true);}
  }

  function render(){
    $("selection-status").className="notice ready";$("selection-status").textContent=result.live?"Live selection ready. Credentials and services stay in your local Worker.":result.mock?"Mock selection ready. No provider calls.":"Commands resolved from this implementation.";
    $("setup-command").textContent=result.setup;$("setup-block").hidden=false;
    $("worker-command").textContent=result.worker;$("worker-block").hidden=false;
    $("client-command").textContent=result.start;$("client-block").hidden=!result.start;
    const terminalAvailable=state?.terminal_handoff===true;
    $("open-worker").hidden=!terminalAvailable;
    $("open-start").hidden=!terminalAvailable||!result.start;
    $("terminal-note").hidden=!terminalAvailable;
    $("controls-block").hidden=!result.controls.length;$("control-commands").replaceChildren();
    for(const control of result.controls){const head=node("div");head.className="code-head";head.append(node("span",control.name));const button=node("button","Copy "+control.name);button.type="button";button.addEventListener("click",()=>copyText(control.command,button));head.append(button);const pre=node("pre");pre.append(node("code",control.command));$("control-commands").append(head,pre);}
    $("credentials").textContent=result.credentials.length?"Worker environment variables: "+result.credentials.join(", ")+". Set values only in Terminal 1.":result.live?"This selection needs no AI API keys.":"Mock mode needs no AI API keys.";
    $("warmup").disabled=!result.local.length;
    $("warmup-status").textContent=result.local.length?"Warm-up is available for "+result.local.map(item=>item.service).join(" and ")+".":result.mock?"Mock mode does not need local warm-up.":"This selection has no local inference component to warm.";
    const web=result.cadence_web_url.replace(/\/$/,"");
    $("cadence-web").href=`${web}/redirects/domain/${encodeURIComponent(result.domain)}/workflows`;
    if(recipe?.id==="recurring-ai-watch"&&result.workflow_id){
      $("workflow-run-panel").hidden=false;$("resolve-run").hidden=false;$("resolve-run").disabled=false;
      $("web-workflow-id").textContent=result.workflow_id;$("web-cluster").textContent=result.cadence_cluster;
      $("workflow-run-scope").textContent="Resolve the current run, or paste an exact Run ID.";
    }else{
      $("workflow-run-scope").textContent=recipe?.id==="ticket-routing"?"Direct run links are deferred for Ticket Routing because its actions can create multiple Workflows.":"This selection does not expose one Workflow ID for direct links.";
    }
    $("read-at").textContent="Recipe files read at "+new Date(result.read_at).toLocaleTimeString()+".";
  }

  function renderServices(target, services) {
    target.replaceChildren();
    for(const service of services){
      const card=node("article");const top=node("div");top.className="service-head";
      top.append(node("strong",service.name));const badge=node("span",service.state);badge.className="status "+service.state.replaceAll(" ","-");top.append(badge);
      card.append(top,node("code",service.endpoint||"Not applicable"),node("p",service.detail));target.append(card);
    }
  }

  async function checkServices(){
    if(!result)return;
    const version=requestVersion;$("check-status").className="small";$("check-status").textContent="Checking selected services…";$("refresh").disabled=true;
    try{const response=await fetch("/api/check?"+query(),{cache:"no-store"});const body=await response.json();if(version!==requestVersion)return;if(!response.ok)throw Error(body.error||"Service checks failed.");checkedSelection=body.selection;renderServices($("service-results"),body.services);$("check-status").textContent="Checked "+new Date(body.checked_at).toLocaleTimeString()+". Results are advisory.";}
    catch(error){$("check-status").className="small error";$("check-status").textContent=error.message;}
    finally{$("refresh").disabled=false;}
  }

  async function warmup(){
    if(!result||!result.local.length)return;
    const version=requestVersion;$("warmup").disabled=true;$("warmup-status").textContent="Warming selected local services…";$("warmup-results").replaceChildren();
    try{const response=await fetch("/api/warmup",{method:"POST",headers:{"Content-Type":"application/json","Origin":location.origin},body:JSON.stringify(Object.fromEntries(query()))});const body=await response.json();if(version!==requestVersion)return;if(!response.ok)throw Error(body.error||"Warm-up failed.");renderServices($("warmup-results"),body.components);$("warmup-status").textContent=body.note;}
    catch(error){$("warmup-status").className="small error";$("warmup-status").textContent=error.message;}
    finally{if(version===requestVersion)$("warmup").disabled=!result?.local.length;}
  }

  async function copyText(text,button){
    const original=button?.dataset.copyLabel||button?.textContent;
    if(button&&!button.dataset.copyLabel)button.dataset.copyLabel=original;
    try{
      await navigator.clipboard.writeText(text);
      $("copy-status").textContent="Copied.";
      if(button){button.textContent="Copied ✓";button.classList.add("copied");setTimeout(()=>{button.textContent=original;button.classList.remove("copied");},2000);}
    }catch{
      $("copy-status").textContent="Clipboard unavailable. Select the command text and copy it manually.";
      if(button){button.textContent="Copy failed";setTimeout(()=>{button.textContent=original;},2000);}
    }
  }

  async function openTerminal(action,button){
    if(!result||!result[action])return;
    const version=requestVersion,status=$(action==="worker"?"worker-terminal-status":"start-terminal-status");
    let copied=true;
    button.disabled=true;status.className="small";status.textContent="Copying command and opening Terminal…";
    try{await navigator.clipboard.writeText(result[action]);}
    catch{copied=false;}
    try{
      const payload={...Object.fromEntries(query()),action};
      const response=await fetch("/api/terminal",{method:"POST",headers:{"Content-Type":"application/json","Origin":location.origin},body:JSON.stringify(payload)});
      const body=await response.json();if(version!==requestVersion)return;
      if(!response.ok)throw Error(body.error||body.detail||"Terminal handoff failed.");
      if(body.state==="prefilled")status.textContent="Ready in Terminal. Review the command and press Enter.";
      else status.textContent=body.detail;
    }catch(error){if(version===requestVersion)status.textContent=(copied?error.message+" The command is still copied.":error.message);}
    finally{if(version===requestVersion)button.disabled=false;}
  }

  function updateRunLinks(source="entered"){
    const runId=$("run-id").value.trim();
    if(!result||!runId){
      for(const id of ["workflow-history","workflow-queries"]){$(id).hidden=true;$(id).removeAttribute("href");}
      $("workflow-run-status").textContent="Run not resolved.";$("workflow-run-status").className="small";
      return;
    }
    const web=result.cadence_web_url.replace(/\/$/,"");
    const base=`${web}/domains/${encodeURIComponent(result.domain)}/${encodeURIComponent(result.cadence_cluster)}/workflows/${encodeURIComponent(result.workflow_id)}/${encodeURIComponent(runId)}`;
    $("workflow-history").href=base+"/history";$("workflow-queries").href=base+"/queries";
    $("workflow-history").hidden=false;$("workflow-queries").hidden=false;
    $("workflow-run-status").className="small ready";
    $("workflow-run-status").textContent=source==="resolved"?"Current Run ID resolved through Cadence-Web.":"Using the entered Run ID.";
  }

  async function resolveWorkflowRun(){
    if(!result||recipe?.id!=="recurring-ai-watch"||!result.workflow_id)return;
    const selectionVersion=requestVersion,version=++runRequestVersion;
    $("resolve-run").disabled=true;$("workflow-run-status").className="small";$("workflow-run-status").textContent="Resolving the current run through Cadence-Web…";
    try{
      const response=await fetch("/api/workflow-run?"+query(),{cache:"no-store"});
      const body=await response.json();if(selectionVersion!==requestVersion||version!==runRequestVersion)return;
      if(!response.ok)throw Error(body.error||"Run lookup failed.");
      if(body.state==="found"){$("run-id").value=body.run_id;updateRunLinks("resolved");}
      else{$("run-id").value="";updateRunLinks();$("workflow-run-status").className="small error";$("workflow-run-status").textContent=body.detail||"Run lookup failed.";}
    }catch(error){if(selectionVersion===requestVersion&&version===runRequestVersion){$("run-id").value="";updateRunLinks();$("workflow-run-status").className="small error";$("workflow-run-status").textContent=error.message;}}
    finally{if(selectionVersion===requestVersion&&version===runRequestVersion)$("resolve-run").disabled=false;}
  }

  async function refresh(runChecks=false){
    clear("Reading recipes…");$("refresh").disabled=true;$("load-status").textContent="Reading the current checkout…";
    try{const response=await fetch("/api/state",{cache:"no-store"});if(!response.ok)throw Error("Could not read recipes.");state=await response.json();options("sample",state.recipes.map(r=>({id:r.id,label:r.title})),{placeholder:"Choose a sample"});$("load-status").textContent=`${state.recipes.length} samples in this checkout`;await flow();if(runChecks&&result)await checkServices();}
    catch(error){state=null;for(const id of ["sample","language","framework","mode","model","classifier"])$(id).replaceChildren();$("load-status").textContent=error.message;clear("Refresh to retry reading this checkout.",true);}
    finally{$("refresh").disabled=false;}
  }

  $("sample").addEventListener("change",()=>{$("language").value="";$("framework").value="";$("mode").value="";flow();});
  $("language").addEventListener("change",()=>{$("framework").value="";$("mode").value="";flow();});
  $("framework").addEventListener("change",()=>{$("mode").value="";flow();});
  for(const id of ["mode","model","classifier"])$(id).addEventListener("change",flow);
  for(const id of ["count","concurrency"])$(id).addEventListener("input",flow);
  $("refresh").addEventListener("click",()=>refresh(true));$("warmup").addEventListener("click",warmup);
  $("resolve-run").addEventListener("click",resolveWorkflowRun);$("run-id").addEventListener("input",()=>{runRequestVersion++;$("resolve-run").disabled=false;updateRunLinks("entered");});
  document.querySelectorAll("[data-copy]").forEach(button=>button.addEventListener("click",()=>{if(result)copyText(result[button.dataset.copy],button);}));
  document.querySelectorAll("[data-terminal]").forEach(button=>button.addEventListener("click",()=>openTerminal(button.dataset.terminal,button)));
  refresh(false);
})();
