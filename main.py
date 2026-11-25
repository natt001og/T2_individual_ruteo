#!/usr/bin/env python3
"""Orquestador de resiliencia sísmica: probabilidades, ATTR y visor Leaflet.

Ejecución sobre una BD ya restaurada:
    python3 main.py

Crear una BD nueva y cargar main.sql:
    python3 main.py --restaurar

La restauración nunca sobrescribe una base existente. PostGIS/pgRouting se
crean como administrador del servidor; el dump se restaura como PGUSER para
que las tablas y funciones queden bajo propiedad del usuario de aplicación.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg2
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
SQL_MODEL = ROOT / "sql" / "01_modelo_falla_2010.sql"


def env_config():
    return {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5433")),
        "dbname": os.getenv("PGDATABASE", "t2_indv_prueba"),
        "user": os.getenv("PGUSER", "natt"),
        "password": os.getenv("PGPASSWORD"),
    }


def connect():
    cfg = env_config()
    return psycopg2.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"]
    )


def restore_database():
    """Crea una BD vacía, instala extensiones y restaura main.sql como PGUSER."""
    cfg = env_config()
    dump = ROOT / "main.sql"
    if not dump.is_file():
        raise SystemExit("No existe main.sql. Sigue la sección de generación del README.md.")

    # Se restringen identificadores porque se interpolan en una consulta SQL
    # ejecutada por el usuario administrador del servidor.
    for key in ("dbname", "user"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cfg[key]):
            raise SystemExit(f"{key} debe contener solo letras, números y guion bajo; iniciar con letra o _. ")

    admin_env = os.environ.copy()
    check = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-p", str(cfg["port"]),
         "-d", "postgres", "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname = '{cfg['dbname']}'"],
        capture_output=True, text=True, env=admin_env
    )
    if check.returncode:
        raise SystemExit(check.stderr.strip() or "No se pudo consultar PostgreSQL como administrador.")
    if check.stdout.strip() == "1":
        raise SystemExit(
            f"La base {cfg['dbname']} ya existe. No se sobrescribió. "
            "Selecciona otro PGDATABASE para una restauración nueva."
        )

    subprocess.run(
        ["sudo", "-u", "postgres", "createdb", "-p", str(cfg["port"]),
         "-O", cfg["user"], cfg["dbname"]], check=True
    )
    subprocess.run(
        ["sudo", "-u", "postgres", "psql", "-p", str(cfg["port"]),
         "-d", cfg["dbname"], "-v", "ON_ERROR_STOP=1", "-c",
         "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS pgrouting;"],
        check=True
    )

    # Importante: restaurar como el usuario de aplicación evita que las
    # tablas y funciones queden perteneciendo a postgres.
    restore_env = os.environ.copy()
    if cfg["password"]:
        restore_env["PGPASSWORD"] = cfg["password"]
    subprocess.run(
        ["psql", "-h", cfg["host"], "-p", str(cfg["port"]),
         "-U", cfg["user"], "-d", cfg["dbname"],
         "-v", "ON_ERROR_STOP=1", "-f", str(dump)],
        check=True, env=restore_env
    )
    print(f"Base {cfg['dbname']} restaurada correctamente.")


def validate_database(conn):
    expected = {
        "red_integrada_aristas_conectada_v1": 74450,
        "red_integrada_nodos_clave_v2": 37,
        "pga_enlaces_2010": 74450,
        "terremoto_2010": 1,
    }
    with conn.cursor() as cur:
        for table, count_expected in expected.items():
            cur.execute(f"SELECT COUNT(*) FROM public.{table}")
            count_found = cur.fetchone()[0]
            if count_found != count_expected:
                raise RuntimeError(
                    f"{table}: se esperaban {count_expected} registros y se encontraron {count_found}."
                )
        cur.execute("""
            SELECT COUNT(*) FROM public.pga_enlaces_2010
            WHERE id_arista = 74450 AND pga_maxima IS NOT NULL
        """)
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Falta el PGA del enlace sintético 74450 en pga_enlaces_2010.")
    conn.commit()
    print("Validación de base de datos: 74.450 aristas, 37 nodos y 74.450 PGA agregados.")


def run_model(conn):
    """Ejecuta el SQL en la conexión abierta, sin reingresar a su contexto."""
    sql = SQL_MODEL.read_text(encoding="utf-8")
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print("\nModelo de falla ejecutado y probabilidades actualizadas.")


def run_attr_scenarios():
    cfg = env_config()
    env = os.environ.copy()
    env.update({"PGHOST": cfg["host"], "PGPORT": str(cfg["port"]),
                "PGDATABASE": cfg["dbname"], "PGUSER": cfg["user"]})
    for scenario in ("pre_terremoto", "post_optimista", "post_conservador"):
        cmd = [sys.executable, str(ROOT / "calcular_attr.py"),
               "--escenario", scenario, "--iteraciones", "5000",
               "--host", cfg["host"], "--puerto", str(cfg["port"]),
               "--base", cfg["dbname"], "--usuario", cfg["user"]]
        subprocess.run(cmd, check=True, env=env)


app = Flask(__name__, static_folder=str(WEB))


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/<path:filename>")
def web_asset(filename):
    if filename not in {"app.js", "style.css"}:
        return ("Not found", 404)
    return send_from_directory(WEB, filename)


@app.get("/api/edges")
def api_edges():
    bbox = request.args.get("bbox")
    where, params = "", []
    if bbox:
        try:
            minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
            where = "WHERE a.geom && ST_MakeEnvelope(%s,%s,%s,%s,4326)"
            params = [minx, miny, maxx, maxy]
        except (TypeError, ValueError):
            return jsonify({"error": "bbox debe ser minLon,minLat,maxLon,maxLat"}), 400
    sql = f"""
        SELECT a.id, a.origen, a.tipo_enlace, a.es_sintetico,
               p.pga_maxima, p.prob_falla, COALESCE(p.nivel_riesgo,'SIN_ESTIMACION'),
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(a.geom, 0.00002))
        FROM public.red_integrada_aristas_conectada_v1 a
        LEFT JOIN public.probabilidades_falla_2010 p ON p.id_arista = a.id
        {where}
        ORDER BY a.id
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        features = []
        for row in cur.fetchall():
            if not row[7]:
                continue
            features.append({
                "type": "Feature", "geometry": json.loads(row[7]),
                "properties": {"id_arista": row[0], "origen": row[1],
                    "tipo_enlace": row[2], "es_sintetico": bool(row[3]),
                    "pga_maxima": row[4], "prob_falla": row[5],
                    "nivel_riesgo": row[6]}
            })
    return jsonify({"type": "FeatureCollection", "features": features})


