import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { 
  Search, 
  Plus, 
  Trash2, 
  Database, 
  Save, 
  X, 
  Terminal, 
  Table as TableIcon, 
  Play, 
  RotateCcw,
  Info,
  ChevronRight,
  BarChart2,
  Clock,
  HardDrive,
} from 'lucide-react';
import TableSidebar from './components/TableInfo';


// ── Componente de visualización R-Tree ────────────────────────────────────────
const RTreeViewer = ({ rtreeQuery, rtreePoints, rtreeTables, onLoadPoints }) => {
  const canvasRef = React.useRef(null);
  const [selectedTable, setSelectedTable] = React.useState('');

  // Todos los puntos a mostrar: los del índice + los resultados de la query
  const allPoints = rtreePoints;
  const resultPoints = rtreeQuery?.results || [];

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const PAD = 40;

    ctx.clearRect(0, 0, W, H);

    // Fondo
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, W, H);

    // Calcular bounding box de todos los puntos
    const pts = allPoints.length > 0 ? allPoints : (rtreeQuery ? [{ lon: rtreeQuery.lon, lat: rtreeQuery.lat }] : []);
    if (pts.length === 0 && !rtreeQuery) {
      ctx.fillStyle = '#475569';
      ctx.font = '14px monospace';
      ctx.textAlign = 'center';
      ctx.fillText('Sin datos. Ejecuta una consulta R-Tree o carga puntos.', W / 2, H / 2);
      return;
    }

    const allLons = pts.map(p => p.lon);
    const allLats = pts.map(p => p.lat);
    if (rtreeQuery) { allLons.push(rtreeQuery.lon); allLats.push(rtreeQuery.lat); }

    let minLon = Math.min(...allLons);
    let maxLon = Math.max(...allLons);
    let minLat = Math.min(...allLats);
    let maxLat = Math.max(...allLats);

    // Expandir un poco para que los puntos no queden en el borde
    const dLon = (maxLon - minLon) * 0.1 || 1;
    const dLat = (maxLat - minLat) * 0.1 || 1;
    minLon -= dLon; maxLon += dLon;
    minLat -= dLat; maxLat += dLat;

    const toX = (lon) => PAD + ((lon - minLon) / (maxLon - minLon)) * (W - 2 * PAD);
    const toY = (lat) => H - PAD - ((lat - minLat) / (maxLat - minLat)) * (H - 2 * PAD);

    // Grid
    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
      const x = PAD + (i / 5) * (W - 2 * PAD);
      const y = PAD + (i / 5) * (H - 2 * PAD);
      ctx.beginPath(); ctx.moveTo(x, PAD); ctx.lineTo(x, H - PAD); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke();
    }

    // Ejes labels
    ctx.fillStyle = '#475569';
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(minLon.toFixed(2), PAD, H - 10);
    ctx.fillText(maxLon.toFixed(2), W - PAD, H - 10);
    ctx.textAlign = 'right';
    ctx.fillText(minLat.toFixed(2), PAD - 4, H - PAD);
    ctx.fillText(maxLat.toFixed(2), PAD - 4, PAD + 4);

    // Círculo de radio si hay query RADIUS
    if (rtreeQuery?.radius != null) {
      const cx = toX(rtreeQuery.lon);
      const cy = toY(rtreeQuery.lat);
      // Radio en unidades de pantalla (mejorado para coordenadas geográficas)
      // Usar el promedio del rango de lon y lat para mejor escala
      const lonRange = maxLon - minLon;
      const latRange = maxLat - minLat;
      const avgRange = (lonRange + latRange) / 2;
      
      // Radio mínimo visible de 10px, escalado según el radio real
      const rPx = Math.max(10, (rtreeQuery.radius / avgRange) * (W - 2 * PAD));
      
      ctx.beginPath();
      ctx.arc(cx, cy, rPx, 0, 2 * Math.PI);
      ctx.strokeStyle = 'rgba(139,92,246,0.8)';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = 'rgba(139,92,246,0.15)';
      ctx.fill();
    }

    // Puntos del índice (azul claro más visible)
    allPoints.forEach(p => {
      ctx.beginPath();
      ctx.arc(toX(p.lon), toY(p.lat), 3, 0, 2 * Math.PI);
      ctx.fillStyle = 'rgba(129, 140, 248, 0.6)'; // Un azul claro más visible
      ctx.fill();
    });

    // Puntos resultado (verde brillante)
    resultPoints.forEach(p => {
      if (p.lon == null) return;
      ctx.beginPath();
      ctx.arc(toX(p.lon), toY(p.lat), 5, 0, 2 * Math.PI);
      ctx.fillStyle = '#10b981';
      ctx.fill();
      ctx.strokeStyle = '#34d399';
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    // Punto de consulta (rojo)
    if (rtreeQuery) {
      const cx = toX(rtreeQuery.lon);
      const cy = toY(rtreeQuery.lat);
      ctx.beginPath();
      ctx.arc(cx, cy, 7, 0, 2 * Math.PI);
      ctx.fillStyle = '#ef4444';
      ctx.fill();
      ctx.strokeStyle = '#fca5a5';
      ctx.lineWidth = 2;
      ctx.stroke();
      // Cruz
      ctx.strokeStyle = '#fca5a5';
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(cx - 12, cy); ctx.lineTo(cx + 12, cy); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx, cy - 12); ctx.lineTo(cx, cy + 12); ctx.stroke();
    }

    // Leyenda
    const legend = [
      { color: '#334155', label: `Puntos índice (${allPoints.length})` },
      { color: '#10b981', label: `Resultados (${resultPoints.length})` },
      { color: '#ef4444', label: 'Punto consulta' },
    ];
    legend.forEach((l, i) => {
      ctx.fillStyle = l.color;
      ctx.fillRect(W - 160, 12 + i * 18, 10, 10);
      ctx.fillStyle = '#94a3b8';
      ctx.font = '10px monospace';
      ctx.textAlign = 'left';
      ctx.fillText(l.label, W - 146, 21 + i * 18);
    });

  }, [allPoints, rtreeQuery, resultPoints]);

  return (
    <div className="animate-in fade-in duration-300 p-4 flex flex-col gap-4">
      <div className="flex items-center gap-4 flex-wrap">
        <h3 className="text-sm font-bold text-slate-300 flex items-center gap-2">
          🗺 Visualización R-Tree
        </h3>
        {rtreeTables.length > 0 && (
          <div className="flex items-center gap-2">
            <select
              value={selectedTable}
              onChange={e => setSelectedTable(e.target.value)}
              className="px-2 py-1 bg-slate-800 border border-slate-600 rounded text-slate-200 text-xs"
            >
              <option value="">-- seleccionar tabla --</option>
              {rtreeTables.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <button
              onClick={() => selectedTable && onLoadPoints(selectedTable)}
              className="px-3 py-1 bg-purple-700 hover:bg-purple-600 text-white text-xs rounded"
            >
              Cargar puntos
            </button>
          </div>
        )}
        {rtreeQuery && (
          <div className="text-xs text-slate-400 bg-slate-800 px-3 py-1 rounded">
            Query: ({rtreeQuery.lon}, {rtreeQuery.lat})
            {rtreeQuery.radius != null && ` RADIUS ${rtreeQuery.radius}`}
            {rtreeQuery.k != null && ` K=${rtreeQuery.k}`}
            {' → '}<span className="text-emerald-400">{resultPoints.length} resultados</span>
          </div>
        )}
      </div>

      <canvas
        ref={canvasRef}
        width={900}
        height={500}
        className="w-full rounded-xl border border-slate-700 bg-[#0f172a]"
        style={{ maxHeight: '500px' }}
      />

      <div className="text-xs text-slate-500 text-center">
        <span className="inline-block w-3 h-3 rounded-full bg-[#334155] mr-1 align-middle" /> Todos los puntos del índice &nbsp;
        <span className="inline-block w-3 h-3 rounded-full bg-emerald-500 mr-1 align-middle" /> Resultados de la búsqueda &nbsp;
        <span className="inline-block w-3 h-3 rounded-full bg-red-500 mr-1 align-middle" /> Punto de consulta &nbsp;
        <span className="inline-block w-3 h-3 rounded-full bg-purple-500/40 border border-purple-400 mr-1 align-middle" /> Radio de búsqueda
      </div>

      {!rtreeQuery && allPoints.length === 0 && (
        <div className="text-center text-slate-500 text-sm py-8">
          Ejecuta una consulta <code className="bg-slate-800 px-1 rounded">SELECT ... WHERE coords IN (POINT(...), RADIUS ...)</code> para ver la visualización aquí automáticamente.
        </div>
      )}
    </div>
  );
};

