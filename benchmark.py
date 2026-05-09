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
    import matplotlib.ticker as mticker
except ImportError:
    print("matplotlib no instalado: pip install matplotlib"); sys.exit(0)

os.makedirs(PLOT_DIR, exist_ok=True)

TECNICAS = ["BPTREE", "SEQUENTIAL", "HASH", "RTREE"]
COLORS   = {"BPTREE":"#6366f1","SEQUENTIAL":"#10b981","HASH":"#f59e0b","RTREE":"#a855f7"}
LABELS   = {"BPTREE":"B+ Tree","SEQUENTIAL":"Sequential","HASH":"Ext. Hash","RTREE":"R-Tree"}
OPS      = ["insert","search","range"]
OP_LBL   = {"insert":"Inserción","search":"Búsqueda puntual","range":"Búsqueda por rango"}

sizes_ok = [n for n in SIZES if n in results]
x        = list(range(len(sizes_ok)))
x_labels = [f"{n:,}" for n in sizes_ok]
BAR_W    = 0.18

def vals(tec, op, metric):
    return [results.get(n,{}).get(tec,{}).get(op,{}).get(metric) or 0
            for n in sizes_ok]

def bar_chart(op, metric, ylabel, fname):
    fig, ax = plt.subplots(figsize=(10,5))
    fig.patch.set_facecolor("#0f172a"); ax.set_facecolor("#1e293b")
    tecs = [t for t in TECNICAS
            if any(results.get(n,{}).get(t,{}).get(op,{}).get(metric)
                   for n in sizes_ok)]
    n_t = len(tecs)
    offs = [(i-(n_t-1)/2)*BAR_W for i in range(n_t)]
    for i, tec in enumerate(tecs):
        vs = vals(tec, op, metric)
        bars = ax.bar([xi+offs[i] for xi in x], vs, width=BAR_W*0.9,
                      color=COLORS[tec], label=LABELS[tec], alpha=0.9, zorder=3)
        for bar, v in zip(bars, vs):
            if v > 0:
                ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()*1.02,
                        f"{v:.0f}" if metric=="io" else f"{v:.1f}",
                        ha="center", va="bottom", fontsize=7,
                        color="white", fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(x_labels, color="#94a3b8")
    ax.set_xlabel("n", color="#94a3b8"); ax.set_ylabel(ylabel, color="#94a3b8")
    ax.set_title(f"{OP_LBL[op]} — {ylabel}", color="white", fontsize=13, pad=12)
    ax.tick_params(colors="#94a3b8"); ax.spines[:].set_color("#334155")
    ax.grid(axis="y", color="#334155", linestyle="--", alpha=0.5, zorder=0)
    ax.legend(facecolor="#1e293b", edgecolor="#334155", labelcolor="white", fontsize=9)
    plt.tight_layout()
    p = os.path.join(PLOT_DIR, fname)
    plt.savefig(p, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(); print(f"  {p}")

print("\nGenerando gráficos...")
for op in OPS:
    bar_chart(op, "ms",  "Tiempo (ms)",          f"{op}_tiempo.png")
    bar_chart(op, "io",  "Accesos a disco",       f"{op}_io.png")

# gráfico extra RTree: RADIUS vs kNN
fig, axes = plt.subplots(1,2,figsize=(12,5))
fig.patch.set_facecolor("#0f172a")
for ax in axes: ax.set_facecolor("#1e293b")
for ax_i, (metric, ylabel) in enumerate([("ms","Tiempo (ms)"),("io","Accesos a disco")]):
    ax = axes[ax_i]
    vr = [results.get(n,{}).get("RTREE",{}).get("search",{}).get(metric,0) or 0 for n in sizes_ok]
    vk = [results.get(n,{}).get("RTREE",{}).get("knn",   {}).get(metric,0) or 0 for n in sizes_ok]
    ax.bar([xi-BAR_W/2 for xi in x], vr, width=BAR_W*0.9, color="#a855f7", label="RADIUS", alpha=0.9, zorder=3)
    ax.bar([xi+BAR_W/2 for xi in x], vk, width=BAR_W*0.9, color="#ec4899", label="kNN",    alpha=0.9, zorder=3)
    ax.set_xticks(x); ax.set_xticklabels(x_labels, color="#94a3b8")
    ax.set_xlabel("n", color="#94a3b8"); ax.set_ylabel(ylabel, color="#94a3b8")
    ax.set_title(f"R-Tree — {ylabel}", color="white", fontsize=11)
    ax.tick_params(colors="#94a3b8"); ax.spines[:].set_color("#334155")
    ax.grid(axis="y", color="#334155", linestyle="--", alpha=0.5, zorder=0)
    ax.legend(facecolor="#1e293b", edgecolor="#334155", labelcolor="white", fontsize=9)
plt.tight_layout()
p = os.path.join(PLOT_DIR, "rtree_spatial.png")
plt.savefig(p, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
plt.close(); print(f"  {p}")

print(f"\nGráficos en '{PLOT_DIR}/'  ✓")
