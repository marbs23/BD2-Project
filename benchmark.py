"""
benchmark.py
============
Evaluación experimental — mide inserción, búsqueda puntual y búsqueda por rango
para BPTree, Sequential File, Extendible Hash y R-Tree con n = 1k / 10k / 100k.

Salida:
  benchmark_results.json   → consumido por el frontend
  benchmark_plots/         → PNGs para el informe (requiere matplotlib)

Uso:
  python3 benchmark.py
  python3 benchmark.py --no-plots
  python3 benchmark.py --sizes 1000 10000
"""

import argparse, csv, json, math, os, sys, time

# ── args ──────────────────────────────────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("--sizes",    nargs="+", type=int, default=[1_000, 10_000, 100_000])
ap.add_argument("--no-plots", action="store_true")
ap.add_argument("--output-dir",    default="benchmark_plots")
ap.add_argument("--results-file",  default="benchmark_results.json")
args = ap.parse_args()

SIZES        = args.sizes
PLOT_DIR     = args.output_dir
RESULTS_FILE = args.results_file
MAKE_PLOTS   = not args.no_plots

# ── imports ───────────────────────────────────────────────────────────────────
from bplustree      import BPlusTree,          Record as RecordBPT
from sequential_file import SeqFile,           Record as RecordSEQ
from extendible_hash import ExtendibleHashFile, Record as RecordHASH
from rtree.rtree_index import RTree
from rtree.page_store  import PageStore, IOStats
from rtree.geometry    import TID

# ── rutas CSV ─────────────────────────────────────────────────────────────────
BOOKS_CSV  = {n: f"books/books_{n}.csv"   for n in SIZES}
COORDS_CSV = {n: f"coords/coords_{n}.csv" for n in SIZES}
TMP = "_bench_"

def cleanup(*paths):
    for p in paths:
        for ext in ["", "_aux.bin", ".json"]:
            f = p + ext
            if os.path.exists(f): os.remove(f)

# ── leer CSV ──────────────────────────────────────────────────────────────────
def read_books(path, limit):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= limit: break
            try:
                rows.append(dict(
                    id     = int(row["book_key"]),
                    title  = row["title"][:100],
                    author = row["author"][:40],
                    pages  = int(float(row["pages"] or 0)),
                    rating = float(row["average_rating"] or 0),
                    year   = int(float(row["published_date"] or 0)),
                ))
            except: pass
    return rows

def read_coords(path, limit):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= limit: break
            try:
                rows.append((float(row["lon"]), float(row["lat"])))
            except: pass
    return rows

# ── medición ──────────────────────────────────────────────────────────────────
def measure(fn):
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000   # ms

