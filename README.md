# bbva-gastos

Pipeline para la cuenta personal de BBVA en España. Lees un extracto, se guarda en una base local, unas reglas lo clasifican y un panel enseña la evolución. El panel no lleva cifras escritas: cada visita pregunta a la base.

```
PDF, Excel o CSV
        │
        ▼
   entrada/  o  cargar
        │
        ▼
 data/gastos.sqlite          ← la fuente del panel
        │
        ├─ reglas de categories.py
        ├─ tus correcciones (data/correcciones.csv)
        └─ Jev, solo para lo que sigue pendiente
        │
        ▼
 http://127.0.0.1:8766
```

El ahorro (MyInvestor y similares) y los Bizum a personas no entran en el consumo. Un mes es incompleto cuando el extracto no cubre el día 1 y el último día de ese mes. Eso se calcula con las fechas guardadas.

La carpeta `data/` no se sube a git. Tampoco la clave ni el PDF.

## Arrancar el panel

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m bbva_gastos servir
```

Abre http://127.0.0.1:8766. Si la base está vacía y ya existe `data/movimientos.json`, el panel lo importa la primera vez. Para parar el panel: Ctrl+C.

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

Al terminar verás `Lectura: pdf-texto`, `pdf-tabla`, `pdf-ocr`, `excel` o `csv`. Si el PDF trae texto, se usa el texto. Si es una foto o un escaneo y no hay texto ni tabla, hace falta Tesseract (más abajo).

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

Jev solo ve los movimientos que las reglas y tus correcciones dejan pendientes. La caché queda en `data/clasificacion_cache.json`. Si la confianza baja de 0,45, el movimiento queda marcado para revisar. `--rehacer` olvida las respuestas anteriores de Jev y vuelve a preguntar.

Sin la clave, `clasificar` no llama a nadie: el panel sigue mostrando lo que ya clasifican las reglas. La clave va en `secrets/openrouter.key`, una línea, y ese fichero no se sube a git.

En el panel, **Generar lectura** pide un resumen a `google/gemini-2.5-flash-lite` por OpenRouter. Solo viajan los totales del periodo. La respuesta se guarda en la base y **Volver a generar** pide otra. Los hechos de debajo salen de la base, sin modelo.

La gráfica cambia entre barras y líneas, enciende o apaga ingresos, consumo y ahorro, y al pulsar un mes filtra el periodo. La tabla ordena por columna y filtra por texto, dirección, origen y categoría en el navegador.

## Qué tienes que hacer tú

Esto no lo puedo hacer desde el repo:

1. **Abrir el panel** en http://127.0.0.1:8766 con el comando `servir` de arriba. La primera vez importa el extracto que ya está en `data/movimientos.json`, si la base está vacía.
2. **Guardar la clave de OpenRouter** en `secrets/openrouter.key` si en esa máquina todavía no está. Sin el fichero, Jev y la lectura no llaman al modelo.
3. **Revisar lo marcado «revisar»** en la tabla (origen Jev con poca confianza) y, si una categoría no te encaja, añadir el patrón a `data/correcciones.csv`. Una corrección gana a Jev.
4. **Soltar el siguiente extracto** en `entrada/` o pasarlo a `cargar`. El PDF original no está en el repo y no hace falta volver a cargarlo si el JSON ya está importado.
5. **Tesseract, solo si un PDF futuro no tiene texto.** Este extracto se leyó como `pdf-texto` y no usa OCR. Instala Tesseract para Windows con el idioma español, deja `tesseract` en el PATH y luego:

```powershell
.\.venv\Scripts\python -m pip install pytesseract
```

6. **Enable Banking, solo si más adelante quieres el sync sin descargar el fichero.** Hace falta tu alta, tu consentimiento en la app de BBVA y un `.pem` que solo puedes bajar tú. Los pasos están en la sección siguiente. No hay clave de Enable Banking en `secrets/`.
7. **No mezcles a ciegas** el mismo periodo del PDF y del sync del banco. El PDF identifica cada apunte por fecha, importe y saldo. El banco lo identifica por su id. El mismo movimiento puede aparecer dos veces.

## Más adelante: sync con el banco

Saca saldos y movimientos de BBVA con [Enable Banking](https://enablebanking.com/) (agregador AISP). El acceso es de solo lectura. La clave del banco no se guarda aquí: BBVA pide usuario, clave y un aviso en su app.

GoCardless Bank Account Data no admite altas nuevas. Enable Banking deja una app de producción restringida, gratis, limitada a las cuentas que enlaces tú.

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
| `entrada` | Lee los ficheros nuevos de `entrada/` |
| `entrada --vigilar` | Repite esa lectura |
| `resumen` | Totales por mes en la terminal |
| `clasificar` | Pide a Jev lo que sigue pendiente |
| `servir` | Abre el panel en el puerto 8766 |
| `check` / `connect` / `sync` | Enable Banking, cuando tengas el `.pem` |

## Pruebas

```powershell
.\.venv\Scripts\python -m pytest
```

## Dashboard MyInvestor

El dashboard de la cartera MyInvestor + cripto (Google Apps Script) vive en [`dashboard_myinvestor/`](dashboard_myinvestor/README.md).
