"""Extractores DOM. Solo devuelven precios por unidad, sin beneficios condicionados."""


COMPROBAR_ACCESO_JS = r"""
    var textoPagina = document.body ? document.body.innerText : '';
    if (/robot or human|just a moment|un momento/i.test(document.title) ||
        document.querySelector('#challenge-running, #cf-challenge-running, #challenge-form') ||
        /verificaci[oó]n de seguridad en curso|verifying you are human|verify you are human|access denied|acceso denegado/i.test(textoPagina)) {
        return JSON.stringify({error: 'Acceso bloqueado por verificacion de seguridad del sitio'});
    }
    if (location.protocol === 'chrome-error:') {
        return JSON.stringify({error: 'No se pudo cargar la pagina del supermercado'});
    }
"""


EXTRACTOR_TOTTUS_JS = r"""
    var out = [];
    var cards = document.querySelectorAll(selectores.tarjeta);
    if (!cards.length) {
        cards = document.querySelectorAll(selectores.tarjeta_respaldo);
    }
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nombre = card.querySelector(selectores.nombre) || card.querySelector(selectores.nombre_respaldo);
        var imagen = card.querySelector(selectores.imagen);
        var textoNombre = nombre ? nombre.textContent.trim() :
            (card.getAttribute('title') || (imagen ? imagen.getAttribute('alt') : '')).trim();
        var link = card.matches('a[href]') ? card : card.querySelector(selectores.enlace);
        var idProducto = (card.id || '').slice(selectores.prefijo_tarjeta.length);
        var bloquePrecios = card.querySelector(selectores.bloque_precios) ||
            document.getElementById(selectores.prefijo_precios + idProducto) || card;
        var precio = bloquePrecios.querySelector(selectores.precio_publico);
        if (!precio) {
            var candidatos = bloquePrecios.querySelectorAll(selectores.precio);
            var precioNormal = null;
            for (var j = 0; j < candidatos.length; j++) {
                var candidato = candidatos[j];
                var contenido = candidato.textContent.trim();
                var filaPrecio = candidato.closest('li') || candidato.parentElement;
                var condicionado = /cmr|tarjeta|club|membres[ií]a|\d+\s*[x×]\s*\$/i.test(filaPrecio.textContent) ||
                    filaPrecio.querySelector('[data-cmr-price], .price-cmr, img[alt*="CMR"], img[src*="cmr"]') ||
                    candidato.matches('[data-cmr-price], .price-cmr');
                if (!/^\$\s*\d[\d.,]*(?:\s*c\s*\/\s*u)?\s*$/i.test(contenido) || condicionado) {
                    continue;
                }
                if (candidato.closest('s, del') ||
                    /line-through/.test(getComputedStyle(candidato).textDecorationLine) ||
                    /line-through/.test(getComputedStyle(filaPrecio).textDecorationLine)) {
                    precioNormal = precioNormal || candidato;
                    continue;
                }
                precio = candidato;
                break;
            }
            precio = precio || precioNormal;
        }
        if (!precio) {
            var principal = card.querySelector(selectores.precio_principal);
            if (principal && !principal.matches('[data-cmr-price], .price-cmr') &&
                !principal.querySelector('[data-cmr-price], .price-cmr') &&
                !/cmr|tarjeta|club|\d+\s*[x×]\s*\$/i.test(principal.textContent)) {
                precio = principal;
            }
        }
        if (!precio) {
            precio = card.querySelector(selectores.precio_normal);
        }
        var texto = precio ? precio.textContent.trim() : '';
        if (!textoNombre || !link || !/\$\s*\d/.test(texto) ||
            /cmr|tarjeta|club|\d+\s*[x×]\s*\$/i.test(texto)) {
            continue;
        }
        out.push({ok: true, nombre: textoNombre, precio: texto, url: link.href});
        var marca = selectores.marca ? card.querySelector(selectores.marca) : null;
        if (marca) { out[out.length - 1].marca = marca.textContent.trim(); }
    }
    return JSON.stringify(out);
"""


EXTRACTOR_UNIMARC_JS = r"""
    var out = [];
    var cards = document.querySelectorAll(selectores.tarjeta);
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nombre = card.querySelector(selectores.nombre);
        var link = card.querySelector(selectores.enlace);
        var oferta = card.querySelector(selectores.precio_oferta);
        var normal = card.querySelector(selectores.precio_normal);
        var textoCard = card.innerText || card.textContent;
        var condicionado = /club\s+unimarc|tarjeta|membres[ií]a|\d+\s*[x×]\s*\$/i.test(textoCard);
        var precio = condicionado ? normal : (oferta || normal);
        var texto = precio ? precio.textContent.trim() : '';
        if (!nombre || !link || !/^\$\s*\d[\d.,]*(?:\s*c\s*\/\s*u)?\s*$/i.test(texto)) {
            continue;
        }
        out.push({ok: true, nombre: nombre.textContent.trim(), precio: texto, url: link.href});
        var marca = selectores.marca ? card.querySelector(selectores.marca) : null;
        if (marca) { out[out.length - 1].marca = marca.textContent.trim(); }
    }
    return JSON.stringify(out);
"""


COMPROBAR_UBICACION_JS = r"""
    var dialogos = document.querySelectorAll('[role="dialog"], dialog[open]');
    for (var i = 0; i < dialogos.length; i++) {
        if (dialogos[i].getClientRects().length &&
            /(?:ingresa|selecciona|elige|indica).{0,40}(?:ubicaci[oó]n|comuna|direcci[oó]n)/i.test(dialogos[i].innerText)) {
            return JSON.stringify({error: 'El sitio requiere seleccionar una ubicacion para continuar'});
        }
    }
    return JSON.stringify([]);
"""
