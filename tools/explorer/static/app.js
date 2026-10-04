"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let state, result, recipe, variant, requestVersion = 0;
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
    for(const id of ["result","resolved-panel","evidence-panel","local-panel"])$(id).hidden=true;
    for(const id of ["setup-command","worker-command","client-command"])$(id).textContent="";
    $("selection-status").textContent=message;$("selection-status").className=isError?"notice error":"notice";
    $("copy-status").textContent="";
  }
  function field(id,show){$(id+"-field").hidden=!show;}
  function flow() {
    clear("Complete the selections to generate matching commands.");
    recipe=state?.recipes.find(r=>r.id===$("sample").value);variant=null;
    for(const id of ["language","framework","mode","model","classifier"])field(id,false);
    $("batch-fields").hidden=true;$("readme").hidden=!recipe?.readme;$("implementation").textContent="";
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
    resolve();
  }
  async function resolve() {
    const version=++requestVersion;
    $("selection-status").textContent="Reading the selected recipe and validating its options…";
    const query=new URLSearchParams({sample:recipe.id,implementation:variant.id,mode:variant.modes.length?$("mode").value:"",model:$("model").value,classifier:$("classifier").value,count:$("count").value,concurrency:$("concurrency").value});
    try{const response=await fetch("/api/resolve?"+query,{cache:"no-store"});const body=await response.json();if(version!==requestVersion)return;if(!response.ok)throw Error(body.error||"Could not resolve this selection.");result=body;render();}
    catch(error){if(version===requestVersion)clear(error.message,true);}
  }
  function configCard(title,config){const card=node("article");card.append(node("h4",title));for(const [label,value] of Object.entries(config)){const line=node("div");line.append(node("span",label),node("code",value));card.append(line);}return card;}
  function render(){
    $("result").hidden=false;$("resolved-panel").hidden=false;$("evidence-panel").hidden=false;
    $("selection-status").className="notice ready";$("selection-status").textContent=result.live?"Implemented live selection · credentials and services stay in your local Worker.":result.mock?"Mock selection · no provider calls.":"Commands resolved from this implementation. Check its README for runtime prerequisites.";
    for(const kind of ["setup","worker"])$(kind+"-command").textContent=result[kind];
    $("client-command").textContent=result.start;$("client-block").hidden=!result.start;
    $("controls-block").hidden=!result.controls.length;$("control-commands").replaceChildren();
    for(const control of result.controls){const head=node("div");head.className="code-head";head.append(node("span",control.name));const button=node("button","Copy "+control.name);button.type="button";button.addEventListener("click",()=>copyText(control.command));head.append(button);const pre=node("pre");pre.append(node("code",control.command));$("control-commands").append(head,pre);}
    $("credentials").textContent=result.credentials.length?"Worker environment variables: "+result.credentials.join(", ")+". Set their values locally before starting the Worker.":result.live?"This selected local inference combination needs no AI API keys.":"Mock mode needs no AI API keys.";
    $("resolution").replaceChildren(configCard("Implementation",{directory:result.implementation,mode:result.mode||"not applicable"}));
    if(result.model)$("resolution").append(configCard("Model",result.model));if(result.classifier)$("resolution").append(configCard("Classifier",result.classifier));
    $("binding").textContent=`Domain: ${result.domain} · Matching task list: ${result.task_list}`+(result.workflow_id?` · Workflow ID: ${result.workflow_id}`:"");
    $("evidence-title").textContent=result.evidence?"Recorded live evidence":"Execution evidence";$("evidence-note").textContent=result.note;
    $("evidence-ids").replaceChildren();$("evidence-link").hidden=!result.evidence;
    if(result.evidence){for(const [label,value] of [["Recorded Workflow ID",result.evidence.workflow_id],["Recorded Run ID",result.evidence.run_id],["Recorded model",result.evidence.model_description]]){$("evidence-ids").append(node("dt",label),node("dd",value));}$("evidence-link").href=result.evidence.source;}
    $("local-panel").hidden=!result.local.length;$("local-commands").replaceChildren();
    for(const service of result.local){const article=node("article");article.append(node("h4",service.service));for(const kind of ["health","warmup"]){const head=node("div");head.className="code-head";head.append(node("span",kind==="health"?"Readiness check":"Warm up"));const button=node("button","Copy");button.type="button";button.addEventListener("click",()=>copyText(service[kind]));head.append(button);const pre=node("pre");pre.append(node("code",service[kind]));article.append(head,pre);}$("local-commands").append(article);}
    $("read-at").textContent="Recipe files read at "+new Date(result.read_at).toLocaleTimeString()+" · Refresh rereads the current checkout.";
  }
  async function copyText(text){try{await navigator.clipboard.writeText(text);$("copy-status").textContent="Copied.";}catch{$("copy-status").textContent="Clipboard unavailable. Select the command text and copy it manually.";}}
  async function refresh(){
    clear("Reading recipes…");$("refresh").disabled=true;$("load-status").textContent="Reading the current checkout…";
    try{const response=await fetch("/api/state",{cache:"no-store"});if(!response.ok)throw Error("Could not read recipes.");state=await response.json();options("sample",state.recipes.map(r=>({id:r.id,label:r.title})),{placeholder:"Choose a sample"});$("load-status").textContent=`${state.recipes.length} samples in this checkout`;flow();}
    catch(error){state=null;for(const id of ["sample","language","framework","mode","model","classifier"])$(id).replaceChildren();$("load-status").textContent=error.message;clear("Refresh to retry reading this checkout.",true);}
    finally{$("refresh").disabled=false;}
  }
  $("sample").addEventListener("change",()=>{$("language").value="";$("framework").value="";$("mode").value="";flow();});
  $("language").addEventListener("change",()=>{$("framework").value="";$("mode").value="";flow();});
  $("framework").addEventListener("change",()=>{$("mode").value="";flow();});
  for(const id of ["mode","model","classifier"])$(id).addEventListener("change",flow);
  for(const id of ["count","concurrency"])$(id).addEventListener("input",flow);
  $("refresh").addEventListener("click",refresh);
  document.querySelectorAll("[data-copy]").forEach(button=>button.addEventListener("click",()=>{if(result)copyText(result[button.dataset.copy]);}));
  refresh();
})();
