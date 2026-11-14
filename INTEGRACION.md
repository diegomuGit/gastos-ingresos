# Integración de Mis finanzas

## Revisión del PR #1

El [PR #1, Dashboard MyInvestor v4](https://github.com/diegomuGit/gastos-ingresos/pull/1), se incorporó en `2f57186`. Añadía un servidor de Google Apps Script, un dashboard de MyInvestor y cripto y un arnés de cálculo en Node. La separación entre lectura de Sheets y cálculo puro ha permitido reutilizar esa vista en el portal sin duplicar cálculos.

| Hallazgo | Resultado |
| --- | --- |
| Si faltaba el coste de cripto, se usaba una cifra fija de una cartera concreta. También se podía usar el coste de operaciones posteriores a la fecha de valoración. | El coste sale de la valoración o del historial hasta esa fecha; si no se conoce, la plusvalía queda sin dato. Se añadieron casos sintéticos para ambos comportamientos. |
| El README remitía a scripts de pruebas y archivos que no se incluyeron en el PR. El arnés no creaba la carpeta de salida y podía aceptar un directorio sin CSV. | Se documentan los comandos disponibles en el repositorio y se comprueban tanto la entrada como la salida. |
| La vista dependía de `google.script.run`, aunque el cálculo ya podía ejecutarse fuera de Google. | La misma vista admite un snapshot privado de solo lectura servido en `/api/inversion`. La ruta de Apps Script sigue disponible. |

El PR se revisó con el código que llegó a `main` y pruebas sintéticas. No se validaron cálculos contra la hoja de cartera real ni se cambió su despliegue. El código del PR original ya forma parte del historial público; los cambios locales no reescriben ese historial.

## Primera etapa de la fusión

- **Gastos y ahorro (`/`):** movimientos BBVA en SQLite, cobertura declarada, conciliación y categorías manuales.
- **Inversión (`/inversion`):** cálculos de `dashboard_myinvestor/Code.gs` a partir de una copia privada de las pestañas; el resultado calculado se guarda en `data/patrimonio.json`.
- **En común:** servidor, navegación y estilos del portal, instrucciones y pruebas desde la raíz. Las categorías y la conciliación se guardan en tablas/columnas aditivas. Las fuentes y los cálculos siguen separados.

Los posibles duplicados se detectan por fecha, importe firmado y divisa entre fuentes. Si se conocen las dos cuentas, deben coincidir. Solo una decisión explícita excluye un movimiento; las coincidencias que se desplazan a otra fecha o tienen otro importe requieren otra forma de conciliación. Las cifras incluyen los pares pendientes hasta que se revisen. Los archivos sin saldo se mantienen separados a falta de identidad fiable, incluso si dos exportaciones solapadas producen candidatos.

La cobertura requiere el intervalo que declara el extracto, con fecha inicial y final inclusivas. Sin esa información, ni los apuntes existentes ni una consulta al banco demuestran cobertura. La importación conserva cargos repetidos y valida que las fechas de las filas encajen en el intervalo.

## Segunda etapa: traspasos BBVA → MyInvestor

El catálogo de aportaciones procede de la misma lista de flujos que recibe el cálculo de rentabilidad, después de combinar `Flujos` y `Historico`. Abarca todo el histórico, aunque la tabla de inversión muestre solo las últimas 100 entradas. El enlace se guarda en SQLite, no en Google Sheets ni en la exportación. Cambiar o quitar la aportación de origen puede dejar un enlace por verificar; el panel muestra ese estado y permite deshacerlo.

La confirmación aplica una clasificación reversible en BBVA y añade una marca en Inversión. Conserva los dos apuntes, el importe original y la fecha de abono usada por MyInvestor para la rentabilidad. Por ejemplo, si de una subida de patrimonio de 260 € corresponden 250 € al traspaso, la ganancia de mercado sigue siendo 10 €.

## Patrimonio conjunto

`/api/patrimonio` combina cierres en euros a una fecha de calendario. La serie histórica de cartera distingue valoraciones de valores arrastrados mediante `miFill` y `crFill`. El cierre de BBVA procede de un saldo registrado o del saldo final de una cadena de movimientos. Si arrastra un cierre sin movimientos, exige cobertura continua de esa cuenta. Un saldo ausente nunca se considera cero.

Después de sumar las tres fuentes, se aplica `importe × (salida reflejada − abono reflejado)` para cada enlace confirmado y verificado. Esto añade el dinero en tránsito o elimina el solapamiento temporal; el ajuste es cero antes de ambos movimientos y desde el día en que los dos están reflejados. No modifica saldos de origen ni rentabilidad. Si las fechas de un enlace no son verificables y podrían cambiar el cierre, se muestra el desglose sin total.

`/api/patrimonio/evolucion` reutiliza ese cierre para cada fecha con valoraciones de MyInvestor y cripto. Solo devuelve los puntos con todos los saldos y ajustes verificables, en orden cronológico. El servidor prepara los saldos de BBVA y los ajustes por día una vez por consulta. El gráfico distingue huecos y no añade valoraciones intermedias.

El parámetro `periodo` acepta `1m`, `3m`, `6m`, `ytd`, `1a` y `todo`. El rango termina en el último cierre común verificable. La variación en euros compara el primer y último cierre dentro del rango. El exportador guarda los flujos netos de MyInvestor y cripto por fecha de valoración en `flujosPatrimonio`, incluidos los días que quedan fuera del gráfico conjunto por falta de saldo bancario. La rentabilidad de cada cartera es su cambio de valor menos esos flujos y el ahorro neto es la diferencia entre la variación conjunta y la suma de ambas rentabilidades.

La cascada representa ese mismo desglose entre los dos cierres. Si faltan flujos para calcular alguna contribución, se conserva la evolución y se indica qué parte queda sin datos. Las exportaciones antiguas permiten ver la evolución y se pueden regenerar desde los CSV para incorporar el desglose.
