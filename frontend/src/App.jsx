import React, { useState, useMemo, useEffect } from 'react';
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
  Download
} from 'lucide-react';


const App = () => {
  const [tables, setTables] = useState({});

  const [activeTab, setActiveTab] = useState('console'); // 'console', 'stats', 'upload'
  const [sqlQuery, setSqlQuery] = useState('');
  const [queryResult, setQueryResult] = useState(null);
  const [queryError, setQueryError] = useState(null);
  const [notification, setNotification] = useState(null);
  
  // Estados para estadísticas y gestión de índices
  const [stats, setStats] = useState(null);
  const [indexTypes, setIndexTypes] = useState(null);
  const [selectedIndexType, setSelectedIndexType] = useState('BPTREE');
  const [concurrencyStats, setConcurrencyStats] = useState(null);
  
  // Estados para manejo de archivos CSV
  const [csvFile, setCsvFile] = useState(null);
  const [tableName, setTableName] = useState('');
  const [tableColumns, setTableColumns] = useState('');

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
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: sqlQuery,
          database_path: "."
        })
      });

      const result = await response.json();

      if (result.success) {
        setQueryResult(result.data || []);
        showNotification(result.message || 'Consulta ejecutada con éxito');
        // Actualizar estadísticas después de cada consulta
        loadStats();
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
      const response = await fetch('http://localhost:8000/api/tables?database_path=.');
      const data = await response.json();
      if (data.success) {
        const tablesMap = {};
        data.tables.forEach(table => {
          tablesMap[table.name] = table;
        });
        setTables(tablesMap);
      }
    } catch (err) {
      console.error('Error cargando tablas:', err);
    }
  };

  // Cargar estadísticas del backend (optimizado)
  const loadStats = async () => {
    try {
      // Solo hacer peticiones si no hay una en curso
      if (window.loadingStats) return;
      window.loadingStats = true;

      const [statsResponse, indexTypesResponse, concurrencyResponse] = await Promise.all([
        fetch('http://localhost:8000/api/stats?database_path=.'),
        fetch('http://localhost:8000/api/index-types'),
        fetch('http://localhost:8000/api/concurrency/stats')
      ]);

      const statsData = await statsResponse.json();
      const indexTypesData = await indexTypesResponse.json();
      const concurrencyData = await concurrencyResponse.json();

      if (statsData.success) setStats(statsData.stats);
      if (indexTypesData.success) setIndexTypes(indexTypesData.index_types);
      if (concurrencyData.success) setConcurrencyStats(concurrencyData.stats);

    } catch (err) {
      console.error('Error cargando estadísticas:', err);
    } finally {
      window.loadingStats = false;
    }
  };

  // Autocompletar campos basados en el nombre del archivo CSV
  const autoFillFields = (fileName) => {
    const fileNameLower = fileName.toLowerCase();
    
    // Mapeo de archivos conocidos a sus configuraciones
    const fileConfigs = {
      'books_1000.csv': {
        tableName: 'books',
        columns: 'id INT INDEX BPTREE, title TEXT, author TEXT, pages INT, average_rating FLOAT, published_date INT'
      },
      'books_10000.csv': {
        tableName: 'books',
        columns: 'id INT INDEX BPTREE, title TEXT, author TEXT, pages INT, average_rating FLOAT, published_date INT'
      },
      'books_100000.csv': {
        tableName: 'books',
        columns: 'id INT INDEX BPTREE, title TEXT, author TEXT, pages INT, average_rating FLOAT, published_date INT'
      },
      'books_1000000.csv': {
        tableName: 'books',
        columns: 'id INT INDEX BPTREE, title TEXT, author TEXT, pages INT, average_rating FLOAT, published_date INT'
      },
    };

    const config = fileConfigs[fileNameLower];
    if (config) {
      setTableName(config.tableName);
      setTableColumns(config.columns);
    }
  };

  // Crear tabla desde archivo CSV
  const createTableFromCSV = async () => {
    if (!csvFile || !tableName || !tableColumns) {
      showNotification('Por favor completa todos los campos', 'error');
      return;
    }

    try {
      // Parsear las columnas del textarea
      const columns = tableColumns.split(',').map(col => {
        const parts = col.trim().split(' ');
        return {
          name: parts[0],
          type: parts[1] || 'TEXT',
          index: parts.includes('INDEX')
        };
      });

      // Crear FormData para enviar el archivo
      const formData = new FormData();
      formData.append('table_name', tableName);
      formData.append('columns', JSON.stringify(columns.reduce((acc, col) => {
        acc[col.name] = col.type;
        return acc;
      }, {})));
      formData.append('file', csvFile);  // Enviar el archivo real
      formData.append('file_path', csvFile.name);
      formData.append('database_path', '.');

      const response = await fetch('http://localhost:8000/api/create-table-from-file', {
        method: 'POST',
        body: formData
      });

      const result = await response.json();
      
      if (result.success) {
        showNotification(`Tabla ${tableName} creada con ${result.records_loaded} registros`);
        loadStats();
        // Limpiar formulario
        setCsvFile(null);
        setTableName('');
        setTableColumns('');
        document.getElementById('csv-input').value = '';
        document.getElementById('columns-input').value = '';
      } else {
        showNotification('Error creando tabla: ' + (result.error || result.message), 'error');
      }

    } catch (err) {
      showNotification('Error de conexión: ' + err.message, 'error');
    }
  };

  // Crear tabla con índice específico
  const createTableWithIndex = async (tableName, columns, indexType) => {
    try {
      const columnsDef = columns.map(col => `${col.name} ${col.type}${col.index ? ` INDEX ${indexType}` : ''}`).join(', ');
      const createSQL = `CREATE TABLE ${tableName} (${columnsDef});`;

      const response = await fetch('http://localhost:8000/api/execute', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: createSQL,
          database_path: "."
        })
      });

      const result = await response.json();
      
      if (result.success) {
        showNotification(`Tabla ${tableName} creada con índice ${indexType}`);
        loadStats();
      } else {
        showNotification('Error creando tabla: ' + (result.error || result.message), 'error');
      }

    } catch (err) {
      showNotification('Error de conexión: ' + err.message, 'error');
    }
  };

  // Ejecutar benchmark de índice
  const runBenchmark = async (tableName, column, indexType) => {
    try {
      const response = await fetch('http://localhost:8000/api/index-performance', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          table_name: tableName,
          index_type: indexType,
          column_name: column
        })
      });

      const result = await response.json();
      
      if (result.success) {
        showNotification(`Benchmark completado: ${result.summary.successful_operations}/${result.summary.total_operations} operaciones exitosas`);
        console.log('Benchmark results:', result);
      } else {
        showNotification('Error en benchmark: ' + (result.error || result.message), 'error');
      }

    } catch (err) {
      showNotification('Error de conexión: ' + err.message, 'error');
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
            <div>
              <p className="text-xs font-semibold text-slate-500 uppercase mb-3 px-2">Tablas (Public)</p>
              <div className="space-y-1">
                {/* Tablas dinámicas desde el backend */}
                <div className="space-y-1">
                  {Object.keys(tables).length > 0 ? (
                    Object.keys(tables).map(tableName => (
                      <button 
                        key={tableName}
                        onClick={() => setActiveTab('console')}
                        className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${activeTab === 'console' ? 'bg-indigo-600/20 text-indigo-400' : 'hover:bg-slate-800 text-slate-400'}`}
                      >
                        <TableIcon size={16} /> {tableName}
                      </button>
                    ))
                  ) : (
                    <div className="text-slate-500 text-sm px-3 py-2">
                      No hay tablas creadas aún
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div>
              <p className="text-xs font-semibold text-slate-500 uppercase mb-3 px-2">Herramientas</p>
              <button 
                onClick={() => setActiveTab('upload')}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${activeTab === 'upload' ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/20' : 'hover:bg-slate-800 text-slate-400'}`}
              >
                <Download size={16} /> Subir CSV
              </button>
            </div>
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
                  onClick={() => setActiveTab('upload')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'upload' ? 'border-indigo-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  Subir CSV
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
            ) : activeTab === 'upload' ? (
              <div className="animate-in fade-in duration-300 p-6">
                <div className="max-w-2xl mx-auto">
                  <h3 className="text-xl font-semibold mb-6 flex items-center gap-2">
                    <Download className="text-slate-500" /> Subir Archivo CSV
                  </h3>
                  
                  <div className="bg-[#1e293b] rounded-xl border border-slate-700 p-6 space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-2">Nombre de la Tabla</label>
                      <input
                        type="text"
                        value={tableName}
                        onChange={(e) => setTableName(e.target.value)}
                        placeholder="ej: products, books, clients, etc."
                        className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-2">Definición de Columnas</label>
                      <textarea
                        id="columns-input"
                        value={tableColumns}
                        onChange={(e) => setTableColumns(e.target.value)}
                        placeholder="id INT INDEX BPTREE, name TEXT, email TEXT INDEX HASH, ..."
                        className="w-full h-20 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono text-sm"
                      />
                      <p className="text-xs text-slate-500 mt-1">Formato: columna1 TIPO [INDEX TIPO_INDICE], columna2 TIPO, ...</p>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-2">Archivo CSV</label>
                      <input
                        id="csv-input"
                        type="file"
                        accept=".csv"
                        onChange={(e) => {
              const file = e.target.files[0];
              setCsvFile(file);
              if (file) {
                autoFillFields(file.name);
              }
            }}
                        className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-200 file:mr-2 file:py-1 file:px-3"
                      />
                      {csvFile && (
                        <p className="text-sm text-slate-400 mt-2">
                          Archivo seleccionado: <span className="text-indigo-400">{csvFile.name}</span>
                        </p>
                      )}
                    </div>
                    
                    <div className="flex gap-2">
                      <select
                        value={selectedIndexType}
                        onChange={(e) => setSelectedIndexType(e.target.value)}
                        className="flex-1 px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-slate-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      >
                        <option value="BPTREE">B+ Tree</option>
                        <option value="SEQUENTIAL">Sequential File</option>
                        <option value="HASH">Extendible Hash</option>
                        <option value="RTREE">R-Tree</option>
                      </select>
                      
                      <button
                        onClick={createTableFromCSV}
                        disabled={!csvFile || !tableName || !tableColumns}
                        className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg flex items-center justify-center gap-2 transition-all"
                      >
                        <Download size={16} />
                        Crear Tabla desde CSV
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ):null}
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