"""
Icon Detector — busca una imagen de referencia en la pantalla.
Sin OpenCV. Usa mss para captura rápida + PIL para template matching.

Modos:
- "screen"  → busca en toda la pantalla
- "region"  → busca solo en (x, y, w, h) — más rápido
"""

import time
from PIL import Image

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False


def _find_subimage(screenshot, icon, threshold=0.05):
    """Busca `icon` dentro de `screenshot`. Retorna (x, y) o None.
    
    Usa comparación por diferencia media de píxeles.
    threshold: 0.0 = match perfecto, 0.05 = 5% de tolerancia.
    """
    sw, sh = screenshot.size
    iw, ih = icon.size

    if iw > sw or ih > sh:
        return None

    # Convertir a RGB para comparación consistente
    screen_rgb = screenshot.convert("RGB")
    icon_rgb = icon.convert("RGB")

    screen_pixels = screen_rgb.load()
    icon_pixels = icon_rgb.load()

    total_icon_pixels = iw * ih
    min_diff = float("inf")
    best_pos = None

    # Buscar en pasos de 2 píxeles para velocidad (luego refina)
    step = max(1, min(iw, ih) // 4)

    for y in range(0, sh - ih + 1, step):
        for x in range(0, sw - iw + 1, step):
            diff = 0
            # Muestrear cada step píxeles
            for iy in range(0, ih, step):
                for ix in range(0, iw, step):
                    try:
                        sp = screen_pixels[x + ix, y + iy]
                        ip = icon_pixels[ix, iy]
                        diff += abs(sp[0] - ip[0]) + abs(sp[1] - ip[1]) + abs(sp[2] - ip[2])
                    except IndexError:
                        pass

            samples = (ih // step) * (iw // step)
            if samples == 0:
                samples = 1
            avg_diff = diff / (samples * 3 * 255)

            if avg_diff < min_diff:
                min_diff = avg_diff
                best_pos = (x, y)

    if min_diff < threshold:
        return best_pos
    return None


def check_icon(icon_path, region=None, threshold=0.05):
    """Verifica si el icono en `icon_path` está visible en pantalla.
    
    Args:
        icon_path: ruta a la imagen .png del icono
        region: None = toda la pantalla, o (x, y, w, h) para región
        threshold: 0.0-1.0, qué tan estricta es la comparación
    
    Returns:
        True si el icono fue encontrado, False si no.
    """
    try:
        icon = Image.open(icon_path)
    except Exception:
        return False  # Icono no válido → no ejecutar

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
                    return True
            return False
    else:
        # Fallback: usar ImageGrab (más lento pero funciona sin mss)
        from PIL import ImageGrab
        if region:
            x, y, w, h = region
            screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        else:
            screenshot = ImageGrab.grab(all_screens=True)

    result = _find_subimage(screenshot, icon, threshold)
    return result is not None


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
    found = check_icon(icon, region)
    elapsed = time.time() - t0
    print(f"Encontrado: {found} ({elapsed:.2f}s)")
