"""Bloquea datos operativos y secretos reconocibles en el indice o commit Git.

Solo informa ruta y motivo, nunca el valor encontrado. No reescribe historial.
"""
import argparse
import ast
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


CARPETAS_PRIVADAS = set('input inputs output outputs tmp temp temporales logs reportes procesados '
    'descargas capturas exports exportaciones historico historial media uploads downloads backups '
    'entregables credentials credenciales secrets secretos .secrets .credentials .aws .azure .ssh '
    '.runtime .venv venv env __pycache__ node_modules .codex .agents .idea .vscode '
    'playwright-report test-results profiles browser_profiles user_data sessions sesiones'.split())
EXTENSIONES_PRIVADAS = {'.xls', '.xlsx', '.xlsm', '.xlsb', '.csv', '.tsv', '.ods', '.pdf',
    '.doc', '.docx', '.ppt', '.pptx', '.eml', '.msg', '.db', '.sqlite', '.sqlite3', '.dump', '.parquet',
    '.log', '.zip', '.7z', '.rar', '.tar', '.gz', '.bak', '.backup', '.old', '.tmp', '.part',
    '.pem', '.key', '.pfx', '.p12', '.jks', '.keystore', '.kdbx', '.png', '.jpg', '.jpeg', '.gif',
    '.bmp', '.tif', '.tiff', '.webp', '.svg', '.ico', '.mp4', '.mov', '.avi', '.mkv', '.webm',
    '.mp3', '.wav', '.m4a'}
PATRONES_NOMBRE = ('credentials*.json', 'credentials*.y*ml', 'credenciales*.json',
    'credenciales*.y*ml', 'credenciales*.txt', 'usuarios*.json', 'usuarios*.txt', 'users*.json',
    'users*.txt', 'token*.json', 'secrets*.json', 'service-account*.json', 'service_account*.json',
    'id_rsa*', 'id_ed25519*', '*.local.json', '*.local.ini', '*.local.yaml', '*.local.yml',
    '*.env', '*.env.*', '*.db-*', '*.sqlite-*', '*.sqlite3-*', '*.log.*')
NOMBRES_PRIVADOS = {'.envrc', '.netrc', '_netrc', '.git-credentials', '.pypirc', '.npmrc'}
CLAVE_SENSIBLE = re.compile(r'(?:PASSWORD|PWD|TOKEN|API_KEY|API_TOKEN|EMAIL|USER|USERNAME|REMITENTE|DESTINATARIOS|LISTA_DISTRIBUCION_(?:CORREO|ERROR))$', re.I)
MARCADORES = [
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{40,}\b'),
    re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    re.compile(r'\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b'),
]


def plantilla(nombre):
    return nombre == '.env.example' or fnmatch.fnmatchcase(nombre, '.env.*.example')


def leer_variables(texto):
    for linea in texto.splitlines():
        resultado = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', linea)
        if resultado:
            clave, valor = resultado.groups()
            if valor.startswith(('"', "'")):
                try:
                    valor = ast.literal_eval(valor)
                except (ValueError, SyntaxError):
                    valor = valor.strip('"\'')
            else:
                valor = valor.split(' #', 1)[0].strip()
            yield clave, str(valor)


def evaluar_archivo(ruta, contenido, secretos=()):
    partes = PurePosixPath(ruta).parts
    nombre = partes[-1].casefold()
    es_plantilla = plantilla(nombre)
    if nombre == '.gitkeep' and contenido == b'':
        return []
    if (any(p.casefold() in CARPETAS_PRIVADAS for p in partes[:-1])
            or nombre in NOMBRES_PRIVADOS
            or nombre.startswith('.env') and not es_plantilla
            or any(fnmatch.fnmatchcase(nombre, p) for p in PATRONES_NOMBRE) and not es_plantilla
            or PurePosixPath(nombre).suffix in EXTENSIONES_PRIVADAS):
        return ['archivo privado, operativo o generado']
    if len(contenido) > 1024 * 1024:
        return ['archivo mayor a 1 MiB; revisar antes de versionar']
    try:
        texto = contenido.decode('utf-8-sig')
    except UnicodeError:
        return ['archivo binario no revisado']
    motivos = []
    if any(p.search(texto) for p in MARCADORES):
        motivos.append('posible token o clave privada reconocible')
    if any(valor in texto for valor in secretos):
        motivos.append('coincide con una credencial privada local')
    if es_plantilla:
        for clave, valor in leer_variables(texto):
            if clave.startswith('SECRET_') or not CLAVE_SENSIBLE.search(clave) or not valor:
                continue
            # Las plantillas comparten nombres y referencias, nunca valores de acceso.
            motivos.append('plantilla con campo de credencial no vacio: ' + clave)
    return list(dict.fromkeys(motivos))


def secretos_locales(raiz):
    variables = list(os.environ.items())
    privado = raiz / '.env'
    if privado.is_file():
        variables.extend(leer_variables(privado.read_text(encoding='utf-8-sig')))
    return tuple({str(v) for k, v in variables if not k.startswith('SECRET_')
        and CLAVE_SENSIBLE.search(k) and len(str(v)) >= 6
        and str(v).casefold() not in ('true', 'false')})


def git(raiz, *args):
    return subprocess.run(['git', '-C', str(raiz), *args], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout


def revisar(raiz, revision=None):
    secretos = secretos_locales(raiz)
    if revision is None:
        rutas = git(raiz, 'ls-files', '-z').split(b'\0')
        prefijo = ':'
    else:
        # Resolver a hash antes de construir referencias de objetos Git.
        identificador = git(raiz, 'rev-parse', '--verify', revision + '^{commit}').decode().strip()
        if not re.fullmatch(r'[0-9a-f]{40,64}', identificador):
            raise ValueError('Referencia Git invalida')
        rutas = git(raiz, 'ls-tree', '-r', '--name-only', '-z', identificador).split(b'\0')
        prefijo = identificador + ':'
    errores = []
    for entrada in rutas:
        if not entrada:
            continue
        ruta = entrada.decode('utf-8')
        contenido = git(raiz, 'show', prefijo + ruta)
        errores.extend((ruta, motivo) for motivo in evaluar_archivo(ruta, contenido, secretos))
    return errores


def main(argv=None):
    parser = argparse.ArgumentParser(description='Verificar el contenido que se va a publicar en Git')
    parser.add_argument('--revision', help='Commit a revisar; por defecto se revisa el indice preparado')
    args = parser.parse_args(argv)
    raiz = Path(__file__).resolve().parents[1]
    try:
        errores = revisar(raiz, args.revision)
    except Exception:
        print('ERROR: no se pudo completar la comprobacion; publicacion bloqueada.')
        return 1
    if errores:
        for ruta, motivo in errores:
            print('BLOQUEADO:', ruta, '-', motivo)
        return 1
    print('Contenido Git verificado: sin datos operativos ni secretos reconocidos.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
