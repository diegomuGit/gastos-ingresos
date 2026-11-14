# Dashboard «Patrimonio · MyInvestor + cripto» — v4 (Google Apps Script)

La hoja **MyInvestor Cartera** es solo la base de datos. El dashboard la lee y hace todos los cálculos; nunca escribe en ella.

Novedades de la v4: sección **Rendimiento diario** con barras de cada día (solo efecto mercado), **drivers** de la rentabilidad (contribución por activo enlazada con Carino y base 100 por activo), periodo «1 día», BTC aparte con los fines de semana sumados al lunes, y todo el cálculo separado en funciones puras portables.

## Instalar (primera vez)
1. Abre la hoja y ve a **Extensiones > Apps Script**.
2. En `Código.gs` (o `Code.gs`), borra lo que haya y pega el contenido de **Code.gs**.
3. Pulsa **+ > HTML**, ponle de nombre **Index** (sin .html) y pega el contenido de **Index.html**.
4. Guarda (Ctrl/Cmd + S).
5. **Implementar > Nueva implementación** > engranaje > **Aplicación web**. Ejecutar como: **Yo**. Quién tiene acceso: **Solo yo**.
6. Pulsa **Implementar**, concede los permisos de tu propio proyecto y guarda en marcadores la **URL de la aplicación web**: ese es tu dashboard.

**Permisos.** La v4 no pide ninguno nuevo: igual que la v3, solo usa `SpreadsheetApp` (lectura de la hoja) y `HtmlService` (servir la página). `doGet` no cambia. Si en el proyecto existe `appsscript.json`, no hace falta tocarlo.

## Actualizar la implementación que ya tienes (v3 → v4)
1. En Apps Script, sustituye todo el contenido de `Code.gs` y de `Index` por los nuevos y guarda.
2. **Implementar > Gestionar implementaciones** > lápiz (editar) > Versión: **Nueva versión** > **Implementar**.

La URL no cambia y, al ser los mismos permisos, no debería volver a pedir autorización. (Para probar sin publicar: **Implementar > Implementaciones de prueba**, que usa siempre el último código guardado.)

## Uso
- Se actualiza solo cada 5 minutos (con la pestaña del navegador visible) y con **↻ Actualizar**. Lo que añadas a la hoja aparece sin tocar nada.
- **◐** cambia entre tema claro y oscuro. Arriba, **Resumen · Diario · MyInvestor · Cripto** llevan a cada sección.
- El selector **Periodo de los gráficos** (1 mes, 3 meses, YTD, 1 año, Todo) afecta a la evolución, a la comparación con el MSCI World y al gráfico de la cripto.
- La sección **Rendimiento diario** tiene su propio selector: **1 día · 20 días · 3 meses · YTD**, y un botón **% / €** para el gráfico de barras. Pulsando una barra se ve ese día en «1 día» (las 20 últimas sesiones). Pulsando un activo en los drivers (o en los botones bajo la base 100) se destaca en los gráficos.
- Al final de la página, **Estado de los datos** lista cada pestaña: filas leídas, rango de fechas y observaciones.
- Los gráficos usan Chart.js desde un CDN; si no carga, los datos siguen en las tablas («Ver los datos en tabla») y los drivers siguen visibles.

## Cabecera: PATRIMONIO TOTAL
**PATRIMONIO TOTAL = último `Total` de Historico + último `Valor_EUR` de Crypto.**
- Cada sumando usa su propio último dato. Debajo se ven el importe, el peso y la fecha de cada uno (MyInvestor: oficial del broker o estimación de mercado; cripto: hora del precio).
- Muestra la variación frente al dato anterior (ya sin flujos) y la rentabilidad del total a 7 días, 1 mes, YTD y desde inicio, con su ganancia en euros.
- Si falta uno de los dos bloques, el total es solo el otro y la cabecera lo dice.

