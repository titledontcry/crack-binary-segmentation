// No browser package required: use Chrome's DevTools protocol and Node's WebSocket.
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const temp=await mkdtemp(join(tmpdir(),'crack-ui-'));
const python=spawn('.venv/bin/python',['drone_inference/tests/preview_server.py'],{stdio:['ignore','pipe','pipe']});
let serverLog='';python.stderr.on('data',data=>serverLog+=data);
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=9227',`--user-data-dir=${temp}/chrome`,'about:blank'],{stdio:'ignore'});
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(fn,label,timeout=25000){const start=Date.now();while(Date.now()-start<timeout){try{if(await fn())return}catch{}await pause(150)}throw new Error('Timed out: '+label)}
let socket, diagnostic;
try{
 await waitFor(async()=>(await fetch('http://127.0.0.1:8766/api/health')).ok,'preview server');
 let tabs;await waitFor(async()=>{tabs=await(await fetch('http://127.0.0.1:9227/json')).json();return tabs.some(tab=>tab.type==='page'&&tab.url==='about:blank')},'Chrome');
 socket=new WebSocket(tabs.find(tab=>tab.type==='page'&&tab.url==='about:blank').webSocketDebuggerUrl);await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
 let id=0;const pending=new Map(),errors=[];
 socket.addEventListener('message',event=>{const message=JSON.parse(event.data);if(message.id){const p=pending.get(message.id);pending.delete(message.id);message.error?p.reject(new Error(JSON.stringify(message.error))):p.resolve(message.result)}else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails)});
 const cdp=(method,params={})=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});socket.send(JSON.stringify({id:key,method,params}))});
 const evaluate=async expression=>{const result=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(JSON.stringify(result.exceptionDetails));return result.result.value};
 diagnostic=evaluate;
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await cdp('Page.navigate',{url:'http://127.0.0.1:8766/projects'});
 await waitFor(()=>evaluate("document.querySelectorAll('.project-card').length>0"),'project cards');
 await evaluate("navigate('/projects/legacy/overview?session=20261010_134920')");
 await waitFor(()=>evaluate("state.detail?.summary.total===232 && !!document.querySelector('#hero-img')"),'232-image overview');
 await waitFor(()=>evaluate("document.querySelector('#hero-img').complete"),'overview image');
 await writeFile(join(temp,'overview.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
 await evaluate("document.querySelector('#hero-review').click()");
 await waitFor(()=>evaluate('editor.ready'),'image editor');
 await evaluate("document.querySelector('#reviewer').value='Browser smoke reviewer';document.querySelector('[data-tool=brush]').click()");
 const point=await evaluate("(()=>{const r=document.querySelector('#image-canvas').getBoundingClientRect();return {x:r.left+r.width*.5,y:r.top+r.height*.5}})()");
 await cdp('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1});
 await cdp('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x+25,y:point.y+10,button:'left',buttons:1});
 await cdp('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x+25,y:point.y+10,button:'left',clickCount:1});
 if(!await evaluate("document.querySelector('#decision').value==='corrected'"))throw new Error('Brush did not select corrected decision');
 const imageId=await evaluate('editor.row.id');
 await evaluate("document.querySelector('#save-review').click()");
 await waitFor(()=>evaluate("editor.ready && !editor.saving && document.querySelector('#editor-message').textContent==='Saved successfully.'"),'saved correction',40000);
 await writeFile(join(temp,'editor.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
 await evaluate("document.querySelector('#editor-close').click()");
 await evaluate("navigate('/projects/legacy/review?session=20261010_134920')");
 await waitFor(()=>evaluate("!!document.querySelector('[data-filter=reviewed]')"),'review tabs');
 await evaluate("document.querySelector('[data-filter=reviewed]').click()");
 await waitFor(()=>evaluate(`state.items.some(r=>r.id===${JSON.stringify(imageId)}&&r.review_status==='corrected')`),'saved image in Human Review');
 await evaluate("navigate('/projects/legacy/reports?session=20261010_134920')");
 await waitFor(()=>evaluate("document.querySelectorAll('.export-link').length===3"),'export buttons');
 const report=await evaluate("fetch(document.querySelector('[data-format=csv]').href).then(r=>r.text())");
 if(!report.includes(imageId)||!report.includes('Browser smoke reviewer'))throw new Error('CSV does not include saved review');
 const thai=await evaluate("/[\\u0E00-\\u0E7F]/.test(document.body.innerText)");if(thai)throw new Error('Thai UI text found');
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 await evaluate("navigate('/projects/legacy/images?session=20261010_134920')");
 await waitFor(()=>evaluate("document.querySelectorAll('.image-card').length===24"),'mobile image grid');
 if(await evaluate('document.documentElement.scrollWidth>window.innerWidth+2'))throw new Error('Mobile horizontal overflow');
 await writeFile(join(temp,'mobile.png'),Buffer.from((await cdp('Page.captureScreenshot')).data,'base64'));
 if(errors.length)throw new Error('Browser errors: '+JSON.stringify(errors));
 console.log(JSON.stringify({result:'PASS',checks:['232-image scope','mask correction','review persistence','CSV consistency','English UI','mobile layout'],screenshots:temp}));
}catch(error){console.error(error);if(diagnostic)console.error('Page:',await diagnostic("({url:location.href,title:document.title,body:document.body?.innerText.slice(0,1000)})").catch(e=>String(e)));console.error(serverLog.slice(-2500));process.exitCode=1}
finally{socket?.close();chrome.kill('SIGTERM');python.kill('SIGTERM')}
