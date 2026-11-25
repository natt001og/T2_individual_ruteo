# T2 – Resiliencia de la infraestructura de Internet ante el terremoto del Maule (2010)

Este repositorio contiene el flujo de evaluación de resiliencia de la red integrada: restauración de la infraestructura y topología final, cálculo de probabilidades de falla a partir de PGA derivada del USGS ShakeMap, cálculo de ATTR antes y después del terremoto y visualización en Leaflet.

## 1. Contenido del proyecto

```text
T2_ruteo/
├── main.py
├── main.sql                         # Se genera desde la BD validada (ver sección 4)
├── calcular_attr.py
├── requirements.txt
├── README.md
├── sql/
│   ├── 00_preparar_exportacion.sql
│   └── 01_modelo_falla_2010.sql
└── web/
    ├── index.html
    ├── app.js
    └── style.css
```

`main.sql` almacena la topología final ya construida. En particular, contiene la red conectada de 74.450 aristas y los 37 nodos estratégicos. Por tanto, el flujo de ejecución no vuelve a construir la topología desde los archivos originales ni vuelve a ejecutar `pgr_createTopology`: restaura el resultado topológico validado y continúa con la evaluación sísmica.

## 2. Requisitos

- Ubuntu/Linux.
- PostgreSQL 16.
- PostGIS.
- pgRouting.
- Python 3.10 o superior.
- Las dependencias de `requirements.txt`.

Instalación de paquetes del sistema (los nombres pueden variar según la versión de Ubuntu):

```bash
sudo apt update
sudo apt install postgresql postgresql-client postgresql-16-postgis-3 postgresql-16-pgrouting python3-venv python3-pip
```

Crear y activar el entorno virtual desde la raíz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

No ejecutar `main.py` con `sudo`: las dependencias están instaladas en el entorno virtual del usuario.

## 3. Configurar PostgreSQL

Configurar las variables de conexión en la terminal. Ajustar usuario, puerto y base de datos al entorno local:

```bash
export PGHOST=localhost
export PGPORT=5433
export PGUSER=natt
export PGDATABASE=t2_indv_prueba
export PGPASSWORD='TU_PASSWORD'
```

No guardar la contraseña en el repositorio ni en `README.md`.

El usuario de aplicación debe poder conectarse a PostgreSQL y crear/actualizar tablas en el esquema `public`. Para usar la opción automática `--restaurar`, `main.py` utiliza `sudo -u postgres` para crear una base nueva e instalar las extensiones; luego restaura `main.sql` como el usuario indicado en `PGUSER`, de modo que las tablas y funciones queden bajo su propiedad.

## 4. Generar el archivo `main.sql`

Este paso se realiza una vez, desde una base validada que contenga la infraestructura final y el PGA agregado por enlace. En el desarrollo se utilizó `t2_indv_prueba`.

### 4.1 Completar el PGA del enlace sintético

El enlace troncal sintético tiene el identificador 74450. El siguiente SQL conserva en `pga_enlaces_2010` el PGA interpolado y validado previamente para ese enlace. La operación es repetible (`ON CONFLICT` actualiza el registro):

```bash
psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  -v ON_ERROR_STOP=1 -f sql/00_preparar_exportacion.sql
```

Comprobar que existen 74.450 registros PGA y que el enlace 74450 tiene un valor no nulo:

```sql
SELECT COUNT(*) AS registros_pga FROM pga_enlaces_2010;

SELECT id_arista, pga_maxima, longitud_evaluada_m
FROM pga_enlaces_2010
WHERE id_arista = 74450;
```

El recuento esperado es 74.450. El PGA del enlace 74450 debe ser aproximadamente `0.375142 g`.

### 4.2 Exportar las tablas necesarias

Desde la misma carpeta del proyecto, ejecutar:

```bash
pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  --no-owner --no-privileges --format=plain \
  -t public.red_integrada_aristas_conectada_v1 \
  -t public.red_integrada_nodos_clave_v2 \
  -t public.pga_enlaces_2010 \
  -t public.terremoto_2010 \
  -t public.terremoto_2010_fuente \
  -t public.shakemap_grid_2010 \
  -f main.sql
```

El dump incluye:

- La red conectada final, con geometría, extremos `source`/`target`, origen, tipo de enlace y marca de enlace sintético.
- Los 37 nodos estratégicos y sus atributos.
- El PGA agregado por enlace, incluido el enlace sintético 74450.
- El registro del terremoto y la tabla de procedencia.
- La grilla ShakeMap utilizada como fuente de amenaza.

Las probabilidades de falla y los resultados ATTR se vuelven a generar al ejecutar `main.py`; no es necesario exportar sus tablas de resultados previos.

`main.sql` es un dump SQL plano, sin propietario ni permisos de la máquina de desarrollo. Debe incorporarse al repositorio junto con los scripts.

## 5. Ejecutar el flujo completo en una base ya restaurada

Con las variables de entorno configuradas y la base preparada:

```bash
python3 main.py
```

El programa realiza, en este orden:

1. Valida las tablas y cantidades de registros de la infraestructura final.
2. Ejecuta el modelo de falla y actualiza `probabilidades_falla_2010` y `red_integrada_aristas_conectada_v1.prob_falla`.
3. Calcula el ATTR preterremoto.
4. Calcula el ATTR post-terremoto optimista.
5. Calcula el ATTR post-terremoto conservador.
6. Inicia el servidor Flask que entrega el mapa Leaflet.

El visor queda disponible en:

