# AutoPulsar

Grabador y reproductor de macros de teclado y ratón con condiciones visuales. Crea, edita y ejecuta secuencias automatizadas — sin depender de herramientas externas.

Para lo que necesites: productividad, gaming, testing, tareas repetitivas, gestión de ventanas, o lo que se te ocurra.

---

## 🚀 Descarga rápida

👉 **[Descargar AutoPulsar.exe](https://github.com/roderick850/TinyTaskOrchestrator/releases/latest)** — ~11 MB, portable, no requiere instalación.

> 💡 Si Windows SmartScreen te bloquea, haz clic en **"Más información"** → **"Ejecutar de todas formas"**. El archivo es seguro, es un falso positivo porque no está firmado digitalmente.

---

## Requisitos

- **Windows** (10/11)
- **Python 3.10+** (solo si ejecutas desde el código fuente)

---

## Instalación

### Opción 1: Ejecutable compilado (recomendado)

Descarga `AutoPulsar.exe` desde la sección de **[Releases](https://github.com/roderick850/TinyTaskOrchestrator/releases)**. Es portable — no necesita Python ni nada instalado. Solo descarga, ejecuta, y listo.

### Opción 2: Desde el código fuente

```bash
git clone https://github.com/roderick850/TinyTaskOrchestrator.git
cd TinyTaskOrchestrator
pip install -r requirements.txt
python main.py
```

### Opción 3: Compilar tu propio .exe

```bash
pip install pyinstaller
pyinstaller --clean AutoPulsar.spec
```

El `.exe` se genera en `dist/AutoPulsar.exe`.

---

## Funciones

### 🎬 Macros nativas

El corazón de AutoPulsar. Graba, edita y reproduce secuencias de teclado y ratón sin necesidad de TinyTask ni ninguna herramienta externa.

- **Grabación en vivo** — presiona teclas, haz clics, mueve el mouse. Todo se captura.
- **Editor visual tipo timeline** — cada acción se muestra como una tarjeta con su tecla, duración y espera.
- **Añadir manualmente** — agrega teclas, clics y pausas sin grabar.
- **Reordenar** — mueve acciones arriba/abajo con un clic.
- **Editar inline** — doble clic para modificar cualquier parámetro.
- **Reproducir** — prueba la macro antes de guardarla.

### 📜 Scripts externos

Si ya tienes macros compiladas con TinyTask u otras herramientas, también puedes agregarlas a la playlist.

| Parámetro | Descripción |
|-----------|------------|
| **Repeticiones** | Cuántas veces se ejecuta ese script |
| **Duración (s)** | Cuánto tarda en completarse (para calcular tiempos) |
| **Pausa (s)** | Espera entre cada repetición |

### 🔄 Modos de loop global

- **Una vez** — ejecuta toda la lista y termina
- **Fijo** — repite la lista completa N veces
- **Infinito** — repite hasta que lo detengas manualmente

### 🎯 Condiciones visuales

Ejecuta scripts solo si ciertos iconos están (o no están) visibles en pantalla. Ideal para automatizaciones que dependen del estado visual del juego o aplicación.

- **Requerir** — el icono DEBE estar visible
- **Bloquear** — el icono NO debe estar visible
- **Repeat Until** — repetir hasta que aparezca/desaparezca un icono
- **Diagnóstico de matching** — analiza y sugiere la tolerancia óptima

### ✅ Habilitar / Deshabilitar scripts

Clic en el checkbox ✅/❌ de cada script para activarlo o saltarlo sin borrarlo de la lista.

### ✏️ Edición inline rápida

Doble clic en cualquier celda de Repeticiones, Duración o Pausa para editarla directamente sin abrir ventanas emergentes.

### ⏱️ Tiempo estimado total

La interfaz calcula y muestra el tiempo estimado de ejecución basado en las duraciones y repeticiones configuradas.

### 📊 Barra de progreso + countdown

Durante la ejecución ves una barra de progreso, porcentaje completado, y un countdown regresivo con el tiempo restante estimado.

### ⌨️ Hotkey global configurable

Configura una tecla rápida (F5–F12) para iniciar o detener la ejecución sin enfocar la ventana. Funciona aunque el programa esté en segundo plano.

### 👤 Perfiles

Playlists independientes para separar automatizaciones de distintos juegos o tareas sin mezclar configuraciones.

### 📌 Mini Bar

Barra flotante compacta always-on-top. Ideal para gaming en un solo monitor — muestra el progreso sin estorbar.

---

## Cómo usar

1. **Crea una macro** — clic en `🎬 Macro`, asígnale un nombre, graba o añade acciones manualmente
2. **Agrega más items** — usa `➕ Agregar` si necesitas scripts externos (.exe)
3. **Configura repeticiones y pausas** — doble clic en cada columna para editar
4. **Ordénalos** con ⬆️⬇️ según el orden de ejecución
5. **Configura** el loop global (una vez, N repeticiones, o infinito)
6. **Ejecuta** con ▶️ *Iniciar* o con la hotkey
7. **Detén** en cualquier momento con ⏹️ *Detener* o la hotkey

### Flujo de ejecución

```
Macro/Script 1 (×N reps) → Macro/Script 2 (×N reps) → ... → [pausa entre loops] → repetir
```

- Cada item se ejecuta la cantidad de veces configurada
- Si configuraste *pausa entre loops*, espera ese tiempo antes del siguiente ciclo

---

## Archivos

| Archivo | Descripción |
|---------|------------|
| `main.py` | Punto de entrada |
| `gui.py` | Interfaz gráfica principal (tkinter + customtkinter) |
| `gui_macro.py` | Editor visual de macros |
| `macro_recorder.py` | Grabador de teclado/ratón |
| `macro_player.py` | Reproductor de macros |
| `executor.py` | Motor de ejecución con hilos |
| `config_manager.py` | Carga/guarda configuración + perfiles |
| `hotkey.py` | Listener de tecla global |
| `mini_bar.py` | Barra flotante compacta |
| `icon_detector.py` | Detección visual de iconos |
| `AutoPulsar.spec` | Spec de PyInstaller |

---

## Tips

- **Macros nativas:** no necesitas TinyTask para nada. Graba directo en AutoPulsar.
- **Duración estimada:** pon siempre un valor realista. El orquestador usa este número para calcular tiempos totales y la barra de progreso.
- **Pausa:** útil si el script necesita que la app destino termine de procesar antes de repetir.
- **Loop infinito + pausa entre loops:** ideal para tareas que corren todo el día con descansos programados.
- **Deshabilitar scripts:** usa el checkbox ✅/❌ para probar distintas combinaciones sin borrar ni reconfigurar.
- **Condiciones visuales:** agrega condiciones a los scripts clave para que solo se ejecuten en la pantalla correcta.
