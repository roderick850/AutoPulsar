"""
Icon Detector — busca una imagen de referencia en la pantalla.
Sin OpenCV. Usa mss para captura rápida + PIL para template matching.

Modos:
- "screen"  → busca en toda la pantalla
- "region"  → busca solo en (x, y, w, h) — más rápido
"""

import time
import os
from PIL import Image

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False


def _find_subimage(screenshot, icon, threshold=0.08, return_debug=False):
    """Busca `icon` dentro de `screenshot`. Retorna (x, y) o None.

    Algoritmo en dos fases:
    1. Busqueda gruesa (paso grande) -> candidato aproximado
    2. Refinamiento fino (+-step alrededor del candidato, paso=1)
       -> posicion exacta

    threshold: 0.0 = match perfecto, 0.08 = 8% de tolerancia (mas
    permisivo para manejar diferencias de renderizado entre captura
    y ejecucion).

    Si return_debug=True, retorna (best_pos, min_diff) en vez de
    solo best_pos -- util para diagnostico.
    """
    sw, sh = screenshot.size
    iw, ih = icon.size

    if iw > sw or ih > sh:
        if return_debug:
            return None, float("inf")
        return None

    # Convertir a RGB para comparación consistente
    screen_rgb = screenshot.convert("RGB")
    icon_rgb = icon.convert("RGB")

    screen_pixels = screen_rgb.load()
    icon_pixels = icon_rgb.load()

    # ── Nota: el threshold se usa tal cual ──
    # Para imágenes con fondo uniforme que producen falsos positivos,
    # bajá la tolerancia manualmente desde ⚙️ Condiciones → 🎯 Tolerancia.
    adjusted_threshold = threshold

    # Paso adaptativo: mas fino para iconos pequeños
    # Si la captura es de una region chica (search area), usar paso mas fino
    total_area = sw * sh
    if total_area < 600000:  # ~800x700, tipico de una region con padding
        step = max(1, min(iw, ih) // 12)  # muy fino en regiones
    else:
        step = max(1, min(iw, ih) // 8)   # mas fino que antes (era //6)

    # ── Fase 1: búsqueda gruesa ──
    min_diff = float("inf")
    best_pos = None

    for y in range(0, sh - ih + 1, step):
        for x in range(0, sw - iw + 1, step):
            diff = 0
            samples = 0
            for iy in range(0, ih, step):
                for ix in range(0, iw, step):
                    try:
                        sp = screen_pixels[x + ix, y + iy]
                        ip = icon_pixels[ix, iy]
                        diff += abs(sp[0] - ip[0]) + abs(sp[1] - ip[1]) + abs(sp[2] - ip[2])
                        samples += 1
                    except IndexError:
                        pass

            if samples == 0:
                continue
            avg_diff = diff / (samples * 3 * 255)

            if avg_diff < min_diff:
                min_diff = avg_diff
                best_pos = (x, y)

    if best_pos is None:
        if return_debug:
            return None, float("inf")
        return None

    # ── Fase 2: refinamiento alrededor del mejor candidato ──
    # Radio amplio (step*3) para no perder el match real por un
    # falso positivo en la fase gruesa
    bx, by = best_pos
    refine_radius = step * 3
    x_start = max(0, bx - refine_radius)
    y_start = max(0, by - refine_radius)
    x_end = min(sw - iw, bx + refine_radius)
    y_end = min(sh - ih, by + refine_radius)

    # Muestreo mas denso para la fase de refinamiento
    fine_sample = max(1, min(iw, ih) // 16)

    for y in range(y_start, y_end + 1):
        for x in range(x_start, x_end + 1):
            diff = 0
            samples = 0
            for iy in range(0, ih, fine_sample):
                for ix in range(0, iw, fine_sample):
                    try:
                        sp = screen_pixels[x + ix, y + iy]
                        ip = icon_pixels[ix, iy]
                        diff += abs(sp[0] - ip[0]) + abs(sp[1] - ip[1]) + abs(sp[2] - ip[2])
                        samples += 1
                    except IndexError:
                        pass

            if samples == 0:
                continue
            avg_diff = diff / (samples * 3 * 255)

            if avg_diff < min_diff:
                min_diff = avg_diff
                best_pos = (x, y)

    if return_debug:
        return best_pos, min_diff
    if min_diff < adjusted_threshold:
        return best_pos
    return None


def check_icon(icon_path, region=None, threshold=0.08):
    """Verifica si el icono en `icon_path` está visible en pantalla.
    
    Args:
        icon_path: ruta a la imagen .png del icono
        region: None = toda la pantalla, o (x, y, w, h) para región
        threshold: 0.0-1.0, qué tan estricta es la comparación
    
    Returns:
        (found, error_msg)
        - (True, None) si el icono fue encontrado
        - (False, "missing") si el archivo no existe
        - (False, "not_found") si el archivo existe pero no se encontró en pantalla
    """
    if not os.path.exists(icon_path):
        return False, "missing"

    try:
        icon = Image.open(icon_path)
    except Exception:
        return False, "missing"  # archivo corrupto o no válido

    if HAS_MSS:
        with mss.mss() as sct:
            if region:
                monitors = [{"left": region[0], "top": region[1],
                            "width": region[2], "height": region[3]}]
            else:
                # Buscar en TODOS los monitores (índice 1+)
                monitors = [sct.monitors[i] for i in range(1, len(sct.monitors))]

            for monitor in monitors:
                screenshot = sct.grab(monitor)
                screenshot = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                if _find_subimage(screenshot, icon, threshold):
                    return True, None
            return False, "not_found"
    else:
        # Fallback: usar ImageGrab (más lento pero funciona sin mss)
        from PIL import ImageGrab
        if region:
            x, y, w, h = region
            screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        else:
            screenshot = ImageGrab.grab(all_screens=True)

    result = _find_subimage(screenshot, icon, threshold)
    if result is not None:
        return True, None
    return False, "not_found"


def check_conditions(conditions):
    """Evalúa múltiples condiciones para decidir si ejecutar un script.
    
    conditions = {
        "mode": "and" | "or",
        "items": [
            {"type": "require", "icon_path": "...", "label": "menú"},
            {"type": "block",   "icon_path": "...", "label": "cargando"},
        ]
    }
    
    - "require": el icono DEBE estar visible
    - "block":   el icono NO debe estar visible
    - mode "and": TODAS las condiciones deben cumplirse
    - mode "or":  AL MENOS UNA condición debe cumplirse
    
    Retorna (should_execute, reason).
    should_execute: True si el script DEBE ejecutarse.
    reason: None si ok, o string con la razón del fallo.
    Si no hay condiciones, retorna (True, None).
    """
    items = conditions.get("items", [])
    if not items:
        return True, None

    mode = conditions.get("mode", "and")
    results = []
    reasons = []

    for cond in items:
        icon_path = cond.get("icon_path", "")
        ctype = cond.get("type", "require")

        if not icon_path:
            # Sin icono: "block" sin icono = no se puede bloquear → pasa
            # "require" sin icono = no se puede requerir → falla
            passed = True if ctype == "block" else False
            results.append(passed)
            if not passed:
                reasons.append(f"Requiere icono (sin ruta)")
            continue

        found, error = check_icon_multi(
            icon_path, cond.get("region"),
            threshold=cond.get("threshold", 0.08),
            samples=cond.get("samples", 1),
            confidence=cond.get("confidence", 1.0))

        if ctype == "require":
            results.append(found)       # debe estar → True si visible
            if not found:
                tag = "falta icono" if error == "missing" else "icono no visible"
                reasons.append(f"Requerir: {tag} ({os.path.basename(icon_path)})")
        elif ctype == "block":
            blocked = not found         # debe NO estar → True si NO visible
            results.append(blocked)
            if not blocked:
                # El icono SÍ está visible → no debería ejecutarse
                reasons.append(f"Bloquear: icono visible ({os.path.basename(icon_path)})")
        else:
            results.append(found)

    if mode == "or":
        passed = any(results)
    else:  # "and"
        passed = all(results)

    if passed:
        return True, None
    else:
        return False, "; ".join(reasons) if reasons else "condiciones no cumplidas"


def check_icon_multi(icon_path, region=None, threshold=0.08,
                     samples=1, confidence=1.0):
    """Verifica un icono con múltiples capturas para mayor fiabilidad.

    Captura la pantalla `samples` veces y cuenta cuántas matchean.
    Retorna True si el porcentaje de aciertos >= confidence.

    Args:
        icon_path: ruta al icono .png
        region: None o [x, y, w, h]
        threshold: tolerancia
        samples: cuántas capturas hacer (1 = single, igual que check_icon)
        confidence: 0.0-1.0, porcentaje mínimo de aciertos

    Returns:
        (found, error_msg)
        found = True si (hits / samples) >= confidence
    """
    if samples <= 1:
        return check_icon(icon_path, region, threshold)

    hits = 0
    last_error = None
    for _ in range(samples):
        f, err = check_icon(icon_path, region, threshold)
        if f:
            hits += 1
        else:
            last_error = err

    success_rate = hits / samples
    if success_rate >= confidence:
        return True, None
    else:
        return False, f"multi: {hits}/{samples} ({success_rate:.0%})"


def diagnose_icon(icon_path, region=None, threshold=0.08):
    """Diagnostica el matching de un icono contra la pantalla.

    Captura la pantalla, busca el mejor match, y retorna un dict
    con toda la informacion necesaria para ajustar la tolerancia.

    Args:
        icon_path: ruta a la imagen .png del icono
        region: None = toda la pantalla, o (x, y, w, h) para region
        threshold: tolerancia actual configurada (para comparar)

    Returns:
        dict con:
        - found: bool, si matchea con el threshold actual
        - min_diff: float, la menor diferencia encontrada (0.0-1.0)
        - position: (x, y) o None, donde se encontro el mejor match
        - icon_size: (w, h) del icono
        - error: str o None, si hubo un error (missing, too_big, etc.)
        - recommendation: float, tolerancia sugerida (min_diff * 1.2)
    """
    import os
    from PIL import Image

    if not os.path.exists(icon_path):
        return {"found": False, "min_diff": 1.0, "position": None,
                "icon_size": (0, 0), "error": "missing",
                "recommendation": 0.08}

    try:
        icon = Image.open(icon_path)
    except Exception:
        return {"found": False, "min_diff": 1.0, "position": None,
                "icon_size": (0, 0), "error": "invalid_file",
                "recommendation": 0.08}

    icon_size = icon.size
    best_pos = None
    best_diff = float("inf")
    best_offset = (0, 0)

    if HAS_MSS:
        with mss.mss() as sct:
            if region:
                monitors = [{"left": region[0], "top": region[1],
                            "width": region[2], "height": region[3]}]
            else:
                monitors = [sct.monitors[i] for i in range(1, len(sct.monitors))]

            for monitor in monitors:
                m_left = monitor.get("left", monitor.get("x", 0))
                m_top = monitor.get("top", monitor.get("y", 0))
                screenshot = sct.grab(monitor)
                screenshot = Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                pos, diff = _find_subimage(
                    screenshot, icon, threshold, return_debug=True)

                if pos is not None and diff < best_diff:
                    best_diff = diff
                    best_pos = (pos[0] + m_left, pos[1] + m_top)
                    best_offset = (m_left, m_top)
    else:
        from PIL import ImageGrab
        if region:
            x, y, w, h = region
            screenshot = ImageGrab.grab(
                bbox=(x, y, x + w, y + h), all_screens=True)
        else:
            screenshot = ImageGrab.grab(all_screens=True)
        pos, diff = _find_subimage(
            screenshot, icon, threshold, return_debug=True)
        if pos is not None:
            best_pos = pos
            best_diff = diff

    found = best_pos is not None and best_diff < threshold
    recommendation = round(best_diff * 1.2, 3) if best_diff < float("inf") else 0.08

    return {
        "found": found,
        "min_diff": best_diff if best_diff < float("inf") else 1.0,
        "position": best_pos,
        "icon_size": icon_size,
        "error": None,
        "recommendation": recommendation,
    }


def check_icon_on_screenshot(screenshot, icon_path, threshold=0.08):
    """Verifica si el icono está en una captura ya hecha (sin volver a capturar).

    Args:
        screenshot: PIL Image ya capturada
        icon_path: ruta a la imagen .png del icono
        threshold: 0.0-1.0, tolerancia

    Returns:
        (found, error_msg)
    """
    if not os.path.exists(icon_path):
        return False, "missing"
    try:
        icon = Image.open(icon_path)
    except Exception:
        return False, "missing"
    result = _find_subimage(screenshot, icon, threshold)
    if result is not None:
        return True, None
    return False, "not_found"


def check_conditions_on_screenshots(screenshots, conditions):
    """Evalúa condiciones contra una lista de capturas ya hechas.

    Igual lógica que check_conditions() pero sin volver a capturar
    la pantalla — reutiliza las screenshots pasadas como argumento.

    Si una condición tiene ``region`` o ``samples > 1``, se hace
    fallback a ``check_icon`` / ``check_icon_multi`` con captura
    fresca — la región requiere un área de búsqueda acotada que no
    se puede extraer de una captura full-screen sin offsets de
    monitor, y el multi-muestreo necesita capturas distintas.

    Args:
        screenshots: lista de PIL Image (una por monitor)
        conditions: dict con mode + items (ver check_conditions)

    Returns:
        (should_execute, reason)
    """
    items = conditions.get("items", [])
    if not items:
        return True, None

    mode = conditions.get("mode", "and")
    results = []
    reasons = []

    for cond in items:
        icon_path = cond.get("icon_path", "")
        ctype = cond.get("type", "require")

        if not icon_path:
            passed = True if ctype == "block" else False
            results.append(passed)
            if not passed:
                reasons.append("Requiere icono (sin ruta)")
            continue

        region = cond.get("region")
        samples = cond.get("samples", 1)
        threshold = cond.get("threshold", 0.08)
        confidence = cond.get("confidence", 1.0)

        # ── Fallback a captura fresca si hay región o multi-muestreo ──
        if region or samples > 1:
            found, _ = check_icon_multi(
                icon_path, region,
                threshold=threshold,
                samples=samples,
                confidence=confidence)
        else:
            # Buscar en todas las screenshots cacheadas hasta encontrar
            found = False
            for screenshot in screenshots:
                f, _ = check_icon_on_screenshot(
                    screenshot, icon_path, threshold=threshold)
                if f:
                    found = True
                    break

        if ctype == "require":
            results.append(found)
            if not found:
                tag = "falta icono" if not os.path.exists(icon_path) else "icono no visible"
                reasons.append(f"Requerir: {tag} ({os.path.basename(icon_path)})")
        elif ctype == "block":
            blocked = not found
            results.append(blocked)
            if not blocked:
                reasons.append(f"Bloquear: icono visible ({os.path.basename(icon_path)})")
        else:
            results.append(found)

    if mode == "or":
        passed = any(results)
    else:
        passed = all(results)

    if passed:
        return True, None
    else:
        return False, "; ".join(reasons) if reasons else "condiciones no cumplidas"


def capture_all_screens():
    """Captura todos los monitores y retorna una lista de PIL Image.

    Returns:
        list[PIL.Image]: una imagen por monitor
    """
    if HAS_MSS:
        with mss.mss() as sct:
            screenshots = []
            for monitor in sct.monitors[1:]:
                screenshot = sct.grab(monitor)
                screenshots.append(Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"))
            return screenshots
    else:
        from PIL import ImageGrab
        return [ImageGrab.grab(all_screens=True)]


# ── Prueba rápida ──
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python icon_detector.py icono.png [x y w h]")
        sys.exit(1)

    icon = sys.argv[1]
    region = None
    if len(sys.argv) >= 6:
        region = tuple(map(int, sys.argv[2:6]))

    t0 = time.time()
    found, error = check_icon(icon, region)
    elapsed = time.time() - t0
    status = "ENCONTRADO" if found else f"NO encontrado ({error})"
    print(f"{status} ({elapsed:.2f}s)")