// ── Mini gráfico de barras en canvas (sin librerías externas) ─────────────────
const BarChart = ({ data, title, color = '#6366f1', unit = '' }) => {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || data.length === 0) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const PAD_L = 52, PAD_R = 12, PAD_T = 28, PAD_B = 36;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#1e293b';
    ctx.fillRect(0, 0, W, H);

    const maxVal = Math.max(...data.map(d => d.value), 1);
    const barW = Math.floor(chartW / data.length * 0.6);
    const gap  = chartW / data.length;

    // Grid lines
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = PAD_T + chartH - (i / 4) * chartH;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
      ctx.fillStyle = '#64748b';
      ctx.font = '9px monospace';
      ctx.textAlign = 'right';
      const label = ((maxVal * i / 4));
      ctx.fillText(label >= 1000 ? `${(label/1000).toFixed(1)}k` : label.toFixed(label < 10 ? 1 : 0), PAD_L - 4, y + 3);
    }

    // Bars
    data.forEach((d, i) => {
      const barH = (d.value / maxVal) * chartH;
      const x = PAD_L + i * gap + (gap - barW) / 2;
      const y = PAD_T + chartH - barH;

      // Shadow
      ctx.fillStyle = color + '33';
      ctx.fillRect(x + 2, y + 2, barW, barH);

      // Bar
      const grad = ctx.createLinearGradient(x, y, x, y + barH);
      grad.addColorStop(0, color);
      grad.addColorStop(1, color + '88');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(x, y, barW, barH, [3, 3, 0, 0]);
      ctx.fill();

      // Value label on top
      ctx.fillStyle = '#e2e8f0';
      ctx.font = 'bold 9px monospace';
      ctx.textAlign = 'center';
      const vLabel = d.value >= 1000 ? `${(d.value/1000).toFixed(1)}k` : d.value.toFixed(d.value < 10 ? 1 : 0);
      ctx.fillText(vLabel + unit, x + barW / 2, y - 4);

      // X label
      ctx.fillStyle = '#94a3b8';
      ctx.font = '9px monospace';
      ctx.fillText(d.label, x + barW / 2, PAD_T + chartH + 14);
    });

    // Title
    ctx.fillStyle = '#cbd5e1';
    ctx.font = 'bold 11px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(title, PAD_L, 16);

  }, [data, title, color, unit]);

  return <canvas ref={canvasRef} width={320} height={200} className="rounded-lg w-full" />;
};

