#!/usr/bin/env python3
"""
Aether Download Accelerator — Universal Cross-Platform Launcher
Compatible con Linux (Wayland/X11), Windows y macOS.
Empaquetable como binario independiente con PyInstaller.
"""

import os
import sys
import platform
import argparse
import subprocess
import shutil
import aiohttp.web
from server.app import create_app

BANNER = """
\033[38;2;0;242;254m   █████╗ ███████╗████████╗██╗  ██╗███████╗██████╗ 
  ██╔══██╗██╔════╝╚══██╔══╝██║  ██║██╔════╝██╔══██╗
  ███████║█████╗     ██║   ███████║█████╗  ██████╔╝
  ██╔══██║██╔══╝     ██║   ██╔══██║██╔══╝  ██╔══██╗
  ██║  ██║███████╗   ██║   ██║  ██║███████╗██║  ██║\033[0m
  \033[38;2;148;163;184m[ High-Speed Multi-Thread Download Accelerator & Glassmorphism UI ]\033[0m
"""

def get_base_dir() -> str:
    """Retorna la ruta base correcta tanto en ejecución normal como empaquetada (PyInstaller)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return getattr(sys, "_MEIPASS")
    return os.path.dirname(os.path.abspath(__file__))

def get_default_download_dir() -> str:
    """Detecta la ruta de descargas estándar según el sistema operativo."""
    user_home = os.path.expanduser("~")
    os_name = platform.system().lower()

    if os_name == "windows":
        downloads = os.path.join(user_home, "Downloads", "Aether")
    elif os_name == "darwin":  # macOS
        downloads = os.path.join(user_home, "Downloads", "Aether")
    else:  # Linux / Unix
        descargas_es = os.path.join(user_home, "Descargas", "Aether")
        downloads_en = os.path.join(user_home, "Downloads", "Aether")
        downloads = descargas_es if os.path.exists(os.path.join(user_home, "Descargas")) else downloads_en

    return downloads

def open_standalone_window(url: str) -> bool:
    """Abre la UI como ventana de aplicación independiente nativa según el SO."""
    os_name = platform.system().lower()

    # --- 1. WINDOWS ---
    if os_name == "windows":
        win_candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for exe in win_candidates:
            if os.path.exists(exe):
                try:
                    subprocess.Popen([exe, f"--app={url}", "--class=AetherAccelerator"])
                    return True
                except Exception:
                    pass
        # Fallback navegador predeterminado Windows
        try:
            os.startfile(url)
            return True
        except Exception:
            return False

    # --- 2. MACOS ---
    elif os_name == "darwin":
        mac_candidates = [
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for exe in mac_candidates:
            if os.path.exists(exe):
                try:
                    subprocess.Popen([exe, f"--app={url}"])
                    return True
                except Exception:
                    pass
        try:
            subprocess.Popen(["open", url])
            return True
        except Exception:
            return False

    # --- 3. LINUX (Wayland / X11) ---
    else:
        linux_candidates = [
            "/opt/brave-origin-bin/brave",
            shutil.which("brave"),
            shutil.which("brave-browser"),
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        ]
        for exe in linux_candidates:
            if exe and os.path.exists(exe):
                try:
                    subprocess.Popen(
                        [exe, f"--app={url}", "--class=AetherAccelerator"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    return True
                except Exception:
                    pass

        if shutil.which("xdg-open"):
            try:
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                pass

    return False

def main():
    parser = argparse.ArgumentParser(description="Aether Download Accelerator — Multiplatform")
    parser.add_argument("--port", type=int, default=8765, help="Puerto del servidor (default: 8765)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--dir", type=str, default=None, help="Directorio de descargas personalizado")
    parser.add_argument("--no-browser", action="store_true", help="No abrir automáticamente el navegador")
    args = parser.parse_args()

    base_dir = get_base_dir()
    ui_dir = os.path.join(base_dir, "ui")

    if args.dir:
        download_dir = os.path.abspath(os.path.expanduser(args.dir))
    else:
        download_dir = get_default_download_dir()

    os.makedirs(download_dir, exist_ok=True)

    os_info = f"{platform.system()} ({platform.machine()})"
    print(BANNER)
    print(f"\033[1;32m✔ Plataforma:\033[0m {os_info}")
    print(f"\033[1;32m✔ Motor Iniciado:\033[0m http://{args.host}:{args.port}")
    print(f"\033[1;34m📁 Carpeta de Descargas:\033[0m {download_dir}")
    print(f"\033[1;35m⚡ Algoritmo:\033[0m Segmentación dinámica (1 a 32 conexiones concurrentes)")
    print(f"\033[1;36m🎨 Interfaz:\033[0m Glassmorphism con 3 Dial Modes (Obsidian, Cyber, OLED)\n")

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        open_standalone_window(url)

    app = create_app(download_dir=download_dir, ui_dir=ui_dir)
    aiohttp.web.run_app(app, host=args.host, port=args.port, print=None)

if __name__ == "__main__":
    main()
