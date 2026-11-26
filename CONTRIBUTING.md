# Contribuyendo a Aether Download Accelerator

¡Gracias por tu interés en colaborar con Aether! Este proyecto prioriza la estabilidad del motor, la seguridad en loopback y una interfaz ágil en Linux, Windows y macOS.

## Principio Base
> **Prohibido romper lo que ya funciona.** Todo cambio debe mantener la integridad de los datos, respetar los contratos existentes de API/WebSocket y validarse con pruebas automatizadas antes de integrarse.

---

## Estándares de Calidad
1. **Verificación de Interfaces**: Mantén compatibilidad con los endpoints REST, mensajes WebSocket (`core/manager.py`, `server/app.py`) y la integración con la extensión MV3 (`extension/`).
2. **Suite de Pruebas Completa**: Antes de enviar un Pull Request, ejecuta todas las pruebas locales sin advertencias:
   ```bash
   python -W default -m unittest discover -s tests -v
   ```
3. **Integridad de Datos**: Toda transferencia segmentada o de flujo único debe conservar exactamente los bytes descargados al pausar, reanudar, validar `ETag` o recuperarse de errores transitorios y de disco (`OSError`).
4. **Fallback Transparente**: Si un servidor no soporta `Accept-Ranges` o responde `200 OK`, el motor debe degradar limpiamente a descarga lineal sin corromper archivos previos ni lanzar excepciones no controladas.
5. **Seguridad Local e Interfaz Static-Glass**:
   - No utilices `innerHTML`, manejadores inline (`onclick=`) ni concatenación de HTML con datos remotos en `ui/app.js`; usa creación segura de nodos (`textContent`).
   - Respeta la validación de `Host`, `Origin`, `Sec-Fetch-Site`, rutas confinadas y nombres de archivo en `core/security.py` y `server/app.py`.
   - Conserva el sistema visual *Static-Glass* y los 3 diales de tema (*Obsidian Glass*, *Cyber Neon*, *OLED Frost*), sin animaciones infinitas ni halos pesados en reposo.
   - Asegura compatibilidad multiplataforma en **Linux**, **Windows** y **macOS**.

---

## Flujo de Trabajo
1. Haz un fork del repositorio.
2. Crea una rama para tu funcionalidad o corrección (`git checkout -b feature/nueva-mejora`).
3. Agrega primero pruebas que reproduzcan el bug o validen la nueva funcionalidad en `tests/`.
4. Verifica que toda la suite (`unittest discover -s tests -v`) pase correctamente.
5. Envía tu Pull Request explicando el cambio, el impacto y capturas si modificaste la interfaz.