## Rendimiento diario y drivers (nuevo en v4)
**Barras de cada día (solo efecto mercado).** Para cada posición: unidades del día anterior × (precio de hoy − precio anterior); la barra de MyInvestor es la suma. Así las aportaciones internas de los jueves (100 € caja → MSCI World cuenta A y 75 € caja → Emergentes) cambian unidades y caja pero **no** la barra, y una compra no cuenta como ganancia.
- **Precios.** Fondos: valor liquidativo (NAV). Acciones: `Valor_EUR / Cantidad`, que ya incluye el efecto divisa de GOOG y MA (USD); ASML cotiza en EUR. El precio en USD y el EURUSD de la nota («Yahoo 281,35 USD», «EURUSD 1,1171») solo se usan para separar en el tooltip la parte de la acción y la del dólar.
- **nav_date.** El movimiento de un fondo se apunta el día en que aparece un VL nuevo (cambia su nav_date). Los fondos irlandeses publican con un día de retraso: la barra de un día junta las acciones de ese día y los fondos con el VL anterior, sin contar nada dos veces. Un día sin VL nuevo, el fondo aporta 0; un VL con nav_date más antigua que la ya usada se ignora (no hay saltos atrás). El tooltip enseña el VL usado por cada fondo.
- **Confirmado o estimado.** Barra oscura = datos del broker; barra clara = estimado (`public_markets` o «NAV est» / VL estimado en la nota). El tooltip dice por qué.
- **Agrupado por ISIN.** Las dos cuentas del MSCI World (A y B) y las dos del Value suman como un solo activo.
- **Total con BTC y BTC aparte.** Una marca negra (—) indica el total con BTC; la línea naranja es lo que aporta el BTC (en % son puntos del total con BTC). El BTC no se mezcla con los drivers de MyInvestor.
- **Fines de semana.** La hoja no tiene filas de sábado ni domingo: el movimiento del BTC del fin de semana se suma al lunes y esa barra se marca con ◆ **«incluye fin de semana»** (tooltip, leyenda y tabla). No se inventan barras. Igual con cualquier día natural sin fila entre dos registros («incluye 1 día laborable sin dato», etc.).

**Qué ha movido MyInvestor (contribución por activo).** Contribución de un activo cada día = peso del día anterior × rendimiento del día (en puntos porcentuales y en euros). Sumadas dan el rendimiento del día; para el periodo se enlazan con **Carino**, así que suman exactamente la TWR del periodo. Barras de más positiva a más negativa, con la **caja** como un activo más que rinde 0 %. Debajo, la **contribución acumulada en el tiempo** (con 20 días, 3 meses o YTD).
- **Residuo.** TWR del día según Historico − suma de contribuciones. Se muestra siempre en la cabecera de los drivers y, si se nota, como barra propia.
- **Sin detalle por posición.** Si una foto de posiciones no cuadra con Historico (Total − posiciones − efectivo), esos días no se reparten por activo: van enteros a «Sin detalle por posición», con el motivo.
- **Cobertura.** Los días sin posiciones se muestran solo como total y la página indica la cobertura disponible.

**Base 100 por activo.** Rendimiento de cada activo, de MyInvestor (TWR) y del BTC con 100 = inicio del periodo. **Enseña rendimiento, no peso**: una acción que pesa un 4 % puede caer un 13 % y restar solo medio punto (lo que suma o resta cada activo está en los drivers).

**Periodo «1 día».** También aparece como primera fila de la tabla «Rentabilidad sin aportaciones».

## Bloque MyInvestor
- Patrimonio, inversiones (fondos y acciones) y efectivo salen de la última fila de **Historico**. Las etiquetas indican la fuente del último dato, la fecha del último dato oficial del broker y la del valor liquidativo.
- Con las **posiciones** (última fecha; si ese día hay filas del broker, solo esas): asignación RV / RF / efectivo frente al objetivo, geografía, tabla de posiciones con variación de 7 días, concentración, plusvalía latente y TER medio.
- Comparación con el benchmark (por defecto MSCI World), aproximado con el valor liquidativo del fondo indexado de la propia cartera que lo replica: no se consulta nada fuera de la hoja.
- Aportaciones y retiradas registradas.

