# Mis finanzas

Mis finanzas reúne los movimientos de BBVA, la cartera de MyInvestor y cripto en un portal local. Puedes revisar gastos y aportaciones, conciliar traspasos y comparar el patrimonio de las tres fuentes a una fecha común.

- [Arrancar el panel](#arrancar-el-panel)
- [Navegar por el dashboard](#navegar-por-el-dashboard)
- [Cargar datos de BBVA](#cargar-otro-extracto) y [preparar la cartera](#dashboard-myinvestor)
- [Conciliar traspasos](#traspasos-de-bbva-a-myinvestor) y [entender el patrimonio conjunto](#patrimonio-conjunto)
- [Ejecutar las pruebas](#pruebas) y [leer el diseño de la integración](INTEGRACION.md)

## Arrancar el panel

Desde la raíz del repositorio, crea el entorno si es la primera vez:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Arranca el servidor:

```powershell
.\.venv\Scripts\python -m bbva_gastos servir
```

Abre el [portal local](http://127.0.0.1:8766). Si la base está vacía y existe `data/movimientos.json`, el panel importa ese histórico al arrancar. Usa `Ctrl+C` para detener el servidor.

Node.js se usa para preparar la exportación de MyInvestor y ejecutar las pruebas de JavaScript. Los gráficos propios de la pestaña **Inversión** usan Chart.js, que se instala con `npm ci --prefix dashboard_myinvestor`. El panel de gastos y el gráfico de patrimonio conjunto funcionan con el servidor de Python.

## Navegar por el dashboard

| Enlace de la barra superior | Qué encontrarás |
| --- | --- |
| [Gastos y ahorro](http://127.0.0.1:8766/) | Evolución mensual, categorías de consumo, movimientos de BBVA y revisión de coincidencias. |
| [Patrimonio conjunto](http://127.0.0.1:8766/#patrimonio) | Saldo BBVA, MyInvestor y cripto a una fecha común, evolución, desglose del cambio y cascada. |
| [Inversión](http://127.0.0.1:8766/inversion) | Vista de MyInvestor y cripto con valoración, rentabilidad, posiciones y aportaciones. |

### Revisar gastos y movimientos

1. Elige un mes en **Periodo** o haz clic en el gráfico mensual de **Evolución**. Puedes alternar entre barras y líneas y mostrar ingresos, consumo o ahorro. **Cobertura por extracto** indica qué fechas respalda cada archivo. Las comparaciones mensuales requieren dos meses completos.
2. Pulsa una barra de **Categorías de consumo** para filtrar la tabla. En **Movimientos** puedes buscar por texto, filtrar por dirección, origen o categoría, activar **Solo por revisar** y ordenar pulsando una cabecera. La tabla ofrece páginas de 25, 50 o 100 filas y calcula el neto sobre todos los resultados del filtro. **Exportar CSV** descarga todos esos resultados en el orden elegido. Los filtros de la tabla actúan sobre la tabla y el CSV. Las tarjetas resumen el periodo mensual.
3. Usa **Editar** en la fila para corregir su categoría. **Conciliación** sirve para revisar duplicados bancarios. **Traspasos BBVA → MyInvestor** enlaza una salida bancaria con una aportación de la cartera. Cada sección muestra sus propias decisiones y permite revertirlas.

**Balance del periodo** suma las entradas y salidas registradas en BBVA. **Consumo** deja fuera el ahorro, los Bizum a personas y los movimientos pendientes de clasificar. Tras importar archivos o cambiar las reglas, pulsa **Actualizar**.

### Comparar el patrimonio entre fechas

1. En **Patrimonio conjunto**, usa **Fecha de corte** y **Consultar** para abrir un cierre concreto. **Última fecha común** vuelve al cierre más reciente que se puede verificar para las tres fuentes. **Ver saldos y traspasos del cierre** muestra de dónde sale cada importe y los ajustes aplicados.
2. En **Evolución del patrimonio**, elige **Periodo del gráfico**. Hay rangos de 1, 3 o 6 meses, este año, 1 año y todo el historial. Este selector controla la evolución, las tarjetas de variación, la cascada y su tabla de fechas. El selector mensual de **Gastos y ahorro** controla los datos bancarios del periodo.
3. Activa o desactiva las series del gráfico y selecciona un punto para consultar ese cierre. **Consultar importes por fecha** ofrece las cifras exactas y permite seleccionar el cierre desde la tabla. Los tramos discontinuos indican huecos entre cierres verificables.
4. Lee **Cascada del patrimonio** de izquierda a derecha. Parte del patrimonio inicial, suma o resta el ahorro neto y las rentabilidades de MyInvestor y cripto, y termina en el patrimonio final. Las fechas son el primer y el último cierre común del rango. Cuando el eje empieza por encima de cero, la cascada indica su valor inicial. Si falta algún cálculo, el panel conserva la evolución y explica la ausencia de la cascada.

### Consultar la cartera

- **Resumen** muestra el patrimonio de MyInvestor y cripto, su evolución y la rentabilidad ponderada por tiempo (TWR). El periodo de esta vista se elige desde su propio selector.
- **Diario** muestra el efecto de mercado en euros o porcentaje, la contribución por activo y la base 100. Tiene su propio selector de periodo.
- **MyInvestor** reúne asignación, geografía, posiciones, comparación con el índice y **Aportaciones y retiradas**. Desde ahí puedes abrir la revisión de traspasos y ver las aportaciones marcadas como conciliadas.
- **Cripto** muestra valoración, coste y compras registradas. **Estado de los datos** indica las pestañas leídas y sus fechas. Después de regenerar la exportación, pulsa **Actualizar**.

El resumen por terminal, sin abrir el navegador:

```powershell
.\.venv\Scripts\python -m bbva_gastos resumen
```

## Cargar otro extracto

En la app de BBVA, descarga «Últimos movimientos» en PDF o en Excel. Dos formas:

```powershell
.\.venv\Scripts\python -m bbva_gastos cargar C:\ruta\al\fichero.pdf
```

o suelta el fichero en `entrada/` y ejecuta:

```powershell
.\.venv\Scripts\python -m bbva_gastos entrada
```

`entrada --vigilar` repite esa lectura cada 30 segundos. Sirve si sincronizas **solo** esa carpeta con Google Drive para escritorio. No pongas un watcher sobre todo Drive: mezclaría ficheros que no son extractos.

Al terminar verás `Lectura: pdf-texto`, `pdf-tabla`, `pdf-ocr`, `excel` o `csv`. Para extraer texto de un PDF escaneado, instala Tesseract con el idioma español, deja `tesseract` en el `PATH` e instala `pytesseract` en el entorno virtual.

Si hay saltos de saldo, el fichero no entra en la base. La cadena no cuadra y clasificarla ensuciaría el panel. Un fichero con el mismo contenido no se inserta dos veces.

La cabecera del Excel o del CSV puede no estar en la primera fila. Valen `Fecha` / `F. Operación`, `Concepto` / `Movimiento`, `Importe`, o columnas separadas de cargo y abono.

## Corregir una categoría

Las reglas viven en `bbva_gastos/categories.py`. Lo que no reconocen queda sin categoría y **fuera del consumo**, para no inventar un gasto.

Para enseñarle un comercio sin tocar código, copia el ejemplo y edítalo:

```powershell
New-Item -ItemType Directory -Force data | Out-Null
Copy-Item correcciones.ejemplo.csv data\correcciones.csv
```

`data/correcciones.csv`:

```csv
patron,categoria
alquiler,vivienda
```

`patron` es una expresión regular, sin distinguir mayúsculas. La categoría tiene que ser una de estas:

- Gastos: `alimentacion`, `cafes_y_restaurantes`, `transporte`, `vivienda`, `salud`, `deporte`, `suscripciones`, `compras`, `ocio`, `viajes`, `ahorro_inversion`, `bizum_personas`, `banco`, `otros`
- Ingresos: `nomina`, `bizum_recibido`, `transferencia_recibida`, `devolucion`, `otros_ingresos`

Una corrección gana a una regla y también a Jev. Recarga el panel: no hace falta reiniciarlo.

## Jev, cuando quieras cubrir el resto

Jev es el modelo de decisiones de TypeSafe. Se llama por OpenRouter, modelo `typesafe/jev-1.14`. No uses la clave contra `api.typesafe.ai`.

1. Crea `secrets/openrouter.key` con la clave en una sola línea. No la pegues en el chat ni en un fichero que se suba a git.
2. Ejecuta:

```powershell
.\.venv\Scripts\python -m bbva_gastos clasificar
```

Jev solo ve los movimientos que las reglas y tus correcciones dejan pendientes. La caché queda en `data/clasificacion_cache.json`. Si la confianza baja de 0,45, el movimiento queda marcado para revisar. `--rehacer` vuelve a preguntar sin reutilizar respuestas de la caché y conserva las categorías anteriores si la llamada falla.

La clasificación por reglas y las correcciones locales funcionan sin clave. Git ignora `secrets/openrouter.key`.

En el panel, **Generar lectura** pide un resumen a `google/gemini-2.5-flash-lite` por OpenRouter. Viajan cifras agregadas, categorías, fechas y recuentos del periodo; no se envían conceptos ni detalles de movimientos. La respuesta se guarda en la base y **Volver a generar** pide otra. Los hechos de debajo salen de la base, sin modelo. Las lecturas anteriores usan otra versión de caché y dejan de mostrarse: se pueden volver a generar con el formato nuevo.

## Sincronización opcional con el banco

Saca saldos y movimientos de BBVA con [Enable Banking](https://enablebanking.com/) (agregador AISP). El acceso es de solo lectura. La clave del banco no se guarda aquí: BBVA pide usuario, clave y un aviso en su app.

### Alta, una sola vez

1. Entra en [enablebanking.com/sign-in](https://enablebanking.com/sign-in/) con tu email. La cuenta se crea en el primer acceso.
2. En [Applications](https://enablebanking.com/cp/applications), crea una app:
   - Environment: **Production** (Sandbox solo tiene un BBVA falso: usuario `user1`, clave `1234`, OTP `012345`).
   - Name: `bbva-gastos`.
   - Redirect URL, exacta: `http://127.0.0.1:8765/callback`
   - Clave: generar en el navegador y exportar la privada.
3. Guarda el `.pem` descargado en `secrets/`, sin renombrarlo. El nombre del archivo es el id de la app.
4. En esa app, pulsa **Activate by linking accounts** y enlaza cada cuenta BBVA que quieras leer. Sin este paso la API devuelve cero cuentas. Este enlace no sustituye al consentimiento de la app: hacen falta los dos.

### Uso

```powershell
.\.venv\Scripts\python -m bbva_gastos check
.\.venv\Scripts\python -m bbva_gastos connect
.\.venv\Scripts\python -m bbva_gastos sync
```

`connect` abre el navegador, espera la vuelta en el puerto 8765 y guarda la sesión en `data/session.json`. `sync` escribe la copia del banco y los mismos apuntes en `data/gastos.sqlite`, que es la base del panel.

El consentimiento dura lo que permita BBVA (hasta unos 90 o 180 días, según devuelva el banco) menos una hora de margen. Cuando caduque, vuelve a ejecutar `connect`.

Cuenta de empresa: `python -m bbva_gastos connect --psu-type business`. BBVA pide, además, el código de empresa de 8 dígitos. Esta vía es la de la cuenta personal.

## Comandos

| Comando | Qué hace |
|---|---|
| `cargar <fichero>` | Lee un PDF, Excel o CSV y lo guarda en la base si el saldo cuadra |
| `saldo-bbva <fecha> <saldo>` | Registra el saldo de cierre en euros de la cuenta indicada |
| `entrada` | Lee los ficheros nuevos de `entrada/` |
| `entrada --vigilar` | Repite esa lectura |
| `resumen` | Totales por mes en la terminal |
| `clasificar` | Pide a Jev lo que sigue pendiente |
| `servir` | Abre el panel en el puerto 8766 |
| `check` / `connect` / `sync` | Enable Banking, cuando tengas el `.pem` |

## Pruebas

```powershell
.\.venv\Scripts\python -m pytest
node --test tests/test_dashboard_ui.cjs tests/test_myinvestor.cjs
```

Las pruebas de JavaScript necesitan Node.js y usan los módulos estándar. Las suites usan datos ficticios y cubren importación, filtros, fechas comunes, conciliación, desglose del patrimonio y respuestas que llegan fuera de orden.

## Dashboard MyInvestor

Con el servidor en marcha, abre [Inversión](http://127.0.0.1:8766/inversion). Exporta las pestañas de tu hoja como CSV a `dashboard_myinvestor/data/`, usando su nombre de pestaña como nombre de fichero (`Historico.csv`, `Crypto.csv`, etc.). Desde la raíz, genera la exportación local:

```powershell
node dashboard_myinvestor/harness.js --out data/patrimonio.json
```

Pulsa **Actualizar** tras generar una nueva exportación. Es una copia de solo lectura; la hoja sigue siendo la fuente y su versión de Apps Script se sigue desplegando como antes. La vista local usa tablas si Chart.js no está instalado. Para activar también los gráficos en local:

```powershell
npm ci --prefix dashboard_myinvestor
```

Consulta [el módulo MyInvestor](dashboard_myinvestor/README.md) y [la revisión del PR e integración](INTEGRACION.md).

## Cobertura y conciliación de BBVA

Si el documento indica el intervalo que cubre, pásalo al cargarlo. Las fechas son inclusivas y pueden incluir días sin movimientos:

```powershell
.\.venv\Scripts\python -m bbva_gastos cargar entrada/enero.csv --desde 2026-01-01 --hasta 2026-01-31
```

Puedes repetir el comando con el mismo fichero para registrar o corregir su intervalo sin duplicar apuntes. Usa `--cuenta` con el mismo identificador estable para todos los extractos de una cuenta; si usas una sola cuenta manual, puedes omitirlo. Para combinar extractos nuevos con Enable Banking, el identificador debe ser el `account_uid` guardado localmente para esa cuenta. Una cobertura sin fechas declaradas queda **desconocida**; un mes es **completo** solo si los intervalos cubren todos sus días para todas las cuentas del panel. **Cobertura por extracto** enseña los intervalos registrados. El histórico anterior conserva sus movimientos y puede registrar cobertura si se conserva el fichero original.

**Conciliación** muestra pares con la misma fecha, importe y divisa de fuentes distintas (extracto/API o dos extractos). Revisa los conceptos y la procedencia. Marca **Son distintos** para descartar el aviso o **Excluir A/B** para retirar ese apunte de cifras y exportaciones. **Restaurar movimiento** revierte la exclusión. Los cargos iguales dentro de un mismo extracto se conservan.

En cada fila, **Editar** cambia solo la categoría de ese movimiento. Para retirar una corrección elige **Usar clasificación automática**. La edición manual tiene prioridad sobre reglas, CSV de correcciones y Jev.

## Traspasos de BBVA a MyInvestor

1. Regenera `data/patrimonio.json` con el comando de MyInvestor de arriba. Incluye `Flujos.csv` si la hoja registra allí las aportaciones. También se leen las aportaciones de `Historico.csv` que ya utiliza el cálculo de cartera.
2. Abre [Traspasos BBVA → MyInvestor](http://127.0.0.1:8766/#traspasos), o entra desde **Aportaciones y retiradas** en Inversión. El panel busca salidas en euros y aportaciones del mismo importe con hasta cinco días naturales de diferencia, incluso si cruzan de mes. Si hay más de un candidato para un apunte, lo indica.
3. Elige **Confirmar traspaso** o **No coinciden**. La decisión se guarda en SQLite y puede deshacerse desde **Decisiones guardadas**. Cada apunte y cada aportación pueden confirmarse una sola vez.

El traspaso confirmado sigue visible en BBVA como salida de caja, dentro de ahorro e inversión y fuera del consumo. MyInvestor marca la aportación existente y la sigue descontando de la rentabilidad una sola vez. La conciliación no crea aportaciones que falten en la hoja ni enlaza importes repartidos en varios apuntes. Si una exportación posterior omite la aportación, el panel señala el enlace para revisión y conserva tu decisión hasta que la deshagas.

## Patrimonio conjunto

En [Patrimonio conjunto](http://127.0.0.1:8766/#patrimonio) verás el saldo de BBVA, MyInvestor y cripto para la **última fecha común**. También puedes consultar un cierre anterior con el selector. Se usan las valoraciones efectivas del mismo día; los valores arrastrados de MyInvestor o cripto no cuentan como datos de ese cierre. Si falta alguna fuente, el panel muestra qué falta y deja el total sin calcular.

**Evolución del patrimonio** muestra el total y las series de BBVA, MyInvestor, cripto y ajuste por traspasos. Solo aparecen puntos de cierres completos y verificables; un tramo discontinuo indica que no hay cierres intermedios. Selecciona un punto o una fecha en **Consultar importes por fecha** para abrir el desglose de ese cierre.

El **Periodo del gráfico** se cuenta desde el último cierre común disponible. El desglose compara el primer y el último cierre común del intervalo. Cada rentabilidad se expresa en euros y descuenta las aportaciones y retiradas de su cartera. El ahorro neto es el cambio del patrimonio ajustado por traspasos menos ambas rentabilidades. Si la cartera estima flujos de cripto, el panel señala la estimación. Regenera `data/patrimonio.json` para incluir el historial de flujos que necesita el desglose.

Para BBVA se usa la columna **Saldo** del extracto. Si hay varios apuntes del día, deben formar una cadena de saldos que permita identificar el cierre. Un saldo anterior solo se mantiene si los extractos cubren los días intermedios y no hay ningún movimiento posterior hasta el cierre. La suma exige un saldo para todas las cuentas importadas. Usa el mismo `--cuenta` para cada fuente de una misma cuenta para evitar tratarla como dos cuentas distintas.

Si el banco te da el saldo de cierre pero tu exportación de movimientos no lo incluye, puedes registrarlo directamente. Usa el importe que indique el banco para esa fecha, no el balance de ingresos y salidas del dashboard. Ejemplo:

```powershell
.\.venv\Scripts\python -m bbva_gastos saldo-bbva 2026-02-03 1234.56
```

Si importaste esa cuenta con `--cuenta`, añade el mismo identificador al registrar el saldo. Repetir el comando para la misma fecha y cuenta actualiza el cierre guardado.

Fuera de las fechas intermedias del traspaso, el total suma los tres saldos sin añadir las aportaciones. Si la salida ya figura en BBVA y el abono todavía no figura en MyInvestor, el tramo intermedio suma el dinero en tránsito. Si el abono figura primero, resta el importe mientras esté reflejado en ambos saldos. El desglose identifica las dos fechas y el ajuste de cada traspaso conciliado. Si el enlace no se puede verificar en la exportación actual y afecta al día consultado, el panel deja el total pendiente de revisión.

## Datos locales y estructura del repositorio

| Ruta | Contenido |
| --- | --- |
| `bbva_gastos/` | Servidor, importación, base SQLite, conciliación y cálculo de patrimonio. La interfaz local está en `bbva_gastos/web/`. |
| `dashboard_myinvestor/` | Cálculo de cartera en `Code.gs`, vista en `Index.html` y exportador de CSV a JSON en `harness.js`. |
| `data/` | Base `gastos.sqlite`, exportación `patrimonio.json`, reglas personales y datos de sesión. |
| `entrada/` y `dashboard_myinvestor/data/` | Extractos y CSV de origen para la importación local. |
| `secrets/` y `tests/` | Claves locales y pruebas con datos ficticios, respectivamente. |

Git ignora los extractos de `entrada/`, los directorios de datos, las claves y los artefactos de prueba. El [diseño de la integración](INTEGRACION.md) describe las reglas de conciliación. [ANALISIS.md](ANALISIS.md) recoge el estado de la primera iteración como referencia histórica.
