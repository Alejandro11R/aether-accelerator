<div align="center">

# ⚡ AETHER DOWNLOAD ACCELERATOR

**Acelerador de descargas concurrente multiplataforma de alta velocidad con interfaz translúcida de Glassmorphism.**

[![CI / Automated Testing](https://github.com/Alejandro11R/aether-accelerator/actions/workflows/ci.yml/badge.svg)](https://github.com/Alejandro11R/aether-accelerator/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-00f2fe.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-f43f5e.svg)](#plataformas)
[![Hyprland Ready](https://img.shields.io/badge/Wayland-Hyprland%20Ready-00f2fe.svg)](#configuración-en-hyprland)

<br/>

Aether es una alternativa de código abierto a programas privativos como Internet Download Manager (IDM) y XDM. Diseñado desde cero con un motor asíncrono ultra-rápido sobre `aiohttp` y una interfaz gráfica futurista con efecto cristal (*Glassmorphism*) optimizada para escritorios modernos como **Hyprland**, **Windows** y **macOS**.

</div>

---

## ✨ Características Principales

*   🚀 **Segmentación Dinámica Concurrente**: Conexiones simultáneas paralelas (de 1 a 32 hilos). Cuando un hilo termina anticipadamente, **subdivide automáticamente** los fragmentos más rezagados para mantener el 100% de saturación de red hasta el último byte (algoritmo inteligente estilo IDM).
*   💎 **Interfaz Glassmorphism de Alta Gama**: Paneles translúcidos con `backdrop-filter: blur(28px)`, bordes de cristal biselados y resplandor interno en fondo oscuro puro (`#040408`).
*   🎛️ **3 Diales de Tema en Vivo (*Impeccable Design*)**:
    1.  **Obsidian Glass**: Fondo ultra oscuro con acentos cian y violeta cuántico.
    2.  **Cyber Neon**: Flúor magenta con alto contraste y resplandor eléctrico.
    3.  **OLED Frost**: Monocromo minimalista puro (negro absoluto y blanco hielo).
*   📊 **Visualizador Matricial de Segmentos en Vivo**: Inspecciona en tiempo real el progreso de cada hilo TCP individual, su velocidad en MB/s y sus rangos de bytes.
*   📈 **Velocímetro SVG Reactivo (Sparkline)**: Gráfica fluida de ancho de banda global transmitida por WebSockets cada 100ms.
*   🛡️ **Cero Regresiones & TDD**: Desarrollado bajo la disciplina estricta del **Clan Tortugas Ninja & Maestro Splinter** con verificación obligatoria de sumas de comprobación SHA-256 e integridad byte-a-byte.
*   💾 **Pre-asignación Zero-Wait en Disco**: Escritura no bloqueante directa con `seek` y pre-dimensionamiento para prevenir la fragmentación de archivos en Linux (ext4/btrfs) y Windows (NTFS).
*   ⏸️ **Pausa y Reanudación Real**: Almacena el estado de los bloques en archivos `.aether.json` para continuar descargas interrumpidas en cualquier momento.

---

## 🖥️ Plataformas Soportadas

| Sistema Operativo | Modo de Ventana | Integración |
| :--- | :--- | :--- |
| 🐧 **Linux** (CachyOS, Arch, Ubuntu, Fedora) | Wayland / X11 | Ventana flotante con soporte nativo de transparencia en Hyprland, Sway, GNOME, KDE. |
| 🪟 **Windows** (10, 11) | Ventana Nativa | Lanzamiento automático en ventana de aplicación independiente vía Edge / Chrome / Brave. |
| 🍎 **macOS** (Apple Silicon / Intel) | App Mode | Lanzamiento optimizado en Brave, Chrome o Safari. |

---

## 🚀 Instalación y Uso

### Opción 1: Ejecución con Python (Recomendada para desarrolladores)

```bash
# 1. Clonar el repositorio
git clone https://github.com/Alejandro11R/aether-accelerator.git
cd aether-accelerator

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Iniciar el acelerador
python run.py
```
*(En Linux también puedes ejecutar directamente `./aether`)*.

---

### Opción 2: Descargar Binarios Precompilados (Sin necesidad de Python)

Descarga el ejecutable para tu plataforma desde la sección de **[Releases](https://github.com/Alejandro11R/aether-accelerator/releases)**:
*   **Linux**: `aether-linux-x64` (`chmod +x aether-linux-x64 && ./aether-linux-x64`)
*   **Windows**: `aether-windows-x64.exe`
*   **macOS**: `aether-macos-x64`

---

## 🪟 Configuración en Hyprland (Linux)

Para que Aether se abra como una ventana flotante translúcida y con desenfoque automático en Hyprland, añade las siguientes reglas a tu `~/.config/hypr/hyprland.conf`:

```ini
windowrulev2 = float, class:^(AetherAccelerator)$
windowrulev2 = size 1200 850, class:^(AetherAccelerator)$
windowrulev2 = center, class:^(AetherAccelerator)$
windowrulev2 = opacity 0.88 0.88, class:^(AetherAccelerator)$
```

---

## 🧪 Suite de Pruebas Automatizadas (TDD)

El proyecto incluye pruebas integrales que verifican la matemática de segmentación, particionado dinámico de hilos, persistencia de estado y validación de hash SHA-256 contra un servidor HTTP mock:

```bash
python tests/test_engine.py
```

Salida esperada:
```
....
----------------------------------------------------------------------
Ran 4 tests in 0.3s

OK
```

---

## 🏛️ Arquitectura del Sistema

```
aether-accelerator/
├── .github/
│   └── workflows/
│       ├── ci.yml                    # Testeo automático en Linux, Windows y macOS
│       └── release.yml               # Generación y compilación de binarios multiplataforma
├── .skills/                          # Skills de ingeniería locales del proyecto
│   ├── tortugas-ninja/SKILL.md       # Clan Tortugas Ninja & Maestro Splinter
│   ├── frontend-design/SKILL.md      # Principios estéticos anti-genéricos
│   ├── impeccable-design/SKILL.md    # Diales y auditoría visual de componentes
│   ├── high-end-visual-design/SKILL.md # Glassmorphism estricto
│   └── ui-taste/SKILL.md             # Contraste y consistencia tipográfica
├── core/
│   ├── segment.py                    # Modelo matemático de hilos y cálculo de rangos
│   ├── engine.py                     # Motor asíncrono y particionado dinámico de chunks
│   └── manager.py                    # Coordinador de cola de descargas y WebSocket
├── server/
│   └── app.py                        # Servidor aiohttp.web, API REST y WebSockets (100ms)
├── ui/
│   ├── index.html                    # Dashboard interactivo con matriz de hilos estilo IDM
│   ├── style.css                     # Sistema Glassmorphism y variables CSS de los 3 temas
│   └── app.js                        # Cliente reactivo, portapapeles y Sparkline SVG
├── tests/
│   └── test_engine.py                # Suite de pruebas unitarias y de integración
├── aether                            # Script ejecutable directo para Unix
├── aether.spec                       # Especificación de PyInstaller para binarios
├── run.py                            # Lanzador universal con detección de navegadores
├── pyproject.toml                    # Configuración de empaquetado estándar
├── requirements.txt                  # Dependencias de producción
├── requirements-dev.txt              # Dependencias de desarrollo y testing
├── LICENSE                           # Licencia de código abierto MIT
└── README.md                         # Documentación oficial
```

---

## 🐢 Squad de Ingeniería

*   🐢 **Leonardo**: Arquitectura UI/UX Glassmorphism y layout responsivo con escala áurea.
*   🐢 **Donatello**: Motor asíncrono, algoritmos de segmentación y gestión de sockets TCP.
*   🐀 **Maestro Splinter**: Seguridad, sanitización de rutas y validación de sumas de verificación.
*   🐢 **Raphael**: QA, TDD y pruebas de resiliencia ante cortes y reconexión.
*   🐢 **Michelangelo**: Micro-interacciones, efectos visuales de flujo de datos y diales de color.

---

## 📄 Licencia

Este proyecto es de código abierto bajo la licencia **[MIT](LICENSE)**.
Desarrollado por [Alejandro11R](https://github.com/Alejandro11R).
