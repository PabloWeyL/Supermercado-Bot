// Ejecuta los extractores reales en Chrome, con un perfil temporal aislado.
// Entrada JSON por stdin: [{html, script}] o [{url, script}]. No usa TagUI ni correo.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

async function main() {
  const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
  const executable = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const endpoint = process.env.CDP_ENDPOINT;
  const profile = endpoint ? null : fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-dom-test-'));
  const chrome = endpoint ? null : spawn(executable, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'
  ], { windowsHide: true, stdio: 'ignore' });
  let ws;
  let nextId = 0;
  const pending = new Map();
  let launchError;
  let ownedPage;
  if (chrome) chrome.on('error', error => { launchError = error; });
  try {
    let page;
    if (endpoint) {
      ownedPage = await (await fetch(endpoint + '/json/new?about:blank', {method: 'PUT'})).json();
      page = ownedPage;
    } else {
      let port;
      for (let attempt = 0; attempt < 60; attempt++) {
        if (launchError) throw launchError;
        try { port = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; break; } catch {}
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      if (!port) throw Error('No se pudo iniciar Chrome. Configura CHROME_PATH.');
      const pages = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
      page = pages.find(item => item.type === 'page');
    }
    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = event => {
      const message = JSON.parse(event.data);
      if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
    };
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(Error('Timeout: ' + method)); }, 20000);
      pending.set(id, result => { clearTimeout(timer); result.error ? reject(Error(JSON.stringify(result.error))) : resolve(result.result); });
      ws.send(JSON.stringify({ id, method, params }));
    });
    const output = [];
    for (const item of cases) {
      if (item.url) {
        await call('Page.navigate', { url: item.url });
        await new Promise(resolve => setTimeout(resolve, 12000));
      } else {
        const { frameTree } = await call('Page.getFrameTree');
        await call('Page.setDocumentContent', {
          frameId: frameTree.frame.id,
          html: '<!doctype html><html><head><base href="https://example.test/"></head><body>' + item.html + '</body></html>'
        });
      }
      for (const action of item.actions || []) {
        await call(action.method, action.params);
      }
      if (item.wait_after_actions_ms) {
        await new Promise(resolve => setTimeout(resolve, item.wait_after_actions_ms));
      }
      const evaluation = await call('Runtime.evaluate', {
        returnByValue: true,
        awaitPromise: true,
        expression: 'JSON.stringify({url:location.href,title:document.title,result:(new Function(' + JSON.stringify(item.script) + '))()})'
      });
      if (evaluation.exceptionDetails) throw Error(JSON.stringify(evaluation.exceptionDetails));
      output.push(JSON.parse(evaluation.result.value));
    }
    process.stdout.write(JSON.stringify(output));
    if (chrome) await call('Browser.close');
  } finally {
    if (ws) ws.close();
    if (chrome) chrome.kill();
    if (ownedPage) await fetch(endpoint + '/json/close/' + ownedPage.id);
    await new Promise(resolve => setTimeout(resolve, 500));
    if (profile) try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
