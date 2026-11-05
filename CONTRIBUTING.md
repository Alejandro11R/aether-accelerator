# Contribuyendo a Aether Download Accelerator

¡Gracias por tu interés en colaborar con Aether! Este proyecto se rige por la disciplina de ingeniería del **Clan Tortugas Ninja & Maestro Splinter**:

## 🐢 La Regla de Oro
> **Prohibido romper lo que ya funciona.** Todo cambio debe ser estrictamente aditivo, retrocompatible y validado con tests unitarios antes de integrarse.

---

## 🐀 Los 5 Controles de Calidad de Splinter
1. **Verificación de Interfaces**: No alteres la estructura de datos o los endpoints existentes sin previo aviso.
2. **Cero Regresiones**: Antes de enviar un Pull Request, ejecuta la suite de tests completa:
   ```bash
   python tests/test_engine.py
   ```
3. **Integridad de Datos**: Toda transferencia debe validar el checksum y no perder un solo byte al particionar o reanudar.
4. **Fallback Transparente**: Si un servidor no soporta `Accept-Ranges`, el motor debe degradar limpiamente a descarga de flujo lineal sin lanzar excepciones al usuario.
5. **Estilo Visual Inquebrantable**: Todo cambio en la interfaz gráfica debe respetar los criterios de **Glassmorphism**, espaciado modular y los 3 diales de tema (*Obsidian Glass*, *Cyber Neon*, *OLED Frost*).

---

## 🛠️ Flujo de Trabajo
1. Haz un fork del repositorio.
2. Crea una rama para tu funcionalidad (`git checkout -b feature/nueva-mejora`).
3. Agrega tus pruebas correspondientes en `tests/`.
4. Asegúrate de que todos los tests pasen con éxito.
5. Envía tu Pull Request explicando el impacto y adjuntando capturas de pantalla si modificas la interfaz.
