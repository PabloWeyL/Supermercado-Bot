"""Archivos operativos privados: escritura atomica, bloqueo y rutas confinadas."""
from contextlib import contextmanager
import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from .errores import ErrorWeyland


def reemplazar_atomico(parcial, destino):
    """Tolera bloqueos transitorios de Windows sin repetir HTTP ni el negocio."""
    for intento in range(5):
        try:
            parcial.replace(destino)
            return
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or intento == 4:
                raise
            time.sleep(0.05 * (2 ** intento))


def guardar_json(ruta, datos):
    parcial = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=ruta.parent,
                                prefix='estado_', suffix='.tmp', delete=False) as archivo:
            parcial = Path(archivo.name)
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.flush()
            os.fsync(archivo.fileno())
        reemplazar_atomico(parcial, ruta)
    finally:
        if parcial is not None:
            parcial.unlink(missing_ok=True)


def sha256(ruta):
    with Path(ruta).open('rb') as archivo:
        return hashlib.file_digest(archivo, 'sha256').hexdigest()


def confinar(raiz, ruta):
    raiz = Path(raiz).resolve()
    ruta = Path(ruta)
    if ruta.is_absolute() or '..' in ruta.parts:
        raise ErrorWeyland('Ruta de artefacto invalida')
    destino = (raiz / ruta).resolve()
    if destino == raiz or not destino.is_relative_to(raiz):
        raise ErrorWeyland('Artefacto fuera del directorio operativo')
    return destino


@contextmanager
def bloqueo(ruta):
    with ruta.open('a+b') as archivo:
        if archivo.tell() == 0:
            archivo.write(b'0')
            archivo.flush()
        archivo.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ErrorWeyland('Ya hay un worker local activo') from None
        try:
            yield
        finally:
            archivo.seek(0)
            if os.name == 'nt':
                msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
