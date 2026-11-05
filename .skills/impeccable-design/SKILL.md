---
name: impeccable-design
description: Live Visual Editor y auditoría de consistencia de espaciado, dials de temas y fidelidad de píxeles.
---

# Impeccable Design — Live Visual Architecture

Esta skill exige una auditoría visual estricta para eliminar inconsistencias en espaciados, colores redundantes y desalineaciones de píxeles.

## Reglas Operativas
1. **El Principio del Dial**:
   - Ofrecer siempre 3 opciones o diales de estilo visual (ej. *Obsidian Glass*, *Cyber Neon*, *Pure Frost*) intercambiables en vivo para permitir al usuario seleccionar la tonalidad que mejor combine con su compositor de ventanas (Hyprland).
2. **Consistencia de Espaciado (Layout Tokens)**:
   - Utilizar escala modular de espaciado estricta: `4px`, `8px`, `16px`, `24px`, `32px`, `48px`.
   - Prohibido mezclar valores arbitrarios como `13px` o `27px`.
3. **Auditoría de Paleta (Color Reduction)**:
   - Evitar tener múltiples tonos de gris inconexos. Definir variables CSS centrales: `--bg-base`, `--bg-surface`, `--bg-glass`, `--border-subtle`, `--accent-primary`.
4. **Validación de Componentes**:
   - Cada tarjeta debe respirar con padding de mínimo `24px` (`1.5rem`).
   - Las esquinas redondeadas (`border-radius`) deben armonizar entre contenedores padre e hijos (radio padre = radio hijo + padding).
