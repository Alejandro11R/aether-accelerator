<div align="center">

# AETHER DOWNLOAD ACCELERATOR

**Acelerador de descargas concurrente multiplataforma de alta velocidad con interfaz Static-Glass.**

[![CI / Automated Testing](https://github.com/Alejandro11R/aether-accelerator/actions/workflows/ci.yml/badge.svg)](https://github.com/Alejandro11R/aether-accelerator/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-00f2fe.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-f43f5e.svg)](#plataformas-soportadas)
[![Hyprland Ready](https://img.shields.io/badge/Wayland-Hyprland%20Ready-00f2fe.svg)](#configuración-en-hyprland-linux)

<br/>

Aether es una alternativa de código abierto a gestores de descargas. Está construido sobre un motor asíncrono en Python con `aiohttp` y una interfaz web ligera de cristal estático (*Static-Glass*) pensada para **Linux (Hyprland / Wayland / X11)**, **Windows** y **macOS**.

</div>

---

## Características Principales

* **Segmentación Dinámica Concurrente**: De 1 a 32 conexiones TCP simultáneas. Cuando un hilo termina antes, subdivide automáticamente los segmentos más lentos para aprovechar el ancho de banda, con validación estricta de rangos, comprobación de `ETag` al reanudar y degradación automática a flujo lineal cuando el servidor no soporta `Accept-Ranges` o responde `200 OK`.
* **Escritura Concurrente Segura y Pausa/Reanudación**: Pre-asignación de espacio y escritura posicional (`os.pwrite` en POSIX, bloqueo de E/S en Windows) con rollback de offset ante errores de disco (`OSError`), persistencia atómica de estado en `.aether.json` y reintentos acotados con backoff frente a fallos HTTP transitorios.
* **Interfaz Static-Glass de Bajo Consumo**: Diseño oscuro (`#05070e`) con degradados radiales estáticos, bordes biselados y relieve interior sin halos `blur(120px)` ni bucles de animación en reposo, optimizado para ventanas flotantes o media pantalla.
* **3 Diales de Tema en Vivo**: **Obsidian Glass**, **Cyber Neon** y **OLED Frost**.
* **Telemetría y Control en Tiempo Real**: Franja compacta de actividad global y sparkline SVG por WebSocket (actualizado cada `500 ms` y pausado automáticamente cuando la pestaña está oculta), barra rápida de descarga desde el estado vacío, detalle expandible inline con banda segmentada por conexión, límite de velocidad en vivo, apertura segura de carpeta nativa y modal accesible de confirmación.
* **Seguridad Local Endurecida**: Validación de cabecera `Host` contra DNS-rebinding, verificación anti-CSRF (`Origin` y `Sec-Fetch-Site`), exigencia de `Content-Type: application/json`, sanitización de nombres remotos y reservados de Windows, confinamiento estricto de directorios de salida y renderizado DOM sin `innerHTML`.
* **Extensión para Navegadores (Manifest V3)**: Intercepta descargas de Chrome o Brave, agrega accesos en el menú contextual y detecta medios HTML5 para enviarlos al motor local en `http://127.0.0.1:8765`.

---

## Plataformas Soportadas

| Sistema Operativo | Modo de Ventana | Integración |
| :--- | :--- | :--- |
| **Linux** (Arch, CachyOS, Ubuntu, Fedora) | Wayland / X11 | Ventana independiente vía Brave/Chrome/Chromium con soporte para Hyprland, Sway, GNOME y KDE. |
| **Windows** (10, 11) | Ventana Nativa | Lanzamiento automático en modo aplicación vía Edge, Chrome o Brave (fallback al navegador predeterminado). |
| **macOS** (Apple Silicon / Intel) | App Mode | Lanzamiento en modo aplicación vía Brave o Chrome (fallback con `open`). |

---

## Instalación y Uso

### Opción 1: Ejecución con Python (Recomendada)

```bash
# 1. Clonar el repositorio
git clone https://github.com/Alejandro11R/aether-accelerator.git
cd aether-accelerator

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Iniciar el acelerador
python run.py
```

*(En Linux también puedes ejecutar directamente `./aether` o usar `--no-browser` para iniciar solo el servidor en `http://127.0.0.1:8765`).*

### Opción 2: Binarios Precompilados (Sin Python)

Descarga el ejecutable correspondiente desde la sección de **[Releases](https://github.com/Alejandro11R/aether-accelerator/releases)**:
* **Linux**: `aether-linux-x64` (`chmod +x aether-linux-x64 && ./aether-linux-x64`)
* **Windows**: `aether-windows-x64.exe`
* **macOS**: `aether-macos-x64`

---

## Extensión del Navegador (Chrome / Brave)

1. Inicia el motor local de Aether (`python run.py` o `./aether`).
2. Abre `brave://extensions` o `chrome://extensions`.
3. Activa el **Modo de desarrollador** en la esquina superior derecha.
4. Haz clic en **Cargar descomprimida** (*Load unpacked*) y selecciona la carpeta `extension/` de este repositorio.
5. Desde el popup de la extensión puedes verificar el estado del motor (`127.0.0.1:8765`), ajustar los hilos por defecto y activar o pausar la intercepción automática.

---

## Configuración en Hyprland (Linux)

Para que Aether se abra como ventana flotante centrada en Hyprland, añade estas reglas a `~/.config/hypr/hyprland.conf`:

```ini
windowrulev2 = float, class:^(AetherAccelerator)$
windowrulev2 = size 1200 850, class:^(AetherAccelerator)$
windowrulev2 = center, class:^(AetherAccelerator)$
windowrulev2 = opacity 0.88 0.88, class:^(AetherAccelerator)$
```

---

## Suite de Pruebas Automatizadas

El proyecto cuenta con **54 pruebas** unitarias, de integración, seguridad local y comportamiento DOM de la interfaz (sin regresiones ni advertencias):

```bash
python -W default -m unittest discover -s tests -v
```

Cobertura incluida:
* `tests/test_engine.py`: segmentación dinámica, limitador de velocidad agregado, pausa/reanudación, cambio de `ETag`, degradación a flujo único, manejo de `OSError` en disco y verificación SHA-256.
* `tests/test_security.py`: validación de nombres personalizados/remotos, nombres reservados de Windows y confinamiento de directorios frente a traversal y symlinks.
* `tests/test_server.py`: middleware contra DNS-rebinding/CSRF, validación `Content-Type`, endpoints REST/WebSocket, apertura segura de carpetas y cierre limpio del servidor.
* `tests/test_ui_security.py`: pruebas en Node `vm` sobre `ui/index.html` y `ui/app.js` verificando renderizado DOM incremental seguro (cero `innerHTML`), bandas segmentadas, control de límite en vivo, modales accesibles y manejo de errores. *(Requiere `node` en el `PATH`).*

---

## Arquitectura del Sistema

```text
aether-accelerator/
├── .github/
│   └── workflows/
│       ├── ci.yml                  # Integración continua en Linux, Windows y macOS
│       └── release.yml             # Compilación de binarios con PyInstaller al crear tags v*
├── core/
│   ├── engine.py                   # Motor asíncrono, probe(), segmentación dinámica y reanudación
│   ├── manager.py                  # Gestor de cola de descargas, persistencia y broadcast WS
│   ├── security.py                 # Sanitización de nombres, validación de rutas y anti-escape
│   └── segment.py                  # Modelo de segmento, rangos de bytes y velocidad en ventana
├── server/
│   └── app.py                      # Servidor aiohttp, middleware de seguridad, API REST y WebSocket
├── ui/
│   ├── index.html                  # Dashboard Static-Glass (diales de tema, modales y barra rápida)
│   ├── style.css                   # Sistema visual Static-Glass (Obsidian, Cyber, OLED Frost)
│   └── app.js                      # Estado reactivo, DOM incremental, sparkline SVG y WS
├── extension/                      # Extensión de navegador Manifest V3 (popup, worker y content script)
├── tests/
│   ├── test_engine.py              # Pruebas del motor de descargas e integración HTTP mock
│   ├── test_security.py            # Pruebas de validación de rutas, nombres y symlinks
│   ├── test_server.py              # Pruebas del servidor HTTP/WS, CSRF, host check y sistema
│   └── test_ui_security.py         # Pruebas de seguridad DOM y comportamiento de ui/app.js
├── aether-core/                    # Prototipo experimental en Rust (no integrado en el runtime actual)
├── aether                          # Lanzador directo en Unix
├── aether.spec                     # Configuración de PyInstaller
├── run.py                          # Punto de entrada CLI y lanzador multiplataforma
├── pyproject.toml                  # Metadatos del paquete y script `aether`
├── requirements.txt                # Dependencia de producción (`aiohttp>=3.9.0`)
├── requirements-dev.txt            # Dependencias de desarrollo y empaquetado
├── CONTRIBUTING.md                 # Guía de contribución y estándares de calidad
├── PRIVACY_POLICY.md               # Política de privacidad de la extensión
├── LICENSE                         # Licencia MIT
└── README.md                       # Documentación principal
```

---

## Licencia

Este proyecto es de código abierto bajo la licencia **[MIT](LICENSE)**.  
Desarrollado por [Alejandro11R](https://github.com/Alejandro11R).
