# Dashboard MyInvestor + cripto (Google Apps Script)

Dashboard web de solo lectura sobre la hoja de cartera. La hoja es la base de datos; el dashboard la lee y hace los cálculos.

- `Code.gs`: servidor Apps Script (`doGet`) y cálculo puro (`computeDashboard_`).
- `Index.html`: la página (Chart.js por CDN).
- `harness.js`: arnés Node para previsualizar y probar el cálculo con CSV exportados de la hoja en `data/` (no se suben a git).
- `INSTRUCCIONES.md`: instalación, despliegue y uso.

Antes de desplegar, sustituye `TU_SPREADSHEET_ID` en `Code.gs` por el ID de tu hoja.
