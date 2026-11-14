# Análisis del proyecto — 14 de noviembre de 2026

> Análisis de la primera iteración. La cobertura por extracto, la conciliación, la edición de categorías y la primera integración de MyInvestor se implementaron después; véase [INTEGRACION.md](INTEGRACION.md).

El proyecto tiene una base adecuada para una herramienta personal local: importadores separados, importes tratados con `Decimal`, persistencia SQLite y reglas de clasificación independientes de los proveedores de IA. La mejora con mayor impacto inmediato es hacer que el dashboard represente todos los datos y permita entender qué cifras están incompletas.

## Estructura y funcionamiento

| Componente | Responsabilidad | Observación |
| --- | --- | --- |
| `statement.py` y `normalize.py` | Lectura de extractos y normalización bancaria | Hay validación de continuidad de saldos; faltan validaciones de fechas reales y moneda. |
| `db.py` | Persistencia, clasificación local y agregaciones | Usa importes decimales; todavía mezcla consultas, importación y reglas en un módulo. |
| `categories.py` y `jev.py` | Reglas y clasificación asistida | Las correcciones ganan; Jev atiende lo pendiente. |
| `lectura.py` | Narración y caché de resúmenes | Ahora usa exclusivamente datos agregados, sin descripciones individuales. |
| `server.py` | HTTP local y API | Suficiente para el uso local actual; concentra la validación de filtros. |
| `web/index.html`, `app.js`, `app.css` | Interfaz sin framework | Se ha separado el JavaScript del HTML para facilitar mantenimiento y pruebas. |

## Mejoras implementadas

| Problema detectado | Cambio | Comprobación |
| --- | --- | --- |
| La tabla descargaba hasta 2.000 movimientos de mayor importe y después filtraba localmente. Podía ocultar movimientos recientes o pequeños. | Filtros y ordenación sobre el periodo entero, paginación y recuentos explícitos. | Caso con 2.051 movimientos, búsqueda del último y total exacto. |
| Una respuesta antigua podía sustituir al periodo o búsqueda actuales. | Cancelación de peticiones y comprobación de su versión antes de dibujar resultados. | Pruebas JavaScript con respuestas deliberadamente desordenadas. |
| Un resumen de IA pendiente podía aparecer bajo otro mes. | La lectura solo se presenta si sigue activo el periodo que la pidió. | Prueba específica de cambio de mes durante la generación. |
| El porcentaje mensual comparaba meses completos e incompletos. | Solo se calcula cuando ambos meses se consideran completos. | Prueba de febrero completo frente a marzo parcial. |
| El resumen enviado a Gemini contenía conceptos y detalles, en contra del README. | Eliminados del contenido enviado y nueva versión de la clave de caché. | Captura del contenido con proveedor simulado. |
| No se podía revisar fácilmente lo pendiente ni conocer el total de los resultados filtrados. | Aviso de cobertura, acceso a revisión, totales de entradas/salidas/neto y limpiar filtros. | API y comprobación funcional en navegador. |
| No había una descarga de los resultados de la búsqueda. | CSV de todas las coincidencias, con formato adecuado para Excel y escape de fórmulas en campos de texto. | Exportación de 55 registros con página de 25 y contenido de prueba que comienza por `=`. |
| Los KPI ocupaban demasiado espacio y «Resultado» podía confundirse con el saldo. | Cuatro tarjetas en escritorio, dos en móvil y etiqueta «Balance del periodo» con explicación. | Comprobación visual de ambas disposiciones. |
| Cada refresco hacía siete peticiones de datos, cada una reaplicando las reglas. | Una petición para el dashboard y otra para la página de movimientos. | Navegación funcional y pruebas HTTP. No se ha realizado un benchmark. |
| `clasificar --rehacer` reutilizaba la caché y borraba categorías antes de contactar con el proveedor. | Omite la caché al rehacer y conserva las clasificaciones anteriores si la petición falla. | Pruebas de caché y fallo del proveedor. |

