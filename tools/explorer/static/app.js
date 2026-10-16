"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let state, result, recipe, variant, requestVersion = 0, runRequestVersion = 0, signalRequestVersion = 0, resolvedRunId = "", checkedSelection = "";
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
    $("prepare").disabled=true;$("prepare-status").className="small";$("prepare-status").textContent="unchecked: Complete a selection to check preparation.";
    $("prepare-diagnostic").hidden=true;$("prepare-diagnostic").querySelector("code").textContent="";
    for(const id of ["worker-terminal-status","start-terminal-status"])$(id).textContent="";
    for(const id of ["open-worker","open-start"]){$(id).hidden=true;$(id).disabled=false;}
    $("terminal-note").hidden=true;
    $("warmup-results").replaceChildren();$("warmup-status").textContent="Complete a selection to see whether warm-up applies.";
    $("selection-status").textContent=message;$("selection-status").className=isError?"notice error":"notice";
    $("copy-status").textContent="";
  }

  function resetWorkflowRun(message) {
    runRequestVersion++;signalRequestVersion++;resolvedRunId="";
    $("resolve-run").hidden=true;$("resolve-run").disabled=true;
    $("workflow-run-panel").hidden=true;$("run-id").value="";
    $("web-workflow-id").value="";$("web-workflow-id").readOnly=true;
    $("workflow-id-help").textContent="";$("web-cluster").textContent="";
    $("workflow-run-status").textContent="Run not resolved.";$("workflow-run-status").className="small";
    for(const id of ["workflow-history","workflow-queries"]){$(id).hidden=true;$(id).removeAttribute("href");}
    $("workflow-signals").hidden=true;$("signal-controls").replaceChildren();$("signal-status").textContent="";
    $("signal-metadata-status").hidden=true;$("signal-metadata-status").textContent="";
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

  function workflowRunQuery() {
    const parameters=query();
    if(!result?.workflow_id)parameters.set("workflow_id",$("web-workflow-id").value.trim());
    const previousRunId=$("run-id").value.trim();
    if(previousRunId)parameters.set("run_id",previousRunId);
    return parameters;
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
    const legacyGoMockMode=variant.kind==="go"&&!variant.generic&&Array.isArray(variant.live_modes)&&!variant.live_modes.includes(mode);
    const showClassifier=variant.classifier&&!(variant.generic&&variant.kind==="go"&&mode==="mock");
    if(showClassifier){
      field("classifier",true);
      let entries=state.classifiers.map(e=>({id:e.id}));
      if(legacyGoMockMode)entries=mode==="worker"?[{id:variant.defaults["-classifier-id"],label:variant.defaults["-classifier-id"]+" (built-in)"},...entries]:[{id:variant.defaults["-classifier-id"],label:variant.defaults["-classifier-id"]+" (built-in)"}];
      options("classifier",entries,{defaultValue:variant.defaults["--classifier-id"]||variant.defaults["-classifier-id"]});
    }
    if(mode==="live-batch"&&variant.kind==="go"){$("batch-fields").hidden=false;const limits=variant.batch_limits;$("count").max=limits.maximumLiveBatchCount||"";$("concurrency").max=limits.maximumLiveConcurrency||"";$("batch-limits").textContent=`Recipe limits: count ≤ ${limits.maximumLiveBatchCount}, concurrency ≤ ${limits.maximumLiveConcurrency}.`;}
    const usesClassifierCatalog=variant.classifier&&(variant.generic&&variant.kind==="go"?mode==="live":!(legacyGoMockMode&&(mode!=="worker"||$("classifier").value===variant.defaults["-classifier-id"])));
    for(const [kind,needed] of [["models",variant.model],["classifiers",usesClassifierCatalog]])if(state.catalog_errors[kind]&&needed){clear(state.catalog_errors[kind],true);return;}
    if((variant.modes.length&&!mode)||(variant.model&&!$("model").value)||(usesClassifierCatalog&&!$("classifier").value))return;
    await resolve();
  }

  async function resolve() {
    const version=++requestVersion;
    $("selection-status").textContent="Reading the selected recipe and validating its options…";
    try{const response=await fetch("/api/resolve?"+query(),{cache:"no-store"});const body=await response.json();if(version!==requestVersion)return;if(!response.ok)throw Error(body.error||"Could not resolve this selection.");result=body;render();}
    catch(error){if(version===requestVersion)clear(error.message,true);}
  }

  function renderTerminalNote(){
    const terminalAvailable=state?.terminal_handoff===true;
    $("terminal-note").hidden=!terminalAvailable;
    if(terminalAvailable)$("terminal-note").textContent=result?.preparation?.state==="prepared"?
      "macOS may ask for Automation or Accessibility permission. The command is pasted for review and is never run automatically.":
      "The launcher will prepare changed dependencies after you press Enter. Prepare them in Step 2 first for predictable timing. The command is pasted for review and is never run automatically.";
  }

  function render(){
    $("selection-status").className="notice ready";$("selection-status").textContent=result.live?"Live selection ready. Credentials and services stay in your local Worker.":result.mock?"Mock selection ready. No provider calls.":"Commands resolved from this implementation.";
    $("setup-command").textContent=result.setup;$("setup-block").hidden=false;
    const preparation=result.preparation||{state:"unchecked",detail:"Preparation has not been checked."};
    $("prepare").disabled=false;$("prepare-status").className="small "+preparation.state.replaceAll(" ","-");
    $("prepare-status").textContent=`${preparation.state}: ${preparation.detail}`;
    $("worker-command").textContent=result.worker;$("worker-block").hidden=false;
    $("client-command").textContent=result.start;$("client-block").hidden=!result.start;
    const terminalAvailable=state?.terminal_handoff===true;
    $("open-worker").hidden=!terminalAvailable;
    $("open-start").hidden=!terminalAvailable||!result.start;
    renderTerminalNote();
    $("controls-block").hidden=!result.controls.length;$("control-commands").replaceChildren();
    for(const control of result.controls){const head=node("div");head.className="code-head";head.append(node("span",control.name));const button=node("button","Copy "+control.name);button.type="button";button.addEventListener("click",()=>copyText(control.command,button));head.append(button);const pre=node("pre");pre.append(node("code",control.command));$("control-commands").append(head,pre);}
    $("credentials").textContent=result.credentials.length?"Worker environment variables: "+result.credentials.join(", ")+". Set values only in Terminal 1.":result.live?"This selection needs no AI API keys.":"Mock mode needs no AI API keys.";
    $("warmup").disabled=!result.local.length;
    $("warmup-status").textContent=result.local.length?"Warm-up is available for "+result.local.map(item=>item.service).join(" and ")+".":result.mock?"Mock mode does not need local warm-up.":"This selection has no local inference component to warm.";
    const web=result.cadence_web_url.replace(/\/$/,"");
    $("cadence-web").href=`${web}/redirects/domain/${encodeURIComponent(result.domain)}/workflows`;
    const resolvedWorkflowId=typeof result.workflow_id==="string"&&result.workflow_id.trim();
    $("workflow-run-panel").hidden=false;$("resolve-run").hidden=false;
    $("web-workflow-id").value=resolvedWorkflowId||"";$("web-workflow-id").readOnly=Boolean(resolvedWorkflowId);
    $("web-cluster").textContent=result.cadence_cluster;
    if(resolvedWorkflowId){
      $("resolve-run").disabled=false;
      $("workflow-id-help").textContent="Resolved from the selected recipe command.";
      $("workflow-run-scope").textContent="Resolve the current run, or paste an exact Run ID.";
    }else{
      $("resolve-run").disabled=true;
      $("workflow-id-help").textContent=recipe?.id==="ticket-routing"?"Enter an exact parent, child, or batch Workflow ID. The Explorer will not choose one.":"Enter the exact Workflow ID produced by this recipe. The Explorer will not choose one.";
      $("workflow-run-scope").textContent="This selection does not expose one safe Workflow ID. Enter the exact Workflow before resolving a run.";
    }
    renderSignalControls();
    $("signal-metadata-status").textContent=result.signal_metadata_error||"";
    $("signal-metadata-status").hidden=!result.signal_metadata_error;
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
    catch(error){if(version===requestVersion){$("check-status").className="small error";$("check-status").textContent=error.message;}}
    finally{if(version===requestVersion)$("refresh").disabled=false;}
  }

  async function warmup(){
    if(!result||!result.local.length)return;
    const version=requestVersion;$("warmup").disabled=true;$("warmup-status").textContent="Warming selected local services…";$("warmup-results").replaceChildren();
    try{const response=await fetch("/api/warmup",{method:"POST",headers:{"Content-Type":"application/json","Origin":location.origin},body:JSON.stringify(Object.fromEntries(query()))});const body=await response.json();if(version!==requestVersion)return;if(!response.ok)throw Error(body.error||"Warm-up failed.");renderServices($("warmup-results"),body.components);$("warmup-status").textContent=body.note;}
    catch(error){if(version===requestVersion){$("warmup-status").className="small error";$("warmup-status").textContent=error.message;}}
    finally{if(version===requestVersion)$("warmup").disabled=!result?.local.length;}
  }

  async function prepareRecipe(){
    if(!result?.launcher_revision)return;
    const version=requestVersion;
    $("prepare").disabled=true;$("prepare-status").className="small preparing";
    $("prepare-status").textContent="preparing: Installing this implementation's declared dependencies…";
    $("prepare-diagnostic").hidden=true;$("prepare-diagnostic").querySelector("code").textContent="";
    try{
      const payload={...Object.fromEntries(query()),launcher_revision:result.launcher_revision};
      const response=await fetch("/api/prepare",{method:"POST",headers:{"Content-Type":"application/json","Origin":location.origin},body:JSON.stringify(payload)});
      const body=await response.json();if(version!==requestVersion)return;
      if(!response.ok)throw Error(body.error||"Preparation failed.");
      result.preparation={state:body.state,detail:body.detail};
      renderTerminalNote();
      $("prepare-status").className="small "+body.state.replaceAll(" ","-");
      $("prepare-status").textContent=`${body.state}: ${body.detail}`;
      if(body.diagnostic){$("prepare-diagnostic").querySelector("code").textContent=body.diagnostic;$("prepare-diagnostic").hidden=false;}
    }catch(error){
      if(version!==requestVersion)return;
      result.preparation={state:"failed",detail:error.message};
      renderTerminalNote();
      $("prepare-status").className="small failed";$("prepare-status").textContent=`failed: ${error.message}`;
    }finally{if(version===requestVersion)$("prepare").disabled=false;}
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

  function signalFieldValue(control,argument,field){
    const raw=field.value;
    if(raw===""){
      if(argument.required)throw Error(`Enter ${argument.label}.`);
      return [false,null];
    }
    let value=raw;
    if(argument.type==="integer"){
      value=Number(raw);if(!Number.isInteger(value))throw Error(`${argument.label} must be an integer.`);
    }else if(argument.type==="number"){
      value=Number(raw);if(!Number.isFinite(value))throw Error(`${argument.label} must be a number.`);
    }else if(argument.type==="boolean"){
      value=raw==="true";
    }
    const constraints=argument.constraints||{};
    if(argument.type==="string"&&((constraints.min_length??0)>value.length||value.length>(constraints.max_length??4096)))throw Error(`${argument.label} does not satisfy its length limits.`);
    if(["integer","number"].includes(argument.type)&&((constraints.minimum??-1e12)>value||value>(constraints.maximum??1e12)))throw Error(`${argument.label} is outside its allowed range.`);
    return [true,value];
  }

  function signalArguments(control,card){
    const values={};
    for(const argument of control.arguments){
      if(argument.type==="object"){
        const objectValue={};let present=false;
        const fields=argument.fields.map(member=>[
          member,card.querySelector(`[data-signal-argument="${argument.name}"][data-signal-field="${member.name}"]`)]);
        if(!argument.required&&fields.every(([,field])=>field.value===""))continue;
        for(const [member,field] of fields){
          const [memberPresent,value]=signalFieldValue(control,member,field);
          if(memberPresent){objectValue[member.name]=value;present=true;}
        }
        if(present||argument.required)values[argument.name]=objectValue;
      }else{
        const field=card.querySelector(`[data-signal-argument="${argument.name}"]`);
        const [present,value]=signalFieldValue(control,argument,field);
        if(present)values[argument.name]=value;
      }
    }
    return values;
  }

  function appendSignalField(control,card,argument,fieldId,argumentName,fieldName){
    const label=node("label",argument.label+(argument.required?"":" (optional)"));label.htmlFor=fieldId;
    let field;
    if(argument.type==="boolean"){
      field=node("select");const empty=node("option","Choose…");empty.value="";field.append(empty);
      for(const value of ["true","false"]){const option=node("option",value);option.value=value;field.append(option);}
    }else{
      field=node("input");field.type=argument.type==="string"?"text":"number";
      if(argument.type==="integer")field.step="1";else if(argument.type==="number")field.step="any";
      if(argument.constraints?.max_length!==undefined)field.maxLength=argument.constraints.max_length;
      if(argument.constraints?.minimum!==undefined)field.min=argument.constraints.minimum;
      if(argument.constraints?.maximum!==undefined)field.max=argument.constraints.maximum;
    }
    field.id=fieldId;field.dataset.signalArgument=argumentName;if(fieldName)field.dataset.signalField=fieldName;field.autocomplete="off";field.spellcheck=false;
    field.addEventListener("input",()=>{const confirmation=card.querySelector(".signal-confirmation");if(confirmation)confirmation.hidden=true;$("signal-status").textContent="";});
    card.append(label,field,node("p",argument.help));
  }

  function resetSignalForms(clearValues=true){
    for(const card of $("signal-controls").querySelectorAll(".signal-control")){
      for(const field of card.querySelectorAll("button,input,select"))field.disabled=false;
      if(clearValues)for(const field of card.querySelectorAll("[data-signal-argument]"))field.value="";
      const confirmation=card.querySelector(".signal-confirmation");if(confirmation)confirmation.hidden=true;
      const send=card.querySelector(".signal-send");if(send){send.disabled=false;send.textContent=send.dataset.defaultLabel;send.classList.remove("copied","failed");}
    }
  }

  function setSignalControlsDisabled(disabled){
    for(const field of $("signal-controls").querySelectorAll("button,input,select"))field.disabled=disabled;
  }

  function renderSignalControls(){
    $("signal-controls").replaceChildren();
    for(const control of result?.signals||[]){
      const card=node("article");card.className="signal-control";card.dataset.signalId=control.id;
      const heading=node("div");heading.className="signal-control-heading";heading.append(node("strong",control.label));
      if(control.intent!=="normal"){const intent=node("span",control.intent);intent.className="signal-intent "+control.intent;heading.append(intent);}
      card.append(heading,node("p",control.description));
      for(const argument of control.arguments){
        if(argument.type==="object"){
          card.append(node("p",argument.label+(argument.required?"":" (optional)")),node("p",argument.help));
          for(const member of argument.fields){
            appendSignalField(control,card,member,`signal-${control.id}-${argument.name}-${member.name}`,argument.name,member.name);
          }
        }else{
          appendSignalField(control,card,argument,`signal-${control.id}-${argument.name}`,argument.name);
        }
      }
      const actions=node("div");actions.className="button-group signal-actions";
      const send=node("button",`Send ${control.label}`);send.type="button";send.className="signal-send"+(control.intent==="destructive"?" danger":"");send.dataset.defaultLabel=send.textContent;
      if(control.intent==="normal")send.addEventListener("click",()=>sendWorkflowSignal(control,card,send));
      else{
        const confirmation=node("div");confirmation.className="signal-confirmation";confirmation.hidden=true;
        const message=node("p",`${control.label} requires confirmation.`);
        const confirm=node("button",`Confirm ${control.label}`);confirm.type="button";if(control.intent==="destructive")confirm.className="danger";
        const cancel=node("button","Cancel");cancel.type="button";cancel.className="secondary";
        send.addEventListener("click",()=>{try{signalArguments(control,card);confirmation.hidden=false;$("signal-status").textContent="";}catch(error){$("signal-status").className="small error";$("signal-status").textContent=error.message;}});
        confirm.addEventListener("click",()=>sendWorkflowSignal(control,card,send));
        cancel.addEventListener("click",()=>{confirmation.hidden=true;});
        confirmation.append(message,confirm,cancel);card.append(confirmation);
      }
      actions.append(send);card.append(actions);$("signal-controls").append(card);
    }
  }

  function updateRunLinks(source="entered",message=""){
    const runId=$("run-id").value.trim();
    const workflowId=$("web-workflow-id").value.trim();
    signalRequestVersion++;resolvedRunId=source==="resolved"?runId:"";
    $("workflow-signals").hidden=source!=="resolved"||!runId||!result?.signals?.length;$("signal-status").textContent="";
    resetSignalForms(true);
    if(!result||!workflowId||!runId){
      for(const id of ["workflow-history","workflow-queries"]){$(id).hidden=true;$(id).removeAttribute("href");}
      $("workflow-run-status").textContent="Run not resolved.";$("workflow-run-status").className="small";
      return;
    }
    const web=result.cadence_web_url.replace(/\/$/,"");
    const base=`${web}/domains/${encodeURIComponent(result.domain)}/${encodeURIComponent(result.cadence_cluster)}/workflows/${encodeURIComponent(workflowId)}/${encodeURIComponent(runId)}`;
    $("workflow-history").href=base+"/history";$("workflow-queries").href=base+"/queries";
    $("workflow-history").hidden=false;$("workflow-queries").hidden=false;
    $("workflow-run-status").className="small ready";
    $("workflow-run-status").textContent=message||(source==="resolved"?"Current Run ID resolved through Cadence-Web.":"Using the entered Run ID.");
  }

  async function resolveWorkflowRun(){
    if(!result||!$("web-workflow-id").value.trim())return;
    const selectionVersion=requestVersion,version=++runRequestVersion;
    $("resolve-run").disabled=true;$("workflow-run-status").className="small";$("workflow-run-status").textContent="Resolving the current run through Cadence-Web…";
    try{
      const response=await fetch("/api/workflow-run?"+workflowRunQuery(),{cache:"no-store"});
      const body=await response.json();if(selectionVersion!==requestVersion||version!==runRequestVersion)return;
      if(!response.ok)throw Error(body.error||"Run lookup failed.");
      if(body.state==="found"||body.state==="stale run"){$("run-id").value=body.run_id;updateRunLinks("resolved",body.detail);if(body.state==="stale run")$("workflow-run-status").className="small stale";}
      else{$("run-id").value="";updateRunLinks();$("workflow-run-status").className="small error";$("workflow-run-status").textContent=body.detail||"Run lookup failed.";}
    }catch(error){if(selectionVersion===requestVersion&&version===runRequestVersion){$("run-id").value="";updateRunLinks();$("workflow-run-status").className="small error";$("workflow-run-status").textContent=error.message;}}
    finally{if(selectionVersion===requestVersion&&version===runRequestVersion)$("resolve-run").disabled=false;}
  }

  async function sendWorkflowSignal(control,card,button){
    if(!result||!resolvedRunId||$("run-id").value.trim()!==resolvedRunId)return;
    let argumentValues;
    try{argumentValues=signalArguments(control,card);}
    catch(error){$("signal-status").className="small error";$("signal-status").textContent=error.message;return;}
    const confirmation=card.querySelector(".signal-confirmation");if(confirmation)confirmation.hidden=true;
    const selectionVersion=requestVersion,runVersion=runRequestVersion,version=++signalRequestVersion;
    const original=button.dataset.defaultLabel;
    setSignalControlsDisabled(true);
    $("signal-status").className="small";$("signal-status").textContent=`Sending ${control.label} through Cadence-Web…`;
    try{
      const payload={...Object.fromEntries(query()),signal_id:control.id,
        signal_metadata_revision:result.signal_metadata_revision,
        arguments:argumentValues,run_id:resolvedRunId};
      const response=await fetch("/api/workflow-signal",{method:"POST",headers:{"Content-Type":"application/json","Origin":location.origin},body:JSON.stringify(payload)});
      const body=await response.json();if(selectionVersion!==requestVersion||runVersion!==runRequestVersion||version!==signalRequestVersion)return;
      if(!response.ok)throw Error(body.error||body.detail||"Signal request failed.");
      const accepted=body.state==="accepted";
      button.textContent=accepted?"Signal sent ✓":"Send failed";button.classList.add(accepted?"copied":"failed");
      $("signal-status").className=accepted?"small ready":"small error";$("signal-status").textContent=body.detail||"Signal request failed.";
      if(accepted)for(const field of card.querySelectorAll("[data-signal-argument]"))field.value="";
      if(body.state==="stale run"||body.state==="no execution"){resolvedRunId="";$("workflow-signals").hidden=true;$("workflow-run-status").className="small error";$("workflow-run-status").textContent=body.detail;}
      setTimeout(()=>{if(selectionVersion===requestVersion&&runVersion===runRequestVersion){button.textContent=original;button.classList.remove("copied","failed");}},2000);
    }catch(error){
      if(selectionVersion===requestVersion&&runVersion===runRequestVersion&&version===signalRequestVersion){button.textContent="Send failed";button.classList.add("failed");$("signal-status").className="small error";$("signal-status").textContent=error.message;setTimeout(()=>{if(selectionVersion===requestVersion&&runVersion===runRequestVersion){button.textContent=original;button.classList.remove("failed");}},2000);}
    }finally{if(selectionVersion===requestVersion&&runVersion===runRequestVersion&&version===signalRequestVersion&&resolvedRunId)setSignalControlsDisabled(false);}
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
  $("refresh").addEventListener("click",()=>refresh(true));$("prepare").addEventListener("click",prepareRecipe);$("warmup").addEventListener("click",warmup);
  $("resolve-run").addEventListener("click",resolveWorkflowRun);$("run-id").addEventListener("input",()=>{runRequestVersion++;$("resolve-run").disabled=!$("web-workflow-id").value.trim();updateRunLinks("entered");});
  $("web-workflow-id").addEventListener("input",()=>{if($("web-workflow-id").readOnly)return;runRequestVersion++;$("run-id").value="";$("resolve-run").disabled=!$("web-workflow-id").value.trim();updateRunLinks("entered");});
  document.querySelectorAll("[data-copy]").forEach(button=>button.addEventListener("click",()=>{if(result)copyText(result[button.dataset.copy],button);}));
  document.querySelectorAll("[data-terminal]").forEach(button=>button.addEventListener("click",()=>openTerminal(button.dataset.terminal,button)));
  refresh(false);
})();
