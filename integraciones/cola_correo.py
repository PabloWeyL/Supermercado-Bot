"""Outbox SQLite: mensaje inmutable, estado por destinatario y recuperacion conservadora."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from integraciones.weyland.archivos import bloqueo
from integraciones.trazabilidad import contexto


class ErrorEntrega(RuntimeError):
    def __init__(self, codigo, *, incierto=False, permanente=False):
        self.codigo, self.incierto, self.permanente = codigo, incierto, permanente
        super().__init__(codigo)


class ColaCorreo:
    def __init__(self, carpeta, max_intentos=5, espera=60):
        self.carpeta = Path(carpeta).resolve()
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.ruta = self.carpeta / 'correos.db'
        self.max_intentos, self.espera = max_intentos, espera
        if not 1 <= max_intentos <= 20 or not 1 <= espera <= 86400:
            raise ValueError('Limites de reintento de correo invalidos')
        with self.conexion() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS mensajes (
                    id TEXT PRIMARY KEY, creado REAL NOT NULL, remitente TEXT NOT NULL,
                    contexto TEXT NOT NULL, mime BLOB NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS destinatarios (
                    mensaje_id TEXT NOT NULL REFERENCES mensajes(id), correo TEXT NOT NULL,
                    estado TEXT NOT NULL CHECK(estado IN ('PENDIENTE','ENVIANDO','ENVIADO','INCIERTO','AGOTADO')),
                    intentos INTEGER NOT NULL DEFAULT 0, proximo REAL NOT NULL DEFAULT 0, codigo TEXT,
                    PRIMARY KEY(mensaje_id, correo));
                CREATE INDEX IF NOT EXISTS idx_correo_pendiente ON destinatarios(estado, proximo);
            ''')

    @contextmanager
    def conexion(self):
        con = sqlite3.connect(self.ruta, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    def registrar(self, identificador, remitente, destinatarios, mime, datos_contexto):
        if not 0 < len(mime) <= 30 * 1024 * 1024:
            raise ValueError('El correo vacio o mayor que 30 MiB no puede encolarse')
        with self.conexion() as con:
            con.execute('INSERT OR IGNORE INTO mensajes VALUES (?,?,?,?,?,?)',
                (identificador, time.time(), remitente, json.dumps(datos_contexto), mime, hashlib.sha256(mime).hexdigest()))
            con.executemany("INSERT OR IGNORE INTO destinatarios(mensaje_id,correo,estado) VALUES (?,?,'PENDIENTE')",
                            [(identificador, correo) for correo in destinatarios])
        return identificador

    def entregado(self, identificador):
        with self.conexion() as con:
            filas = con.execute('SELECT estado FROM destinatarios WHERE mensaje_id=?', (identificador,)).fetchall()
        return bool(filas) and all(f['estado'] == 'ENVIADO' for f in filas)

    def procesar(self, enviar, *, solo=None, ahora=None):
        ahora = time.time() if ahora is None else ahora
        with bloqueo(self.carpeta / 'envio.lock'):
            with self.conexion() as con:
                # No hay otro emisor activo bajo este lock. ENVIANDO es una caida previa.
                con.execute("UPDATE destinatarios SET estado='INCIERTO',codigo='INTERRUPCION' WHERE estado='ENVIANDO'")
                sql = "SELECT DISTINCT mensaje_id FROM destinatarios WHERE estado='PENDIENTE' AND proximo<=?"
                args = [ahora]
                if solo is not None:
                    sql += ' AND mensaje_id=?'
                    args.append(solo)
                ids = [f[0] for f in con.execute(sql + ' ORDER BY mensaje_id LIMIT 20', args)]
            for identificador in ids:
                with self.conexion() as con:
                    mensaje = dict(con.execute('SELECT * FROM mensajes WHERE id=?', (identificador,)).fetchone())
                    filas = con.execute("SELECT correo,intentos FROM destinatarios WHERE mensaje_id=? AND estado='PENDIENTE' AND proximo<=?",
                                        (identificador, ahora)).fetchall()
                    destinatarios = [f['correo'] for f in filas]
                    con.executemany("UPDATE destinatarios SET estado='ENVIANDO',intentos=intentos+1 WHERE mensaje_id=? AND correo=?",
                                    [(identificador, correo) for correo in destinatarios])
                try:
                    if hashlib.sha256(mensaje['mime']).hexdigest() != mensaje['sha256']:
                        raise ErrorEntrega('CONTENIDO_ALTERADO', permanente=True)
                    with contexto(json.loads(mensaje['contexto'])):
                        rechazados = enviar(mensaje['remitente'], destinatarios, mensaje['mime'])
                    if not isinstance(rechazados, dict) or not set(rechazados) <= set(destinatarios):
                        raise ErrorEntrega('RESPUESTA_SMTP_INCOMPATIBLE', incierto=True)
                    estados = {}
                    for fila in filas:
                        correo = fila['correo']
                        if correo not in rechazados:
                            estados[correo] = ('ENVIADO', 0, 'ACEPTADO_SMTP')
                        else:
                            codigo = int(rechazados[correo][0])
                            agotado = codigo >= 500 or fila['intentos'] + 1 >= self.max_intentos
                            estados[correo] = ('AGOTADO' if agotado else 'PENDIENTE',
                                ahora + min(86400, self.espera * (2 ** fila['intentos'])), f'SMTP_{codigo}')
                except Exception as error:
                    if not isinstance(error, ErrorEntrega):
                        error = ErrorEntrega('RESULTADO_DESCONOCIDO', incierto=True)
                    estados = {}
                    for fila in filas:
                        agotado = error.permanente or fila['intentos'] + 1 >= self.max_intentos
                        estado = 'INCIERTO' if error.incierto else ('AGOTADO' if agotado else 'PENDIENTE')
                        estados[fila['correo']] = (estado, ahora + min(86400, self.espera * (2 ** fila['intentos'])), error.codigo)
                with self.conexion() as con:
                    con.executemany('UPDATE destinatarios SET estado=?,proximo=?,codigo=? WHERE mensaje_id=? AND correo=?',
                        [(estado, proximo, codigo, identificador, correo)
                         for correo, (estado, proximo, codigo) in estados.items()])
        return self.resumen(solo=solo)

    def resumen(self, *, solo=None):
        with self.conexion() as con:
            sql = 'SELECT estado,COUNT(*) AS cantidad FROM destinatarios'
            args = ()
            if solo is not None:
                sql += ' WHERE mensaje_id=?'
                args = (solo,)
            return {f['estado']: f['cantidad'] for f in con.execute(sql + ' GROUP BY estado', args)}

    def listar(self):
        with self.conexion() as con:
            return [dict(f) for f in con.execute('''SELECT mensaje_id,correo,estado,intentos,proximo,codigo
                FROM destinatarios ORDER BY mensaje_id,correo''')]

    def reactivar(self, identificador):
        with bloqueo(self.carpeta / 'envio.lock'), self.conexion() as con:
            con.execute("UPDATE destinatarios SET estado='PENDIENTE',intentos=0,proximo=0 WHERE mensaje_id=? AND estado='AGOTADO'", (identificador,))

    def resolver_incierto(self, identificador, entregado, destinatario=None):
        with bloqueo(self.carpeta / 'envio.lock'), self.conexion() as con:
            sql = "UPDATE destinatarios SET estado=?,intentos=0,proximo=0,codigo='RESUELTO_OPERADOR' WHERE mensaje_id=? AND estado='INCIERTO'"
            args = ['ENVIADO' if entregado else 'PENDIENTE', identificador]
            if destinatario is not None:
                sql += ' AND correo=?'
                args.append(destinatario.casefold())
            return con.execute(sql, args).rowcount