```text
http://127.0.0.1:5000
```

Para ejecutar los cálculos sin iniciar el mapa:

```bash
python3 main.py --sin-mapa
```

Para utilizar otro puerto o interfaz local:

```bash
python3 main.py --host-web 127.0.0.1 --puerto-web 5001
```

## 6. Restaurar `main.sql` en una base de datos nueva

Para probar la reproducibilidad desde una base vacía, elegir un nombre que todavía no exista. Por ejemplo:

```bash
export PGDATABASE=t2_indv_reproducible
python3 main.py --restaurar
```

La opción `--restaurar`:

1. Comprueba que la base indicada no exista.
2. La crea con propietario `PGUSER`.
3. Instala PostGIS y pgRouting como administrador de PostgreSQL.
4. Restaura `main.sql` como `PGUSER` (no como `postgres`).
5. Continúa con la validación, el modelo de falla, los tres ATTR y el mapa.

Por seguridad, el programa se detiene si la base ya existe. No elimina ni sobrescribe bases existentes. Para repetir la prueba, utilizar otro valor de `PGDATABASE` o eliminar manualmente la base de prueba después de verificar que no contiene información que se necesite conservar.

La restauración requiere que el usuario del sistema pueda ejecutar `sudo -u postgres` y que `PGUSER` tenga permisos de conexión. No ejecutar todo el programa con `sudo`.

## 7. Modelo probabilístico de falla

Se utiliza el modelo simplificado A, con una función de fragilidad lognormal:

\[
P(F\mid PGA)=\Phi\left(\frac{\ln(PGA/\theta)}{\beta}\right),
\qquad \theta=0.30\,g,\quad \beta=0.60.
\]

La PGA utilizada es la agregada por enlace en `pga_enlaces_2010`, derivada de la interpolación de la grilla USGS ShakeMap. Para cada arista se utiliza el máximo PGA agregado (`pga_maxima`) como intensidad de entrada al modelo.

Los parámetros `theta` y `beta` son supuestos simplificados adoptados para el ejercicio; no constituyen una curva de fragilidad calibrada para cada tipo de cable o instalación. Las aristas sin PGA estimable se mantienen con `prob_falla = NULL` y nivel `SIN_ESTIMACION`; no se extrapola una probabilidad fuera de la cobertura útil del ShakeMap.

Los niveles descriptivos se asignan así:

- `BAJO`: probabilidad menor que 0.20.
- `MEDIO`: probabilidad entre 0.20 y menos de 0.50.
- `ALTO`: probabilidad igual o superior a 0.50.
- `SIN_ESTIMACION`: no existe PGA utilizable para el enlace.

## 8. Escenarios ATTR

Se consideran los mismos 37 nodos estratégicos, por lo que se evalúan:

\[
\binom{37}{2}=666
\]

pares de nodos.

- `pre_terremoto`: todas las aristas se consideran operativas (`p_falla = 0`). Se calcula de forma exacta.
- `post_optimista`: los enlaces con probabilidad estimada utilizan su probabilidad calculada; los enlaces sin estimación se consideran operativos (`p_falla = 0`).
- `post_conservador`: los enlaces con probabilidad estimada utilizan su probabilidad calculada; los enlaces sin estimación se consideran fallidos (`p_falla = 1`).

Los escenarios posteriores se estiman mediante Monte Carlo con 5.000 iteraciones y semilla fija 2026. Optimista y conservador son escenarios de sensibilidad frente a la falta de cobertura; no representan estados de falla observados.

## 9. Mapa Leaflet

El visor consume los endpoints locales de Flask:

- `/api/edges`: geometrías de enlaces, tipo, origen, marca sintética, PGA, probabilidad y nivel de riesgo.
- `/api/nodes`: nodos estratégicos, rol, tipo de instalación y atributos.
- `/api/epicenter`: epicentro, magnitud y profundidad.
- `/api/summary`: cantidades de enlaces, niveles de riesgo y resultados ATTR.

El mapa diferencia los enlaces sintéticos, representa los niveles de riesgo y permite consultar atributos de enlaces y nodos. El epicentro se muestra como una capa independiente.

## 10. Resultados de referencia de la ejecución validada

La ejecución validada en el entorno de desarrollo produjo:

| Escenario | ATTR | Método |
|---|---:|---|
| Preterremoto | 74.4745 % | Exacto |
| Postoptimista | 19.8580 % | Monte Carlo, 5.000 iteraciones |
| Postconservador | 0.6523 % | Monte Carlo, 5.000 iteraciones |

El escenario postoptimista considera operativos los enlaces sin estimación; el conservador los considera fallidos. Los resultados pueden variar mínimamente si se modifican los datos, el código, la semilla o el número de iteraciones.

## 11. Consideraciones y limitaciones

- La topología cargada corresponde a la red integrada final ya construida; el dump no reemplaza los archivos fuente ni documenta por sí solo cada operación de integración geométrica.
- El modelo de falla es una simplificación y no incorpora fragilidades diferenciadas por cable aéreo, soterrado o submarino.
- La independencia de fallas entre enlaces es un supuesto de la simulación ATTR; no se modela explícitamente la correlación espacial de daños producidos por un mismo terremoto.
- Los enlaces sin PGA estimable no se consideran fallas observadas. Los escenarios optimista y conservador permiten acotar la sensibilidad del ATTR a esta falta de información.
- La red incluye enlaces sintéticos identificados mediante `es_sintetico`; estos deben distinguirse de la infraestructura observada al interpretar el mapa y los resultados.
