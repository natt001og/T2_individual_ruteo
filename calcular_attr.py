#!/usr/bin/env python3
"""Calcula ATTR preterremoto y post-terremoto en tres escenarios.

Escenarios:
  pre_terremoto: todas las aristas operativas (p_falla=0).
  post_optimista: enlaces sin estimacion considerados operativos (p=0).
  post_conservador: enlaces sin estimacion considerados fallidos (p=1).
"""
import argparse
import math
import os
import random
from collections import Counter

import psycopg2


class DSU:
    def __init__(self, n):
        self.parent = list(range(n))
        self.sz = [1] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        x, y = self.find(a), self.find(b)
        if x == y:
            return
        if self.sz[x] < self.sz[y]:
            x, y = y, x
        self.parent[y] = x
        self.sz[x] += self.sz[y]

    def clone(self):
        other = DSU.__new__(DSU)
        other.parent = self.parent.copy()
        other.sz = self.sz.copy()
        return other


def pair_count(dsu, terminals):
    sizes = Counter(dsu.find(v) for v in terminals)
    return sum(n * (n - 1) // 2 for n in sizes.values())


def compute_attr(node_ids, edges, iterations=5000, seed=2026):
    ids = set(node_ids)
    for u, v, _ in edges:
        ids.add(u); ids.add(v)
    index = {v: i for i, v in enumerate(sorted(ids))}
    terminals = [index[v] for v in node_ids]
    total_pairs = len(terminals) * (len(terminals) - 1) // 2
    if total_pairs == 0:
        raise ValueError('Se requieren al menos dos nodos estrategicos.')

    base = DSU(len(index))
    uncertain = []
    certain_failed = 0
    for u, v, p in edges:
        if p is None or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError(f'Probabilidad invalida en enlace {u}-{v}: {p}')
        if p == 0:
            base.union(index[u], index[v])
        elif p == 1:
            certain_failed += 1
        else:
            uncertain.append((index[u], index[v], p))

    if not uncertain:
        connected = pair_count(base, terminals)
        value = connected / total_pairs
        return dict(method='exacto', attr=value, connected_pairs=connected,
                    total_pairs=total_pairs, runs=0, ci_low=value, ci_high=value,
                    variable_edges=0, certain_failed_edges=certain_failed)

    rng = random.Random(seed)
    samples = []
    for i in range(iterations):
        dsu = base.clone()
        for u, v, p in uncertain:
            if rng.random() >= p:
                dsu.union(u, v)
        samples.append(pair_count(dsu, terminals) / total_pairs)
        if (i + 1) % 1000 == 0:
            print(f'Simulaciones: {i + 1}/{iterations}', flush=True)

    mean = sum(samples) / iterations
    variance = sum((x - mean) ** 2 for x in samples) / (iterations - 1)
    se = math.sqrt(variance / iterations)
    return dict(method='monte_carlo', attr=mean, connected_pairs=None,
                total_pairs=total_pairs, runs=iterations,
                ci_low=max(0.0, mean - 1.96 * se),
                ci_high=min(1.0, mean + 1.96 * se),
                variable_edges=len(uncertain), certain_failed_edges=certain_failed)


def calculate(conn, scenario, iterations, seed):
    with conn.cursor() as cur:
        cur.execute('SELECT id_vertice FROM red_integrada_nodos_clave_v2 ORDER BY id_vertice')
        nodes = [int(r[0]) for r in cur.fetchall()]
        cur.execute('''
            SELECT a.source, a.target,
                   CASE
                     WHEN %s = 'pre_terremoto' THEN 0.0
                     WHEN %s = 'post_optimista' THEN COALESCE(p.prob_falla, 0.0)
                     WHEN %s = 'post_conservador' THEN COALESCE(p.prob_falla, 1.0)
                   END AS p_falla
            FROM red_integrada_aristas_conectada_v1 a
            LEFT JOIN probabilidades_falla_2010 p ON p.id_arista = a.id
            ORDER BY a.id
        ''', (scenario, scenario, scenario))
        rows = cur.fetchall()
        if not rows or any(u is None or v is None or p is None for u, v, p in rows):
            raise ValueError('La topologia tiene extremos o probabilidades sin resolver.')
        edges = [(int(u), int(v), float(p)) for u, v, p in rows]

    result = compute_attr(nodes, edges, iterations, seed)
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS attr_integrada_resultados_v2 (
                escenario TEXT PRIMARY KEY,
                total_nodos INTEGER NOT NULL,
                total_aristas INTEGER NOT NULL,
                total_pares INTEGER NOT NULL,
                pares_conectados_exactos INTEGER,
                attr DOUBLE PRECISION NOT NULL,
                metodo TEXT NOT NULL,
                iteraciones INTEGER NOT NULL,
                semilla INTEGER NOT NULL,
                ic95_inferior DOUBLE PRECISION NOT NULL,
                ic95_superior DOUBLE PRECISION NOT NULL,
                fecha_calculo TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            INSERT INTO attr_integrada_resultados_v2
            (escenario,total_nodos,total_aristas,total_pares,pares_conectados_exactos,
             attr,metodo,iteraciones,semilla,ic95_inferior,ic95_superior,fecha_calculo)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT (escenario) DO UPDATE SET
              total_nodos=EXCLUDED.total_nodos,
              total_aristas=EXCLUDED.total_aristas,
              total_pares=EXCLUDED.total_pares,
              pares_conectados_exactos=EXCLUDED.pares_conectados_exactos,
              attr=EXCLUDED.attr, metodo=EXCLUDED.metodo,
              iteraciones=EXCLUDED.iteraciones, semilla=EXCLUDED.semilla,
              ic95_inferior=EXCLUDED.ic95_inferior,
              ic95_superior=EXCLUDED.ic95_superior,
              fecha_calculo=CURRENT_TIMESTAMP
        ''', (scenario, len(nodes), len(edges), result['total_pairs'],
              result['connected_pairs'], result['attr'], result['method'],
              result['runs'], seed, result['ci_low'], result['ci_high']))
    conn.commit()
    return len(nodes), len(edges), result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--escenario', choices=['pre_terremoto','post_optimista','post_conservador'], required=True)
    ap.add_argument('--iteraciones', type=int, default=5000)
    ap.add_argument('--semilla', type=int, default=2026)
    ap.add_argument('--host', default=os.getenv('PGHOST','localhost'))
    ap.add_argument('--puerto', type=int, default=int(os.getenv('PGPORT','5433')))
    ap.add_argument('--base', default=os.getenv('PGDATABASE','t2_indv_prueba'))
    ap.add_argument('--usuario', default=os.getenv('PGUSER','natt'))
    args = ap.parse_args()
    if args.iteraciones < 2:
        ap.error('--iteraciones debe ser al menos 2')
    conn = psycopg2.connect(host=args.host, port=args.puerto, dbname=args.base,
                            user=args.usuario, password=os.getenv('PGPASSWORD'))
    try:
        n_nodes, n_edges, r = calculate(conn, args.escenario, args.iteraciones, args.semilla)
        print('\n========== RESULTADOS ATTR ==========')
        print(f'Escenario: {args.escenario}')
        print(f'Nodos clave: {n_nodes} | Aristas: {n_edges}')
        print(f'Metodo: {r["method"]}')
        print(f'Pares totales: {r["total_pairs"]}')
        if r['connected_pairs'] is not None:
            print(f'Pares conectados: {r["connected_pairs"]}')
        print(f'Enlaces probabilisticos: {r["variable_edges"]}')
        print(f'Enlaces con falla segura: {r["certain_failed_edges"]}')
        print(f'ATTR: {r["attr"]:.8f} ({r["attr"]*100:.4f}%)')
        print(f'IC 95%: [{r["ci_low"]:.8f}, {r["ci_high"]:.8f}]')
        print('Resultado guardado en attr_integrada_resultados_v2.')
    finally:
        conn.close()

if __name__ == '__main__':
    main()