Se mantienen los endpoints individuales de KPI, categorías, estado, evolución y lectura. `/api/movimientos` conserva la propiedad `movimientos` y añade `total`, `total_periodo`, `offset`, `limit` y `resumen`. Su orden predeterminado pasa a fecha descendente y el máximo por página a 200; los clientes externos deben recorrer las páginas si necesitan todo el periodo.

## Siguientes mejoras priorizadas

1. **Cobertura real por extracto — prioridad alta.** `_complete` infiere continuidad a partir de la primera y última fecha de toda la base. Dos extractos separados pueden hacer que un mes intermedio parezca completo. Guardar cuenta, origen e intervalo de cobertura por importación y distinguir «completo», «parcial» y «cobertura desconocida». Debe probarse un hueco entre dos importaciones y un mes completo sin movimientos en sus extremos.

2. **Identidad por cuenta y conciliación entre fuentes — prioridad alta.** `movement_hash` incluye el identificador externo junto con fecha, importe, saldo y descripción; el mismo apunte de PDF y API puede dar hashes distintos. Además, `to_canonical` descarta `account_uid`. Introducir identidad bancaria por cuenta y origen, y detectar candidatos a duplicado entre extractos y API para revisión. Requiere migración y pruebas con pagos legítimos repetidos; no conviene deduplicarlos automáticamente solo por fecha e importe.

3. **Validación y atomicidad de importaciones — prioridad alta.** `parse_date_value` comprueba formato pero no la existencia del día; `parse_amount` puede aceptar valores no finitos y las agregaciones no separan divisas. Validar fechas con calendario e importes finitos y rechazar monedas no soportadas o separarlas. `import_file` confirma movimientos antes de registrar el fichero: agrupar ambas operaciones en una transacción y presentar errores por fichero para que un documento defectuoso no detenga toda la carpeta.

4. **Corregir categorías desde la tabla — prioridad media.** Permitir corregir un movimiento y, opcionalmente, crear una regla reutilizable. Mostrar qué movimientos afectaría la regla antes de guardarla. Persistir correcciones por identificador con prioridad explícita, conservar historial y ofrecer deshacer. Hoy las correcciones siguen haciéndose mediante CSV.

5. **Agregaciones y lecturas consistentes — prioridad media.** Aunque se reducen las peticiones, `apply_local` y varias agregaciones todavía recorren todos los movimientos; `hechos` repite consultas por mes. Compartir un conjunto de datos por petición, agrupar por mes una vez y tomar una instantánea coherente si se importa en paralelo. Medir con un histórico sintético de varios años antes de introducir caché persistente.

6. **Análisis de consumo — prioridad media.** Presupuestos por categoría, gastos recurrentes, comparación de periodos equivalentes y evolución del consumo excluyendo meses sin cobertura. Primero resolver cobertura y duplicados para que estas métricas sean fiables.

## Validación y límites

- Estado inicial: 23 pruebas de Python correctas.
- Estado final: 45 pruebas de Python y 4 de JavaScript correctas.
- Comprobación en navegador con 246 movimientos ficticios: páginas, búsqueda sin tildes, revisión, filtros de categoría, periodos incompletos, sin resultados, barras/líneas y disposición de escritorio/móvil. Sin errores de JavaScript en la consola observada.
- Sin cambios en la base personal ni llamadas reales a Jev, Gemini o Enable Banking. Los fallos y respuestas del proveedor se simulan en pruebas; su disponibilidad externa no se ha validado.
- La cobertura sigue siendo una estimación y la búsqueda sigue recorriendo los datos en Python. La paginación limita lo que se transmite y dibuja, pero todavía no evita leer el periodo completo en el servidor.
- No se han incorporado nuevas dependencias de producción ni migraciones destructivas. Las lecturas antiguas permanecen almacenadas con su clave previa y dejan de mostrarse hasta generar una nueva.