## Bloque Cripto
- Valor, cantidad, precio, coste, plusvalía latente (valor − coste) y peso sobre el patrimonio total; gráfico de valor frente a coste y tabla de compras de Crypto_Movs.
- **Coste**, por este orden: `Coste_EUR` de la valoración mostrada o historial de `Crypto_Movs` hasta ese día. Si falta el coste, la plusvalía queda sin dato.
- Si el historial de compras solo explica parte del saldo, el dashboard muestra cuántas unidades tienen coste registrado.

## Rentabilidad (tal como la calcula Code.gs)
- **TWR (ponderada por tiempo).** Entre cada dos valoraciones consecutivas se aplica Dietz modificado: `r = (V_fin − V_ini − F) / (V_ini + Σ w·f)`, donde `F` son los flujos del tramo y `w` la fracción del tramo, en días, que cada flujo estuvo dentro. Los tramos se encadenan: `(1 + r₁) · (1 + r₂) · … − 1`. Así una aportación o una retirada no cuenta como ganancia ni como pérdida.
- **Periodos.** Todos terminan en el último dato del dashboard (la fecha más reciente entre Historico y Crypto). **1 día**: frente al dato anterior. **1M**: desde el mismo día del mes anterior. **YTD**: desde el 31 de diciembre del año anterior. **Desde inicio**: desde el primer registro. También 7 días, y 3 meses / 1 año cuando el historial los cubre. Se parte de la última valoración con fecha ≤ inicio del periodo; si el historial empieza después, se calcula desde el primer registro y se marca con `*`.
- **Euros.** Ganancia de mercado del periodo = variación del valor − flujos netos. Al pasar el ratón por una celda se ven las fechas, los flujos, el Dietz modificado del periodo completo (ponderado por dinero) y, con 365 días o más, la TWR anualizada.
- **Flujos de MyInvestor.** Pestaña Flujos (aportación +, retirada −; las filas de nota se ignoran) más la columna de aportaciones de Historico, sin duplicar: una aportación de Historico se da por registrada si Flujos tiene el mismo importe (± 0,50 €) entre 5 días antes y 1 después. Si no hay ninguna de las dos, se asume que no hubo flujos y se avisa. Las aportaciones internas de los jueves (caja → fondo) no son flujos: no cambian el patrimonio.
- **Flujos de la cripto.** Entre valoraciones consecutivas de Crypto: si los movimientos de Crypto_Movs explican el cambio de cantidad (tolerancia del 2 %), se usa su importe real en EUR; si no, el flujo se deduce como cambio de cantidad × precio del día.
- **Total y forward-fill.** La serie del total usa la unión de fechas de Historico y Crypto. Los días en que la cripto (o MyInvestor) no tiene dato se arrastra su último valor conocido; en la tabla de evolución se marca con †. Un bloque que empieza a registrarse más tarde entra como aportación por su primer valor, no como ganancia (la cripto, el 12-oct).

## Pestañas que lee
Se leen por **nombre de cabecera**, no por letra de columna: da igual mayúsculas, acentos o sufijos de unidad (`Total` = `Total_EUR`, `Cantidad` = `Cantidad_BTC`, `TER` = `TER_%`), y puede haber filas de texto antes de la cabecera.