# ── BPTree ────────────────────────────────────────────────────────────────────
def bench_bptree(n, csv_path):
    path = TMP + f"bpt_{n}.bin"
    cleanup(path)

    # INSERT: medir io DURANTE from_csv, no después
    db = BPlusTree(path)          # árbol vacío, contadores en 0
    records = read_books(csv_path, n)
    db.reset_stats()
    t0 = time.perf_counter()
    for rec in records:
        db.add(RecordBPT(**rec))
    insert_ms = (time.perf_counter() - t0) * 1000
    s = db.get_stats(); insert_io = s["reads"] + s["writes"]

    ids = sorted(records, key=lambda r: r["id"])
    mid = ids[len(ids)//2]["id"]
    lo  = ids[len(ids)//4]["id"]
    hi  = ids[len(ids)*3//4]["id"]

    db.reset_stats()
    t0 = time.perf_counter(); db.search(mid); search_ms = (time.perf_counter()-t0)*1000
    s = db.get_stats(); search_io = s["reads"] + s["writes"]

    db.reset_stats()
    t0 = time.perf_counter(); db.range_search(lo, hi); range_ms = (time.perf_counter()-t0)*1000
    s = db.get_stats(); range_io = s["reads"] + s["writes"]

    db.close(); cleanup(path)
    return dict(
        insert=dict(ms=round(insert_ms,3), io=insert_io),
        search=dict(ms=round(search_ms,3), io=search_io),
        range =dict(ms=round(range_ms, 3), io=range_io),
    )

# ── Sequential File ───────────────────────────────────────────────────────────
def bench_sequential(n, csv_path):
    path = TMP + f"seq_{n}.bin"
    cleanup(path)

    t0 = time.perf_counter()
    db = SeqFile.from_csv(path, csv_path, limite=n)
    insert_ms = (time.perf_counter() - t0) * 1000
    s = db.get_stats(); insert_io = s["reads"] + s["writes"]

    ids = sorted(read_books(csv_path, n), key=lambda r: r["id"])
    mid = ids[len(ids)//2]["id"]
    lo  = ids[len(ids)//4]["id"]
    hi  = ids[len(ids)*3//4]["id"]

    db.reset_stats()
    t0 = time.perf_counter(); db.search(mid); search_ms = (time.perf_counter()-t0)*1000
    s = db.get_stats(); search_io = s["reads"] + s["writes"]

    db.reset_stats()
    t0 = time.perf_counter(); db.range_search(lo, hi); range_ms = (time.perf_counter()-t0)*1000
    s = db.get_stats(); range_io = s["reads"] + s["writes"]

    db.close(); cleanup(path)
    return dict(
        insert=dict(ms=round(insert_ms,3), io=insert_io),
        search=dict(ms=round(search_ms,3), io=search_io),
        range =dict(ms=round(range_ms, 3), io=range_io),
    )

# ── Extendible Hash ───────────────────────────────────────────────────────────
def bench_hash(n, csv_path):
    bin_p = TMP + f"hash_{n}.bin"
    idx_p = TMP + f"hash_{n}.json"
    cleanup(bin_p, idx_p)

    t0 = time.perf_counter()
    db = ExtendibleHashFile.from_csv(bin_p, idx_p, csv_path)
    insert_ms = (time.perf_counter() - t0) * 1000
    insert_io = db.disk_reads + db.disk_writes

    ids = sorted(read_books(csv_path, n), key=lambda r: r["id"])
    mid = ids[len(ids)//2]["id"]

    db.reset_counters()
    t0 = time.perf_counter(); db.search(mid); search_ms = (time.perf_counter()-t0)*1000
    search_io = db.disk_reads + db.disk_writes

    db.close(); cleanup(bin_p, idx_p)
    return dict(
        insert=dict(ms=round(insert_ms,3), io=insert_io),
        search=dict(ms=round(search_ms,3), io=search_io),
        range =dict(ms=None, io=None),   # no soportado
    )

# ── R-Tree ────────────────────────────────────────────────────────────────────
def bench_rtree(n, csv_path):
    path = TMP + f"rtree_{n}.bin"
    cleanup(path)

    stats_obj = IOStats()
    store = PageStore(path, stats_obj)
    db    = RTree(store)

    before = stats_obj.snapshot()
    t0 = time.perf_counter()
    db.bulk_load_from_csv(csv_path, "lon", "lat")
    insert_ms = (time.perf_counter() - t0) * 1000
    after = stats_obj.snapshot()
    insert_io = (after.reads - before.reads) + (after.writes - before.writes)

    pts = read_coords(csv_path, n)
    mid_lon, mid_lat = pts[len(pts)//2]

    before = stats_obj.snapshot()
    t0 = time.perf_counter()
    db.range_search(mid_lon, mid_lat, 1.0)
    search_ms = (time.perf_counter() - t0) * 1000
    after = stats_obj.snapshot()
    search_io = (after.reads - before.reads) + (after.writes - before.writes)

    before = stats_obj.snapshot()
    t0 = time.perf_counter()
    db.knn(mid_lon, mid_lat, 10)
    knn_ms = (time.perf_counter() - t0) * 1000
    after = stats_obj.snapshot()
    knn_io = (after.reads - before.reads) + (after.writes - before.writes)

    cleanup(path)
    return dict(
        insert=dict(ms=round(insert_ms,3), io=insert_io),
        search=dict(ms=round(search_ms,3), io=search_io),   # radius
        knn   =dict(ms=round(knn_ms,   3), io=knn_io),
        range =dict(ms=round(search_ms,3), io=search_io),   # alias para gráficos
    )

# ── loop principal ────────────────────────────────────────────────────────────
results = {}

for n in SIZES:
    print(f"\n{'='*56}\n  n = {n:,}\n{'='*56}")
    bp = BOOKS_CSV.get(n);  cp = COORDS_CSV.get(n)

    if not bp or not os.path.exists(bp):
        print(f"  [SKIP] no se encontró {bp}"); continue

    results[n] = {}

    print("  [BPTree]     ", end="", flush=True)
    try:
        r = bench_bptree(n, bp); results[n]["BPTREE"] = r
        print(f"insert={r['insert']['ms']:.0f}ms/{r['insert']['io']}io  "
              f"search={r['search']['ms']:.1f}ms/{r['search']['io']}io  "
              f"range={r['range']['ms']:.1f}ms/{r['range']['io']}io")
    except Exception as e:
        print(f"ERROR: {e}"); results[n]["BPTREE"] = {}

    print("  [Sequential] ", end="", flush=True)
    try:
        r = bench_sequential(n, bp); results[n]["SEQUENTIAL"] = r
        print(f"insert={r['insert']['ms']:.0f}ms/{r['insert']['io']}io  "
              f"search={r['search']['ms']:.1f}ms/{r['search']['io']}io  "
              f"range={r['range']['ms']:.1f}ms/{r['range']['io']}io")
    except Exception as e:
        print(f"ERROR: {e}"); results[n]["SEQUENTIAL"] = {}

    print("  [Hash]       ", end="", flush=True)
    try:
        r = bench_hash(n, bp); results[n]["HASH"] = r
        print(f"insert={r['insert']['ms']:.0f}ms/{r['insert']['io']}io  "
              f"search={r['search']['ms']:.1f}ms/{r['search']['io']}io  range=N/A")
    except Exception as e:
        print(f"ERROR: {e}"); results[n]["HASH"] = {}

    if cp and os.path.exists(cp):
        print("  [RTree]      ", end="", flush=True)
        try:
            r = bench_rtree(n, cp); results[n]["RTREE"] = r
            print(f"insert={r['insert']['ms']:.0f}ms/{r['insert']['io']}io  "
                  f"radius={r['search']['ms']:.1f}ms/{r['search']['io']}io  "
                  f"knn={r['knn']['ms']:.1f}ms/{r['knn']['io']}io")
        except Exception as e:
            print(f"ERROR: {e}"); results[n]["RTREE"] = {}

# ── guardar JSON ──────────────────────────────────────────────────────────────
with open(RESULTS_FILE, "w") as f:
    json.dump({"sizes": SIZES,
               "results": {str(k): v for k, v in results.items()}}, f, indent=2)
print(f"\nResultados → {RESULTS_FILE}")

# ── gráficos ──────────────────────────────────────────────────────────────────
if not MAKE_PLOTS:
    print("Gráficos omitidos."); sys.exit(0)

try:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("matplotlib no instalado: pip install matplotlib"); sys.exit(0)

# subcarpetas separadas
TRAD_DIR  = os.path.join(PLOT_DIR, "tradicionales")
RTREE_DIR = os.path.join(PLOT_DIR, "rtree")
os.makedirs(TRAD_DIR,  exist_ok=True)
os.makedirs(RTREE_DIR, exist_ok=True)

sizes_ok = [n for n in SIZES if n in results]
x        = list(range(len(sizes_ok)))
x_labels = [f"{n:,}" for n in sizes_ok]

STYLE = dict(facecolor="#0f172a")
AX_BG = "#1e293b"
GRID  = "#334155"

# ── helpers ───────────────────────────────────────────────────────────────────
def _style_ax(ax):
    ax.set_facecolor(AX_BG)
    ax.tick_params(colors="#94a3b8")
    ax.spines[:].set_color(GRID)
    ax.grid(axis="y", color=GRID, linestyle="--", alpha=0.5, zorder=0)

def _legend(ax):
    ax.legend(facecolor=AX_BG, edgecolor=GRID, labelcolor="white", fontsize=9)

def _save(fig, path):
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  {path}")

def _val(tec, op, metric, n):
    return results.get(n, {}).get(tec, {}).get(op, {}).get(metric) or 0

# ── 1. ÍNDICES TRADICIONALES ──────────────────────────────────────────────────
# Colores y etiquetas
TRAD_TECS    = ["BPTREE", "SEQUENTIAL", "HASH"]
TRAD_COLORS  = {"BPTREE": "#6366f1", "SEQUENTIAL": "#10b981", "HASH": "#f59e0b"}
TRAD_LABELS  = {"BPTREE": "B+ Tree",  "SEQUENTIAL": "Sequential", "HASH": "Ext. Hash"}
TRAD_OPS     = ["insert", "search", "range"]
TRAD_OP_LBL  = {"insert": "Inserción", "search": "Búsqueda puntual",
                "range":  "Búsqueda por rango"}

BAR_W = 0.22

def trad_chart(op, metric, ylabel, fname):
    """Gráfico agrupado: un grupo por n, una barra por técnica."""
    fig, ax = plt.subplots(figsize=(10, 5), **STYLE)
    _style_ax(ax)

    tecs = [t for t in TRAD_TECS
            if any(_val(t, op, metric, n) for n in sizes_ok)]
    n_t  = len(tecs)
    offs = [(i - (n_t - 1) / 2) * BAR_W for i in range(n_t)]

    for i, tec in enumerate(tecs):
        vs   = [_val(tec, op, metric, n) for n in sizes_ok]
        bars = ax.bar([xi + offs[i] for xi in x], vs,
                      width=BAR_W * 0.9, color=TRAD_COLORS[tec],
                      label=TRAD_LABELS[tec], alpha=0.9, zorder=3)
        for bar, v in zip(bars, vs):
            if v > 0:
                lbl = f"{v:.0f}" if metric == "io" else f"{v:.1f}"
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() * 1.02, lbl,
                        ha="center", va="bottom", fontsize=7,
                        color="white", fontweight="bold")

    ax.set_xticks(x); ax.set_xticklabels(x_labels, color="#94a3b8")
    ax.set_xlabel("Tamaño del dataset (n)", color="#94a3b8")
    ax.set_ylabel(ylabel, color="#94a3b8")
    ax.set_title(f"{TRAD_OP_LBL[op]} — {ylabel}", color="white", fontsize=13, pad=12)
    _legend(ax)
    _save(fig, os.path.join(TRAD_DIR, fname))

print("\nGenerando gráficos — Índices Tradicionales...")
for op in TRAD_OPS:
    trad_chart(op, "ms", "Tiempo (ms)",       f"{op}_tiempo.png")
    trad_chart(op, "io", "Accesos a disco",   f"{op}_io.png")

# ── 2. R-TREE ─────────────────────────────────────────────────────────────────
RTREE_OPS    = ["insert", "search", "knn"]   # search = radius
RTREE_OP_LBL = {"insert": "Inserción (STR)",
                "search": "Búsqueda por radio (RADIUS)",
                "knn":    "Búsqueda kNN"}
RTREE_COLORS = {"insert": "#a855f7", "search": "#ec4899", "knn": "#06b6d4"}

def rtree_chart_single(op, metric, ylabel, fname):
    """Una operación del R-Tree, barras por tamaño."""
    fig, ax = plt.subplots(figsize=(8, 4), **STYLE)
    _style_ax(ax)

    vs   = [_val("RTREE", op, metric, n) for n in sizes_ok]
    bars = ax.bar(x, vs, width=0.5, color=RTREE_COLORS[op], alpha=0.9, zorder=3)
    for bar, v in zip(bars, vs):
        if v > 0:
            lbl = f"{v:.0f}" if metric == "io" else f"{v:.1f}"
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() * 1.02, lbl,
                    ha="center", va="bottom", fontsize=8,
                    color="white", fontweight="bold")

    ax.set_xticks(x); ax.set_xticklabels(x_labels, color="#94a3b8")
    ax.set_xlabel("Tamaño del dataset (n)", color="#94a3b8")
    ax.set_ylabel(ylabel, color="#94a3b8")
    ax.set_title(f"R-Tree — {RTREE_OP_LBL[op]} — {ylabel}",
                 color="white", fontsize=12, pad=10)
    _save(fig, os.path.join(RTREE_DIR, fname))

def rtree_chart_compare(metric, ylabel, fname):
    """Compara las 3 operaciones del R-Tree en un solo gráfico."""
    fig, ax = plt.subplots(figsize=(10, 5), **STYLE)
    _style_ax(ax)

    n_ops = len(RTREE_OPS)
    bw    = 0.22
    offs  = [(i - (n_ops - 1) / 2) * bw for i in range(n_ops)]

    for i, op in enumerate(RTREE_OPS):
        vs   = [_val("RTREE", op, metric, n) for n in sizes_ok]
        bars = ax.bar([xi + offs[i] for xi in x], vs,
                      width=bw * 0.9, color=RTREE_COLORS[op],
                      label=RTREE_OP_LBL[op], alpha=0.9, zorder=3)
        for bar, v in zip(bars, vs):
            if v > 0:
                lbl = f"{v:.0f}" if metric == "io" else f"{v:.1f}"
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() * 1.02, lbl,
                        ha="center", va="bottom", fontsize=7,
                        color="white", fontweight="bold")

    ax.set_xticks(x); ax.set_xticklabels(x_labels, color="#94a3b8")
    ax.set_xlabel("Tamaño del dataset (n)", color="#94a3b8")
    ax.set_ylabel(ylabel, color="#94a3b8")
    ax.set_title(f"R-Tree — Comparación de operaciones — {ylabel}",
                 color="white", fontsize=13, pad=12)
    _legend(ax)
    _save(fig, os.path.join(RTREE_DIR, fname))

print("Generando gráficos — R-Tree...")
for op in RTREE_OPS:
    rtree_chart_single(op, "ms", "Tiempo (ms)",     f"{op}_tiempo.png")
    rtree_chart_single(op, "io", "Accesos a disco", f"{op}_io.png")

rtree_chart_compare("ms", "Tiempo (ms)",     "comparacion_tiempo.png")
rtree_chart_compare("io", "Accesos a disco", "comparacion_io.png")

print(f"\nGráficos tradicionales → '{TRAD_DIR}/'")
print(f"Gráficos R-Tree        → '{RTREE_DIR}/'  ✓")
