# Dashboard MyInvestor + cripto (Google Apps Script)

Dashboard web de solo lectura sobre la hoja de cartera. La hoja es la base de datos; el dashboard la lee y hace los cálculos.

- `Code.gs`: servidor Apps Script (`doGet`) y cálculo puro (`computeDashboard_`).
- `Index.html`: la página (Chart.js por CDN).
- `harness.js`: exportación local del cálculo con CSV de las pestañas en `data/` (ignorados por Git). Desde la raíz: `node dashboard_myinvestor/harness.js --out data/patrimonio.json`.
- `INSTRUCCIONES.md`: instalación, despliegue y uso.

Antes de desplegar, sustituye `TU_SPREADSHEET_ID` en `Code.gs` por el ID de tu hoja.

Para ver la exportación en el portal local, arranca `python -m bbva_gastos servir` y abre `/inversion`. `npm ci` en esta carpeta instala Chart.js para la vista local. Las pruebas con datos ficticios están en `tests/test_myinvestor.cjs` en la raíz.

El [recorrido por el dashboard](../README.md#navegar-por-el-dashboard) explica cómo alternar entre inversión, gastos y patrimonio conjunto. La exportación incluye los flujos de cartera necesarios para conciliar aportaciones y mostrar el desglose de la cascada.
