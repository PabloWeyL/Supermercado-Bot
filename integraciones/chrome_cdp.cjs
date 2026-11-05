// Puente JSON por stdin/stdout a Chrome. Requiere Node.js 22 o posterior.
const readline = require('node:readline');
const socket = new WebSocket(process.argv[2]);
const timeout = Number(process.argv[3] || 20000);
const pending = new Map();
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
const connectionTimer = setTimeout(() => { emit({bridgeError: 'Timeout al conectar con Chrome'}); socket.close(); }, timeout);
socket.onopen = () => { clearTimeout(connectionTimer); emit({ready: true}); };
socket.onerror = () => { clearTimeout(connectionTimer); emit({bridgeError: 'No se pudo conectar con Chrome'}); };
socket.onmessage = event => {
  const result = JSON.parse(event.data);
  if (pending.has(result.id)) {
    clearTimeout(pending.get(result.id));
    pending.delete(result.id);
    emit(result);
  }
};
socket.onclose = () => {
  for (const [id, timer] of pending) { clearTimeout(timer); emit({id, error: {message: 'Chrome cerro la conexion'}}); }
  pending.clear();
  process.exit(0);
};
const input = readline.createInterface({input: process.stdin});
input.on('line', line => {
  try {
    const request = JSON.parse(line);
    const timer = setTimeout(() => {
      pending.delete(request.id);
      emit({id: request.id, error: {message: 'Timeout: ' + request.method}});
    }, timeout);
    pending.set(request.id, timer);
    socket.send(JSON.stringify(request));
  } catch (error) { emit({bridgeError: error.message}); }
});
input.on('close', () => socket.close());
