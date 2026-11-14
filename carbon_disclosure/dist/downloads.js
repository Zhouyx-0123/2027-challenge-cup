(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.LedgerDownloads=factory();
})(typeof window!=='undefined'?window:this,function(){
  'use strict';
  function save(bytes,type,filename,view){
    let host=view||(typeof window!=='undefined'?window:null);
    if(!host)throw new Error('Download requires a browser window');
    // Streamlit uses a trusted same-origin srcdoc. Owning the Blob and anchor
    // in the surrounding document also supports desktop browser download UI.
    try{if(host.parent&&host.parent!==host&&host.parent.document)host=host.parent;}catch{}
    const url=host.URL.createObjectURL(new host.Blob([bytes],{type}));
    const link=host.document.createElement('a');
    link.href=url;link.download=filename;link.hidden=true;
    host.document.body.append(link);
    try{link.click();}finally{link.remove();host.setTimeout(()=>host.URL.revokeObjectURL(url),30000);}
  }
  return{save};
});