@app.get("/api/nodes")
def api_nodes():
    sql = """
        SELECT id_vertice, rol, tipo_nodo, tipo_instalacion, grado,
               id_componente, ST_AsGeoJSON(geom)
        FROM public.red_integrada_nodos_clave_v2 ORDER BY id_vertice
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        features = [{
            "type": "Feature", "geometry": json.loads(r[6]),
            "properties": {"id_vertice": r[0], "rol": r[1], "tipo_nodo": r[2],
                           "tipo_instalacion": r[3], "grado": r[4],
                           "id_componente": r[5]}
        } for r in cur.fetchall()]
    return jsonify({"type": "FeatureCollection", "features": features})


@app.get("/api/epicenter")
def api_epicenter():
    sql = """
        SELECT mag, title, ST_Z(geom),
               ST_AsGeoJSON(ST_Transform(ST_Force2D(geom),4326))
        FROM public.terremoto_2010 ORDER BY ogc_fid LIMIT 1
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return jsonify({"type": "FeatureCollection", "features": []})
    return jsonify({"type": "Feature", "geometry": json.loads(row[3]),
                    "properties": {"magnitud": row[0], "evento": row[1],
                                   "profundidad_km": row[2]}})


@app.get("/api/summary")
def api_summary():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*), COUNT(prob_falla),
                   COUNT(*) FILTER (WHERE prob_falla IS NULL),
                   COUNT(*) FILTER (WHERE nivel_riesgo='ALTO'),
                   COUNT(*) FILTER (WHERE nivel_riesgo='MEDIO'),
                   COUNT(*) FILTER (WHERE nivel_riesgo='BAJO')
            FROM public.probabilidades_falla_2010
        """)
        edge = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM public.red_integrada_nodos_clave_v2")
        nodes = cur.fetchone()[0]
        cur.execute("""
            SELECT escenario, attr, ic95_inferior, ic95_superior
            FROM public.attr_integrada_resultados_v2
            WHERE escenario IN ('pre_terremoto','post_optimista','post_conservador')
            ORDER BY CASE escenario WHEN 'pre_terremoto' THEN 1
                 WHEN 'post_optimista' THEN 2 ELSE 3 END
        """)
        attr = [{"escenario": r[0], "attr": r[1],
                 "ic95_inferior": r[2], "ic95_superior": r[3]}
                for r in cur.fetchall()]
    return jsonify({"aristas": edge[0], "con_estimacion": edge[1],
                    "sin_estimacion": edge[2], "riesgo_alto": edge[3],
                    "riesgo_medio": edge[4], "riesgo_bajo": edge[5],
                    "nodos": nodes, "attr": attr})


def main():
    parser = argparse.ArgumentParser(description="Flujo de resiliencia sísmica y mapa Leaflet")
    parser.add_argument("--restaurar", action="store_true",
                        help="Crea una BD nueva y restaura main.sql; no sobrescribe bases existentes.")
    parser.add_argument("--sin-mapa", action="store_true",
                        help="Ejecuta el modelo y ATTR sin iniciar Leaflet.")
    parser.add_argument("--host-web", default="127.0.0.1")
    parser.add_argument("--puerto-web", type=int, default=5000)
    args = parser.parse_args()

    if args.restaurar:
        restore_database()

    with connect() as conn:
        validate_database(conn)
        run_model(conn)

    run_attr_scenarios()
    print("\nFlujo completado: modelo de falla y ATTR pre/post calculados.")
    if not args.sin_mapa:
        print(f"Mapa disponible en http://{args.host_web}:{args.puerto_web}")
        app.run(host=args.host_web, port=args.puerto_web, debug=False)


if __name__ == "__main__":
    main()