| Pestaña | Para qué | Columnas clave |
|---|---|---|
| Historico | Total, efectivo, evolución y TWR de MyInvestor | Fecha, Total (o Fondos / Acciones / Efectivo), source, aportaciones, Nota |
| Stocks + Fondos | Posiciones y vista diaria, en pestañas separadas | Fecha, source, ISIN_Ticker, Cuenta, Valor, Cantidad, NAV, nav_date, Coste, Nota_recuperacion (o Nota) |
| Stocks y Fondos *(opcional)* | Posiciones, en una sola pestaña | las mismas, más Tipo |
| Fondos_Ref | TER, clase, RV / RF, geografía (`Geo_*`), benchmark | ISIN |
| Targets | Objetivos de asignación y benchmark | Categoria, Target_% |
| Flujos | Aportaciones y retiradas | Fecha, Tipo, Importe |
| Crypto | Valoración diaria de la cripto | Fecha, Valor_EUR (o Cantidad × Precio_EUR), Precio_EUR, Cantidad, Coste_EUR, price_time |
| Crypto_Movs *(opcional)* | Compras y ventas: coste, precio medio, flujos | Fecha, Cantidad_BTC, Tipo, Total_EUR, Comision_BTC, Cantidad_acum_BTC |

Para las posiciones vale «Stocks y Fondos», Stocks + Fondos o ambas a la vez: no se duplica nada y el resultado es el mismo. En la nota de cada posición, «NAV est» (o «est 1,32…», «from prior») marca el VL como estimado y «uds … est.» las unidades.

## Si una pestaña falta o está vacía
El resto sigue funcionando y cada hueco se explica con un mensaje:
- **Historico**: sin Total, efectivo ni evolución de MyInvestor. Si hay posiciones, el patrimonio de MyInvestor es su suma (sin efectivo) y la vista diaria sale solo de las posiciones.
- **Posiciones**: sin asignación, geografía, tabla ni concentración; el Total sigue saliendo de Historico y las barras diarias también, pero «sin detalle por posición».
- **Fondos_Ref**: sin TER, clase ni geografía de fondos. **Targets**: objetivo 5 / 80 / 15 y benchmark MSCI World. **Flujos**: se asume que no hubo aportaciones ni retiradas (salvo las de la columna de Historico).
- **Crypto**: el bloque muestra «Sin datos de cripto», el patrimonio total es solo MyInvestor y la vista diaria va sin BTC.
- **Crypto_Movs**: sin detalle de compras; solo se muestra `Coste_EUR` cuando figura en la valoración y los flujos de la cripto se deducen del cambio de cantidad.
- Sin Historico ni Crypto: «Sin datos de patrimonio».

Si faltan Historico o las posiciones, o una pestaña existe pero no se encuentra su cabecera (por ejemplo, una columna clave renombrada), aparece además un aviso arriba y en **Estado de los datos**.

## Cómo está organizado el código (pensando en migrar a Vercel)
- **Code.gs** tiene dos partes separadas por una frontera explícita:
  1. **Adaptador de Apps Script** (`doGet`, `getDashboardData`, `openSpreadsheet_`, `plainValues_`): lo único que toca servicios de Google. Lee las pestañas, convierte las celdas de fecha en texto `yyyy-MM-dd HH:mm` en la zona de la hoja y llama a `computeDashboard_`.
  2. **Cálculo puro** (`computeDashboard_` y todo lo que llama: lectura tolerante, TWR, rendimientos diarios, contribuciones, Carino, base 100…): recibe `{ 'Pestaña': [[celdas]] }` y devuelve un objeto JSON, sin `SpreadsheetApp`, `HtmlService`, `Utilities`, `Session`, `document` ni `window`. Se puede portar a TypeScript tal cual.
- **Index.html** solo pinta: no calcula rentabilidades, contribuciones ni bases 100 (todo llega ya calculado).

## Datos y pruebas en local

Exporta las pestañas como CSV con su nombre (`Historico.csv`, `Crypto.csv`, etc.) a `dashboard_myinvestor/data/`. Desde la raíz del repositorio:

```powershell
node dashboard_myinvestor/harness.js --out data/patrimonio.json
.\.venv\Scripts\python -m bbva_gastos servir
node --test tests/test_myinvestor.cjs
```

Abre `/inversion` en el servidor local y pulsa **Actualizar** después de regenerar el JSON. Los CSV y el JSON se guardan en carpetas ignoradas por Git. La prueba de Node usa datos ficticios. Consulta el README raíz para activar los gráficos locales.