// ── Panel de Benchmark ────────────────────────────────────────────────────────
const BenchmarkPanel = ({ queryHistory }) => {
  const [benchData, setBenchData] = useState(null);
  const [loading, setLoading]     = useState(false);

  // Estado independiente para cada sección
  const [tradOp,     setTradOp]     = useState('insert');
  const [tradMetric, setTradMetric] = useState('ms');
  const [spatOp,     setSpatOp]     = useState('insert');
  const [spatMetric, setSpatMetric] = useState('ms');

  const TRAD = {
    tecnicas: ['BPTREE', 'SEQUENTIAL', 'HASH'],
    colors:   { BPTREE: '#6366f1', SEQUENTIAL: '#10b981', HASH: '#f59e0b' },
    labels:   { BPTREE: 'B+ Tree', SEQUENTIAL: 'Sequential', HASH: 'Ext. Hash' },
    ops:      ['insert', 'search', 'range'],
    opLabels: { insert: 'Inserción', search: 'Búsqueda puntual', range: 'Búsqueda por rango' },
  };

  const SPAT = {
    tecnicas: ['RTREE'],
    colors:   { RTREE: '#a855f7' },
    labels:   { RTREE: 'R-Tree' },
    ops:      ['insert', 'radius', 'knn'],
    opLabels: { insert: 'Inserción (STR)', radius: 'Radio (RADIUS)', knn: 'kNN' },
    opKey:    { insert: 'insert', radius: 'search', knn: 'knn' },
  };

  const loadBenchmark = async () => {
    setLoading(true);
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);
      const res = await fetch('http://localhost:8000/api/benchmark', { signal: controller.signal });
      clearTimeout(timeout);
      const data = await res.json();
      setBenchData(data);
    } catch {
      setBenchData({ success: false, message: 'Backend no disponible. Inicia el servidor con: python3 main.py' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadBenchmark(); }, []);

  const GroupedBarChart = ({ tecnicas, colors, labels, opKey, metric }) => {
    const canvasRef = useRef(null);
    const sizes = benchData?.sizes || [];

    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas || sizes.length === 0) return;
      const ctx = canvas.getContext('2d');
      const W = canvas.width, H = canvas.height;
      const PAD_L = 56, PAD_R = 16, PAD_T = 32, PAD_B = 40;
      const chartW = W - PAD_L - PAD_R;
      const chartH = H - PAD_T - PAD_B;

      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = '#1e293b';
      ctx.fillRect(0, 0, W, H);

      const allVals = tecnicas.flatMap(tec =>
        sizes.map(n => benchData.results[String(n)]?.[tec]?.[opKey]?.[metric] ?? 0)
      );
      const maxVal = Math.max(...allVals, 1);

      const nGroups = sizes.length;
      const nBars   = tecnicas.length;
      const groupW  = chartW / nGroups;
      const barW    = Math.min(groupW / (nBars + 1), 40);
      const groupPad = (groupW - barW * nBars) / 2;

      // grid
      ctx.strokeStyle = '#334155'; ctx.lineWidth = 1;
      for (let i = 0; i <= 4; i++) {
        const y = PAD_T + chartH - (i / 4) * chartH;
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
        ctx.fillStyle = '#64748b'; ctx.font = '9px monospace'; ctx.textAlign = 'right';
        const v = maxVal * i / 4;
        ctx.fillText(v >= 1000 ? `${(v/1000).toFixed(1)}k` : v.toFixed(v < 10 ? 1 : 0), PAD_L - 4, y + 3);
      }

      // barras
      sizes.forEach((n, gi) => {
        const gx = PAD_L + gi * groupW;
        tecnicas.forEach((tec, bi) => {
          const val  = benchData.results[String(n)]?.[tec]?.[opKey]?.[metric] ?? 0;
          const barH = (val / maxVal) * chartH;
          const x    = gx + groupPad + bi * barW;
          const y    = PAD_T + chartH - barH;
          const col  = colors[tec];

          ctx.fillStyle = col + '33';
          ctx.fillRect(x + 2, y + 2, barW - 2, barH);

          const grad = ctx.createLinearGradient(x, y, x, y + barH);
          grad.addColorStop(0, col); grad.addColorStop(1, col + '88');
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.roundRect(x, y, barW - 2, barH, [3, 3, 0, 0]);
          ctx.fill();

          if (val > 0) {
            ctx.fillStyle = '#e2e8f0'; ctx.font = 'bold 8px monospace'; ctx.textAlign = 'center';
            const lbl = val >= 1000 ? `${(val/1000).toFixed(1)}k` : val.toFixed(val < 10 ? 1 : 0);
            ctx.fillText(lbl, x + (barW - 2) / 2, y - 3);
          }
        });

        ctx.fillStyle = '#94a3b8'; ctx.font = '10px monospace'; ctx.textAlign = 'center';
        ctx.fillText(n >= 1000 ? `${n/1000}k` : String(n), gx + groupW / 2, PAD_T + chartH + 16);
      });

      // leyenda
      tecnicas.forEach((tec, i) => {
        const lx = PAD_L + i * 100;
        ctx.fillStyle = colors[tec];
        ctx.fillRect(lx, 10, 10, 10);
        ctx.fillStyle = '#94a3b8'; ctx.font = '9px monospace'; ctx.textAlign = 'left';
        ctx.fillText(labels[tec], lx + 13, 19);
      });
    }, [tecnicas, opKey, metric, sizes]);

    return <canvas ref={canvasRef} width={600} height={260} className="w-full rounded-lg" />;
  };

  const Section = ({ title, cfg, activeOp, setActiveOp, activeMetric, setActiveMetric }) => {
    const opKey = cfg.opKey ? cfg.opKey[activeOp] : activeOp;
    const accentCol = cfg.tecnicas[0] === 'RTREE' ? '#a855f7' : '#6366f1';
    return (
      <div className="bg-[#1e293b] rounded-xl border border-slate-700 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between flex-wrap gap-2">
          <h4 className="text-sm font-bold text-slate-200">{title}</h4>
          <div className="flex gap-2 flex-wrap">
            <div className="flex rounded-lg overflow-hidden border border-slate-600">
              {cfg.ops.map(op => (
                <button key={op} onClick={() => setActiveOp(op)}
                  className="px-3 py-1 text-xs font-medium transition-colors"
                  style={activeOp === op
                    ? { background: accentCol, color: 'white' }
                    : { background: '#1e293b', color: '#94a3b8' }}>
                  {cfg.opLabels[op]}
                </button>
              ))}
            </div>
            <div className="flex rounded-lg overflow-hidden border border-slate-600">
              <button onClick={() => setActiveMetric('ms')}
                className={`px-3 py-1 text-xs font-medium flex items-center gap-1 transition-colors ${
                  activeMetric === 'ms' ? 'bg-yellow-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                <Clock size={10}/> ms
              </button>
              <button onClick={() => setActiveMetric('io')}
                className={`px-3 py-1 text-xs font-medium flex items-center gap-1 transition-colors ${
                  activeMetric === 'io' ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                <HardDrive size={10}/> I/O
              </button>
            </div>
          </div>
        </div>

        <div className="p-4">
          <GroupedBarChart
            tecnicas={cfg.tecnicas} colors={cfg.colors} labels={cfg.labels}
            opKey={opKey} metric={activeMetric}
          />
        </div>

        <div className="border-t border-slate-700 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-slate-800/50">
                <th className="px-4 py-2 text-left text-slate-400">Técnica</th>
                {(benchData?.sizes || []).map(n => (
                  <th key={n} className="px-4 py-2 text-right text-slate-400">
                    n={n >= 1000 ? `${n/1000}k` : n}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {cfg.tecnicas.map(tec => (
                <tr key={tec} className="hover:bg-slate-800/30">
                  <td className="px-4 py-2 flex items-center gap-2 font-medium">
                    <span className="w-2 h-2 rounded-full" style={{ background: cfg.colors[tec] }}/>
                    {cfg.labels[tec]}
                    {tec === 'HASH' && activeOp === 'range' &&
                      <span className="text-[9px] text-yellow-500 bg-yellow-500/10 px-1 rounded ml-1">N/A</span>}
                  </td>
                  {(benchData?.sizes || []).map(n => {
                    const isNA = tec === 'HASH' && activeOp === 'range';
                    const val  = benchData?.results[String(n)]?.[tec]?.[opKey]?.[activeMetric];
                    return (
                      <td key={n} className="px-4 py-2 text-right font-mono">
                        {isNA
                          ? <span className="text-slate-600">N/A</span>
                          : val != null
                            ? <span className="text-slate-200">
                                {activeMetric === 'ms' ? val.toFixed(1) : val.toLocaleString()}
                              </span>
                            : <span className="text-slate-600">—</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-bold text-slate-200 flex items-center gap-2">
          <BarChart2 size={18} className="text-indigo-400" />
          Evaluación Experimental
        </h3>
        <button onClick={loadBenchmark} disabled={loading}
          className="flex items-center gap-2 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-600 text-white text-xs rounded-lg transition-colors">
          <RotateCcw size={12} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Cargando...' : 'Actualizar'}
        </button>
      </div>

      {!benchData?.success ? (
        <div className="bg-[#1e293b] rounded-xl border border-slate-700 p-6 text-center">
          <BarChart2 size={40} className="mx-auto mb-3 text-slate-600" />
          <p className="text-slate-400 text-sm mb-2">
            {benchData?.message || 'No hay datos de benchmark disponibles.'}
          </p>
          <p className="text-slate-500 text-xs font-mono bg-slate-800 inline-block px-3 py-1 rounded">
            python3 benchmark.py
          </p>
        </div>
      ) : (
        <>
          <Section
            title="📊 Índices Tradicionales — B+ Tree · Sequential File · Ext. Hash"
            cfg={TRAD}
            activeOp={tradOp}         setActiveOp={setTradOp}
            activeMetric={tradMetric} setActiveMetric={setTradMetric}
          />
          <Section
            title="🗺️ Índice Espacial — R-Tree"
            cfg={SPAT}
            activeOp={spatOp}         setActiveOp={setSpatOp}
            activeMetric={spatMetric} setActiveMetric={setSpatMetric}
          />
        </>
      )}

      {/* Historial de consultas */}
      {queryHistory.length > 0 && (
        <div className="bg-[#1e293b] rounded-xl border border-slate-700 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-700">
            <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Clock size={12} className="text-indigo-400" />
              Historial de consultas ({queryHistory.length})
            </h4>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-800/50">
                  <th className="px-3 py-2 text-left text-slate-400">#</th>
                  <th className="px-3 py-2 text-left text-slate-400">Consulta</th>
                  <th className="px-3 py-2 text-right text-slate-400">ms</th>
                  <th className="px-3 py-2 text-right text-slate-400">Lecturas</th>
                  <th className="px-3 py-2 text-right text-slate-400">Escrituras</th>
                  <th className="px-3 py-2 text-right text-slate-400">Total I/O</th>
                  <th className="px-3 py-2 text-center text-slate-400">Estado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {[...queryHistory].reverse().map((entry, i) => (
                  <tr key={i} className="hover:bg-slate-800/30">
                    <td className="px-3 py-2 text-slate-500 font-mono">{queryHistory.length - i}</td>
                    <td className="px-3 py-2 font-mono text-indigo-300 max-w-xs truncate" title={entry.sql}>
                      {entry.sql.length > 60 ? entry.sql.slice(0, 60) + '…' : entry.sql}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-yellow-400">{entry.tiempo_ms.toFixed(2)}</td>
                    <td className="px-3 py-2 text-right font-mono text-blue-400">{entry.reads}</td>
                    <td className="px-3 py-2 text-right font-mono text-orange-400">{entry.writes}</td>
                    <td className="px-3 py-2 text-right font-mono text-emerald-400">{entry.total_io}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                        entry.ok ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'
                      }`}>{entry.ok ? 'OK' : 'ERR'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="p-4 border-t border-slate-700">
            <p className="text-xs text-slate-500 mb-2 font-semibold uppercase tracking-wider">
              Tiempo por consulta (últimas {Math.min(queryHistory.length, 20)})
            </p>
            <BarChart
              data={queryHistory.slice(-20).map((e, i) => ({
                label: `#${queryHistory.length - queryHistory.slice(-20).length + i + 1}`,
                value: e.tiempo_ms,
              }))}
              title="ms" color="#6366f1"
            />
          </div>
        </div>
      )}
    </div>
  );
};

const App = () => {
  const [tables, setTables] = useState({});

  const [activeTab, setActiveTab] = useState('console'); // 'console', 'stats', 'rtree', 'benchmark'
  const [sqlQuery, setSqlQuery] = useState('');
  const [queryResult, setQueryResult] = useState(null);
  const [queryError, setQueryError] = useState(null);
  const [notification, setNotification] = useState(null);
  
  // Estados para estadísticas y gestión de índices
  const [stats, setStats] = useState(null);
  const [indexTypes, setIndexTypes] = useState(null);
  const [concurrencyStats, setConcurrencyStats] = useState(null);
  
  // Stats de la última consulta ejecutada
  const [lastQueryStats, setLastQueryStats] = useState(null);
  // Historial de todas las consultas ejecutadas
  const [queryHistory, setQueryHistory] = useState([]);

  // Estado para visualización R-Tree
  const [rtreePoints, setRtreePoints] = useState([]);
  const [rtreeQuery, setRtreeQuery] = useState(null); // { lon, lat, radius }
  const [rtreeTables, setRtreeTables] = useState([]);

  // Notificaciones
  const showNotification = (msg) => {
    setNotification(msg);
    setTimeout(() => setNotification(null), 3000);
  };

  // MOTOR de consultas - comunicación con backend
  const executeQuery = async () => {
    setQueryError(null);
    setQueryResult(null);

    try {
      const response = await fetch('http://localhost:8000/api/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sqlQuery, database_path: "." })
      });

      const result = await response.json();

      // Guardar stats de la última consulta siempre
      const queryStats = {
        sql: sqlQuery,
        tiempo_ms: result.execution_time_ms ?? 0,
        reads: result.io_stats?.reads ?? 0,
        writes: result.io_stats?.writes ?? 0,
        total_io: result.io_stats?.total_io ?? 0,
        ok: result.success,
      };
      setLastQueryStats(queryStats);
      setQueryHistory(prev => [...prev, queryStats]);

      if (result.success) {
        setQueryResult(result.data || []);
        showNotification(result.message || 'Consulta ejecutada con éxito');
        loadStats();

        // Si la consulta es espacial, extraer punto y radio para visualizar
        const spatialMatch = sqlQuery.match(
          /POINT\s*\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)\s*,\s*RADIUS\s+([\d.]+)/i
        );
        const knnMatch = sqlQuery.match(
          /POINT\s*\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)\s*,\s*K\s+(\d+)/i
        );
        if (spatialMatch || knnMatch) {
          const m = spatialMatch || knnMatch;
          setRtreeQuery({
            lon: parseFloat(m[1]),
            lat: parseFloat(m[2]),
            radius: spatialMatch ? parseFloat(m[3]) : null,
            k: knnMatch ? parseInt(m[3]) : null,
            results: result.data || [],
          });
          setActiveTab('rtree');
        }
      } else {
        setQueryError(result.error || result.message || 'Error en la consulta');
        showNotification('Error en la consulta', 'error');
      }
    } catch (err) {
      setQueryError('Error de conexión con el backend: ' + err.message);
      showNotification('No se pudo conectar con el backend', 'error');
    }
  };

  // Cargar tablas desde el backend
  const loadTables = async () => {
    try {
      const ctrl = new AbortController();
      setTimeout(() => ctrl.abort(), 3000);
      const response = await fetch('http://localhost:8000/api/tables?database_path=.', { signal: ctrl.signal });
      const data = await response.json();
      if (data.success) {
        const tablesMap = {};
        const rtree = [];
        data.tables.forEach(table => {
          tablesMap[table.name] = table;
          if (table.index_type === 'RTREE' || table.name.includes('rtree')) {
            rtree.push(table.name);
          }
        });
        setTables(tablesMap);
        setRtreeTables(rtree);
      }
    } catch (err) {
      console.error('Error cargando tablas:', err);
    }
  };

  // Cargar todos los puntos de una tabla R-Tree para visualización
  const loadRtreePoints = async (table) => {
    try {
      const response = await fetch(`http://localhost:8000/api/rtree-points/${table}`);
      const data = await response.json();
      if (data.success) setRtreePoints(data.points || []);
    } catch (err) {
      console.error('Error cargando puntos R-Tree:', err);
    }
  };

  // Cargar estadísticas del backend (optimizado)
  const loadStats = async () => {
    try {
      if (window.loadingStats) return;
      window.loadingStats = true;

      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 3000);

      const [statsResponse, indexTypesResponse, concurrencyResponse] = await Promise.all([
        fetch('http://localhost:8000/api/stats?database_path=.',    { signal: ctrl.signal }),
        fetch('http://localhost:8000/api/index-types',              { signal: ctrl.signal }),
        fetch('http://localhost:8000/api/concurrency/stats',        { signal: ctrl.signal }),
      ]);
      clearTimeout(t);

      const statsData       = await statsResponse.json();
      const indexTypesData  = await indexTypesResponse.json();
      const concurrencyData = await concurrencyResponse.json();

      if (statsData.success)       setStats(statsData.stats);
      if (indexTypesData.success)  setIndexTypes(indexTypesData.index_types);
      if (concurrencyData.success) setConcurrencyStats(concurrencyData.stats);

    } catch {
      // backend no disponible — no bloquear la UI
    } finally {
      window.loadingStats = false;
    }
  };

  // Cargar estadísticas al montar el componente
  useEffect(() => {
    loadTables();
    loadStats();
    // Solo actualizar estadísticas cuando la pestaña esté activa y cada 30 segundos
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') {
        loadTables();
        loadStats();
      }
    }, 30000); // Actualizar cada 30 segundos en lugar de 5
    return () => clearInterval(interval);
  }, []);

  // Panel de estadísticas por archivo
  const StatsPanel = () => {
    if (!stats || !stats.index_files) {
      return (
        <div className="bg-[#1e293b] rounded-xl border border-slate-700 p-4">
          <p className="text-slate-400 text-sm">No hay estadísticas disponibles</p>
        </div>
      );
    }

    return (
      <div className="space-y-4">
        <div className="bg-[#1e293b] rounded-xl border border-slate-700 p-4">
          <h4 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
            <Database size={16} className="text-indigo-400" />
            Estadísticas Generales
          </h4>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
            <div className="bg-slate-800 rounded-lg p-3">
              <p className="text-slate-500 mb-1">Tablas Totales</p>
              <p className="text-xl font-bold text-indigo-400">{stats.index_files.length}</p>
            </div>
            <div className="bg-slate-800 rounded-lg p-3">
              <p className="text-slate-500 mb-1">Total I/O</p>
              <p className="text-xl font-bold text-emerald-400">{stats.total_operations.total_io}</p>
            </div>
            <div className="bg-slate-800 rounded-lg p-3">
              <p className="text-slate-500 mb-1">Lecturas</p>
              <p className="text-xl font-bold text-blue-400">{stats.total_operations.reads}</p>
            </div>
            <div className="bg-slate-800 rounded-lg p-3">
              <p className="text-slate-500 mb-1">Escrituras</p>
              <p className="text-xl font-bold text-orange-400">{stats.total_operations.writes}</p>
            </div>
          </div>
        </div>

        <div className="bg-[#1e293b] rounded-xl border border-slate-700 p-4">
          <h4 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
            <TableIcon size={16} className="text-indigo-400" />
            Estadísticas por Archivo
          </h4>
          <div className="space-y-2">
            {stats.index_files.map((file, index) => (
              <div key={index} className="bg-slate-800 rounded-lg p-3 border border-slate-700">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <p className="text-sm font-medium text-slate-200">{file.name}</p>
                    <p className="text-xs text-slate-500">{file.index_type} • {file.size_kb} KB</p>
                  </div>
                  <span className={`text-xs px-2 py-1 rounded-full ${
                    file.index_type === 'BPTREE' ? 'bg-blue-500/20 text-blue-400' :
                    file.index_type === 'SEQUENTIAL' ? 'bg-green-500/20 text-green-400' :
                    file.index_type === 'HASH' ? 'bg-yellow-500/20 text-yellow-400' :
                    'bg-purple-500/20 text-purple-400'
                  }`}>
                    {file.index_type}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <p className="text-slate-500">Tamaño</p>
                    <p className="font-mono text-slate-300">{file.size_kb} KB</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Modificado</p>
                    <p className="font-mono text-slate-300">{new Date(file.modified).toLocaleTimeString()}</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Archivo</p>
                    <p className="font-mono text-slate-300 truncate">{file.file}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {concurrencyStats && (
          <div className="bg-[#1e293b] rounded-xl border border-slate-700 p-4">
            <h4 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
              <Terminal size={16} className="text-indigo-400" />
              Concurrencia
            </h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
              <div className="bg-slate-800 rounded-lg p-3">
                <p className="text-slate-500 mb-1">Transacciones Activas</p>
                <p className="text-xl font-bold text-yellow-400">{concurrencyStats.active_transactions}</p>
              </div>
              <div className="bg-slate-800 rounded-lg p-3">
                <p className="text-slate-500 mb-1">Completadas</p>
                <p className="text-xl font-bold text-emerald-400">{concurrencyStats.completed_transactions}</p>
              </div>
              <div className="bg-slate-800 rounded-lg p-3">
                <p className="text-slate-500 mb-1">Conflictos</p>
                <p className="text-xl font-bold text-red-400">{concurrencyStats.conflicts_detected}</p>
              </div>
              <div className="bg-slate-800 rounded-lg p-3">
                <p className="text-slate-500 mb-1">Operaciones</p>
                <p className="text-xl font-bold text-blue-400">{concurrencyStats.total_operations}</p>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };


  return (
    <div className="min-h-screen bg-[#0f172a] text-slate-200 font-sans">
      {/* Sidebar */}
      <div className="flex h-screen overflow-hidden">
        
        {/* Panel Izquierdo: Estructura de la DB */}
        <aside className="w-64 bg-[#1e293b] border-r border-slate-700 p-4 hidden md:block">
          <div className="flex items-center gap-2 mb-8 px-2">
            <Database className="text-indigo-400" size={24} />
            <h2 className="font-bold text-lg tracking-tight">PG-Lite Admin</h2>
          </div>

          <div className="space-y-6">
            <TableSidebar tables={tables} onTableClick={(table) => {
              if (table.index_type === 'RTREE' || table.name.includes('rtree')) {
                loadRtreePoints(table.name);
              }
            }} />

          </div>
        </aside>

        {/* Contenido Principal */}
        <main className="flex-1 flex flex-col overflow-hidden bg-[#0f172a]">
          
          {/* Header Superior */}
          <header className="h-16 border-b border-slate-800 bg-[#1e293b]/50 backdrop-blur-md flex items-center justify-between px-8">
            <div className="flex items-center gap-4">
              <div className="md:hidden flex items-center gap-2">
                <Database className="text-indigo-400" size={20} />
                <span className="font-bold">PG-Lite</span>
              </div>
              <nav className="flex gap-6">
                <button 
                  onClick={() => setActiveTab('console')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'console' ? 'border-indigo-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  Consola SQL
                </button>
                <button 
                  onClick={() => setActiveTab('stats')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'stats' ? 'border-indigo-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  Estadísticas
                </button>
                <button 
                  onClick={() => setActiveTab('rtree')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'rtree' ? 'border-purple-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  🗺 R-Tree
                </button>
                <button 
                  onClick={() => setActiveTab('benchmark')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'benchmark' ? 'border-yellow-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  📊 Benchmark
                </button>
              </nav>
            </div>
            
            <div className="flex items-center gap-3">
               <span className="text-[10px] bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 px-2 py-0.5 rounded uppercase font-bold tracking-widest">Connected</span>
            </div>
          </header>

          {/* Area de Trabajo */}
          <section className="flex-1 overflow-auto p-6">
            
            {activeTab === 'stats' ? (
              <div className="animate-in fade-in duration-300 p-6">
                <StatsPanel />
              </div>
            ) : activeTab === 'console' ? (
              <div className="animate-in fade-in duration-300 flex flex-col gap-4">
                <div>
                  <span className="text-xs font-bold text-slate-400 flex items-center gap-2">
                    <Terminal size={14} /> SQL QUERY EDITOR
                  </span>
                  <div className="flex gap-2 mt-3">
                    <button 
                      onClick={() => setSqlQuery('')}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-700 rounded transition-colors"
                      title="Limpiar"
                    >
                      <RotateCcw size={16} />
                    </button>
                    <button 
                      onClick={executeQuery}
                      className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-1.5 rounded-lg flex items-center gap-2 text-sm font-bold transition-all shadow-lg shadow-emerald-900/20"
                    >
                      <Play size={14} fill="currentColor" /> EJECUTAR
                    </button>
                  </div>
                  <textarea 
                    spellCheck="false"
                    className="w-full h-40 bg-[#1e293b] p-4 font-mono text-sm text-indigo-300 outline-none resize-none mt-3"
                    value={sqlQuery}
                    onChange={(e) => setSqlQuery(e.target.value)}
                  />
                  <div className="px-4 py-2 bg-slate-800/30 border-t border-slate-700 flex gap-4 overflow-x-auto whitespace-nowrap scrollbar-hide">
                    <span className="text-[10px] text-slate-500 font-bold self-center">ATAJOS:</span>
                    {Object.keys(tables).length > 0 && (
                      <>
                        <button 
                          onClick={() => setSqlQuery(`SELECT * FROM ${Object.keys(tables)[0]}`)}
                          className="text-[10px] bg-slate-700 hover:bg-indigo-600 text-slate-300 hover:text-white px-2 py-1 rounded transition-colors"
                        >
                          SELECT ALL FROM {Object.keys(tables)[0]}
                        </button>
                        <button 
                          onClick={() => setSqlQuery(`SELECT * FROM ${Object.keys(tables)[0]} LIMIT 10`)}
                          className="text-[10px] bg-slate-700 hover:bg-indigo-600 text-slate-300 hover:text-white px-2 py-1 rounded transition-colors"
                        >
                          SELECT FIRST 10
                        </button>
                        <button 
                          onClick={() => setSqlQuery(`INSERT INTO ${Object.keys(tables)[0]} VALUES (1, 'test', 'test');`)}
                          className="text-[10px] bg-slate-700 hover:bg-indigo-600 text-slate-300 hover:text-white px-2 py-1 rounded transition-colors"
                        >
                          INSERT TEST
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {/* Panel stats última consulta */}
                {lastQueryStats && (
                  <div className={`flex gap-4 text-xs px-4 py-2 rounded-lg border ${lastQueryStats.ok ? 'bg-emerald-900/10 border-emerald-800/40' : 'bg-red-900/10 border-red-800/40'}`}>
                    <span className="text-slate-400 font-bold self-center">ÚLTIMA CONSULTA:</span>
                    <div className="bg-slate-800 rounded px-3 py-1.5">
                      <p className="text-slate-500">Tiempo</p>
                      <p className="font-mono font-bold text-yellow-400">{lastQueryStats.tiempo_ms.toFixed(2)} ms</p>
                    </div>
                    <div className="bg-slate-800 rounded px-3 py-1.5">
                      <p className="text-slate-500">Lecturas disco</p>
                      <p className="font-mono font-bold text-blue-400">{lastQueryStats.reads}</p>
                    </div>
                    <div className="bg-slate-800 rounded px-3 py-1.5">
                      <p className="text-slate-500">Escrituras disco</p>
                      <p className="font-mono font-bold text-orange-400">{lastQueryStats.writes}</p>
                    </div>
                    <div className="bg-slate-800 rounded px-3 py-1.5">
                      <p className="text-slate-500">Total I/O</p>
                      <p className="font-mono font-bold text-emerald-400">{lastQueryStats.total_io}</p>
                    </div>
                  </div>
                )}

                {/* Panel de Resultados */}
                <div className="flex-1 bg-[#020617] rounded-xl border border-slate-700 overflow-hidden flex flex-col min-h-[300px]">
                  <div className="bg-slate-800/50 px-4 py-2 border-b border-slate-700">
                     <span className="text-xs font-bold text-slate-400">DATA OUTPUT</span>
                  </div>
                  
                  <div className="flex-1 overflow-auto">
                    {queryError && (
                      <div className="p-6 flex items-start gap-3 bg-red-900/10 text-red-400">
                        <Info size={20} className="shrink-0" />
                        <div>
                          <p className="font-bold text-sm">Error de Sintaxis</p>
                          <p className="text-xs mt-1 opacity-80">{queryError}</p>
                        </div>
                      </div>
                    )}

                    {queryResult ? (
                      <table className="w-full text-left border-collapse">
                        <thead className="sticky top-0 bg-[#0f172a] text-slate-500 text-[10px] uppercase tracking-wider">
                          <tr>
                            {Object.keys(queryResult[0] || {}).map(key => (
                              <th key={key} className="px-4 py-3 border-b border-slate-800">{key}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/50">
                          {queryResult.map((row, i) => (
                            <tr key={i} className="hover:bg-indigo-900/10 transition-colors">
                              {Object.values(row).map((val, j) => (
                                <td key={j} className="px-4 py-3 text-xs font-mono text-slate-300">{String(val)}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : !queryError && (
                      <div className="h-full flex flex-col items-center justify-center text-slate-600 italic">
                        <Terminal size={48} className="mb-4 opacity-10" />
                        <p>Escribe una consulta y presiona "Ejecutar" para ver los resultados.</p>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : activeTab === 'rtree' ? (
              <RTreeViewer
                rtreeQuery={rtreeQuery}
                rtreePoints={rtreePoints}
                rtreeTables={rtreeTables}
                onLoadPoints={loadRtreePoints}
              />
            ) : activeTab === 'benchmark' ? (
              <div className="animate-in fade-in duration-300 p-6">
                <BenchmarkPanel queryHistory={queryHistory} />
              </div>
            ) : null}
          </section>

          {/* Barra de Estado Inferior */}
          <footer className="h-8 bg-indigo-600 flex items-center px-4 justify-between text-[10px] font-bold text-indigo-100 uppercase tracking-tighter">
            <div className="flex gap-4">
              <span className="flex items-center gap-1"><ChevronRight size={10}/> DB: memory_simulation</span>
              <span className="flex items-center gap-1"><ChevronRight size={10}/> Schema: public</span>
            </div>
            <div>
              Ready to process queries
            </div>
          </footer>
        </main>
      </div>

      {/* Notificacion */}
      {notification && (
        <div className="fixed bottom-12 right-6 bg-emerald-600 text-white px-4 py-2 rounded-lg shadow-xl animate-in fade-in slide-in-from-right-4 duration-300 text-sm font-bold flex items-center gap-2">
          <Info size={16} /> {notification}
        </div>
      )}
    </div>
  );
};

export default App;