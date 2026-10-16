"use strict";
(() => {
  const key="cadence-explorer-theme",valid=value=>value==="dark"||value==="light";
  const apply=(value,persist=false)=>{
    const theme=valid(value)?value:"dark";
    document.documentElement.dataset.theme=theme;
    if(persist)try{localStorage.setItem(key,theme);}catch{}
    return theme;
  };
  let saved;
  try{saved=localStorage.getItem(key);}catch{}
  window.ExplorerTheme={key,valid,apply,
    current:()=>valid(document.documentElement.dataset.theme)?document.documentElement.dataset.theme:"dark"};
  apply(saved);
})();
