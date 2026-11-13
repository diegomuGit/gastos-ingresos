# Entrada

Suelta aquí el PDF de «Últimos movimientos», o el mismo dato en Excel o CSV.

Luego, desde la raíz del repo:

```powershell
.\.venv\Scripts\python -m bbva_gastos entrada
```

Si el saldo no cuadra, el fichero no entra en la base. Un fichero idéntico no se carga dos veces.

Para dejarlo mirando esta carpeta:

```powershell
.\.venv\Scripts\python -m bbva_gastos entrada --vigilar
```

Si usas Google Drive para escritorio, sincroniza solo esta carpeta. No vigiles todo Drive.
