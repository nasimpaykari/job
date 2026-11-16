const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve(__dirname,'../browser_saver'),d=new JSDOM(fs.readFileSync(path.join(root,'popup.html'),'utf8'),{url:'https://extension.example/popup.html',runScripts:'outside-only'}),w=d.window;
 let copied='',opened='',stored=null,options=null;const capture={format:'job-coach-capture',version:1,title:'Faculty',institution:'Example',url:'https://example.edu/job',description:'Job text',method:'structured'};
 w.chrome={storage:{local:{get:async()=>({appUrl:'http://localhost:5050'}),set:async v=>{stored=v}}},tabs:{query:async()=>[{id:5,url:capture.url}],create:async v=>{opened=v.url}},scripting:{executeScript:async v=>{options=v;return [{result:capture}]}}};
 Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{copied=value}}});w.eval(fs.readFileSync(path.join(root,'extract.js'),'utf8'));w.eval(fs.readFileSync(path.join(root,'popup.js'),'utf8'));
 const $=id=>w.document.getElementById(id),wait=async fn=>{for(let i=0;i<100;i++){if(fn())return;await new Promise(r=>setTimeout(r,10))}throw Error('Popup timeout')};
 await wait(()=>!$('copyOpen').disabled);assert.equal(options.target.tabId,5);assert.equal($('title').value,'Faculty');$('institution').value='Reviewed employer';$('copyOpen').click();await wait(()=>opened);assert.equal(opened,'http://localhost:5050/#browser-import');assert.equal(JSON.parse(copied).institution,'Reviewed employer');assert.equal(stored.appUrl,'http://localhost:5050');
 opened='';$('appUrl').value='https://outside.example';$('copyOpen').click();await wait(()=>$('message').textContent.includes('local app address'));assert.equal(opened,'');
 $('appUrl').value='http://127.0.0.1:5050/?token=secret';$('copyOpen').click();await wait(()=>$('message').textContent.includes('no login token'));assert.equal(opened,'');d.window.close();console.log('Extension popup passed: active-tab capture, reviewed field copy, local app open, stored address, remote/token address rejection.');
})().catch(e=>{console.error(e);process.exit(1)});
