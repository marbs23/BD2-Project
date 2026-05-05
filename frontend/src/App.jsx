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
  const [tables, setTables] = useState({
    users: [
      { id: 1, name: 'Alejandro García', email: 'ale@example.com', role_id: 1, status: 'Activo' },
      { id: 2, name: 'María López', email: 'maria@example.com', role_id: 2, status: 'Inactivo' },
      { id: 3, name: 'Roberto Sanz', email: 'roberto@example.com', role_id: 3, status: 'Activo' },
      { id: 4, name: 'Lucía Fernández', email: 'lucia@example.com', role_id: 3, status: 'Activo' },
    ],
    roles: [
      { id: 1, role_name: 'Administrador', permissions: 'Full Access' },
      { id: 2, role_name: 'Editor', permissions: 'Write/Read' },
      { id: 3, role_name: 'Usuario', permissions: 'Read Only' },
    ]
  });

  const [activeTab, setActiveTab] = useState('explorer'); // 'explorer' o 'console'
  const [sqlQuery, setSqlQuery] = useState('SELECT users.name, roles.role_name \nFROM users \nJOIN roles ON users.role_id = roles.id');
  const [queryResult, setQueryResult] = useState(null);
  const [queryError, setQueryError] = useState(null);
  const [notification, setNotification] = useState(null);

  // Notificaciones
  const showNotification = (msg) => {
    setNotification(msg);
    setTimeout(() => setNotification(null), 3000);
  };

  // MOTOR de consultas
  const executeQuery = () => {
    setQueryError(null);
    const query = sqlQuery.toLowerCase().trim().replace(/\s+/g, ' ');

    try {
      if (query === 'select * from users') {
        setQueryResult(tables.users);
        return;
      }

      if (query === 'select * from roles') {
        setQueryResult(tables.roles);
        return;
      }

      if (query.includes('join') && query.includes('on')) {
        const joinedData = tables.users.map(user => {
          const role = tables.roles.find(r => r.id === user.role_id);
          return {
            id: user.id,
            user_name: user.name,
            email: user.email,
            role_name: role ? role.role_name : 'N/A',
            permissions: role ? role.permissions : 'N/A'
          };
        });
        setQueryResult(joinedData);
        showNotification('Query JOIN ejecutada con éxito');
        return;
      }

      if (query.includes('where status = \'activo\'')) {
        setQueryResult(tables.users.filter(u => u.status === 'Activo'));
        return;
      }

      throw new Error("Sintaxis no soportada en este simulador. Prueba con: SELECT * FROM users o un JOIN entre users y roles.");

    } catch (err) {
      setQueryError(err.message);
      setQueryResult(null);
    }
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
                <button 
                  onClick={() => setActiveTab('explorer')}
                  className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${activeTab === 'explorer' ? 'bg-indigo-600/20 text-indigo-400' : 'hover:bg-slate-800 text-slate-400'}`}
                >
                  <TableIcon size={16} /> users
                </button>
                <button className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-slate-400 hover:bg-slate-800 transition-colors">
                  <TableIcon size={16} /> roles
                </button>
              </div>
            </div>

            <div>
              <p className="text-xs font-semibold text-slate-500 uppercase mb-3 px-2">Herramientas</p>
              <button 
                onClick={() => setActiveTab('console')}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${activeTab === 'console' ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/20' : 'hover:bg-slate-800 text-slate-400'}`}
              >
                <Terminal size={16} /> Query Console
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
                  onClick={() => setActiveTab('explorer')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'explorer' ? 'border-indigo-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  Explorador de Datos
                </button>
                <button 
                  onClick={() => setActiveTab('console')}
                  className={`text-sm font-medium pb-5 pt-5 border-b-2 transition-all ${activeTab === 'console' ? 'border-indigo-500 text-white' : 'border-transparent text-slate-400 hover:text-slate-200'}`}
                >
                  Consola SQL
                </button>
              </nav>
            </div>
            
            <div className="flex items-center gap-3">
               <span className="text-[10px] bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 px-2 py-0.5 rounded uppercase font-bold tracking-widest">Connected</span>
            </div>
          </header>

          {/* Area de Trabajo */}
          <section className="flex-1 overflow-auto p-6">
            
            {activeTab === 'explorer' ? (
              <div className="animate-in fade-in duration-300">
                <div className="flex justify-between items-center mb-6">
                  <h3 className="text-xl font-semibold flex items-center gap-2">
                    <TableIcon className="text-slate-500" /> Tabla: users
                  </h3>
                  <button className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm px-4 py-2 rounded-lg flex items-center gap-2 transition-all">
                    <Plus size={16} /> Añadir Fila
                  </button>
                </div>
                
                <div className="bg-[#1e293b] rounded-xl border border-slate-700 overflow-hidden shadow-2xl">
                  <table className="w-full text-left border-collapse">
                    <thead className="bg-slate-800/50 text-slate-400 text-xs uppercase">
                      <tr>
                        <th className="px-6 py-4 border-b border-slate-700">id</th>
                        <th className="px-6 py-4 border-b border-slate-700">name</th>
                        <th className="px-6 py-4 border-b border-slate-700">email</th>
                        <th className="px-6 py-4 border-b border-slate-700">role_id</th>
                        <th className="px-6 py-4 border-b border-slate-700 text-right">Acciones</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-700/50">
                      {tables.users.map(user => (
                        <tr key={user.id} className="hover:bg-slate-800/30 transition-colors">
                          <td className="px-6 py-4 font-mono text-indigo-400 text-sm">{user.id}</td>
                          <td className="px-6 py-4 text-sm font-medium">{user.name}</td>
                          <td className="px-6 py-4 text-sm text-slate-400">{user.email}</td>
                          <td className="px-6 py-4 text-sm">
                            <span className="bg-slate-700 px-2 py-0.5 rounded text-xs">{user.role_id}</span>
                          </td>
                          <td className="px-6 py-4 text-right">
                             <button className="text-slate-500 hover:text-red-400 p-1"><Trash2 size={16} /></button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="h-full flex flex-col gap-4 animate-in slide-in-from-bottom-4 duration-300">
                {/* Editor de SQL */}
                <div className="flex-none bg-[#1e293b] rounded-xl border border-slate-700 overflow-hidden shadow-xl">
                  <div className="bg-slate-800 px-4 py-2 border-b border-slate-700 flex justify-between items-center">
                    <span className="text-xs font-bold text-slate-400 flex items-center gap-2">
                      <Terminal size={14} /> SQL QUERY EDITOR
                    </span>
                    <div className="flex gap-2">
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
                  </div>
                  <textarea 
                    spellCheck="false"
                    className="w-full h-40 bg-[#1e293b] p-4 font-mono text-sm text-indigo-300 outline-none resize-none"
                    value={sqlQuery}
                    onChange={(e) => setSqlQuery(e.target.value)}
                  />
                  <div className="px-4 py-2 bg-slate-800/30 border-t border-slate-700 flex gap-4 overflow-x-auto whitespace-nowrap scrollbar-hide">
                    <span className="text-[10px] text-slate-500 font-bold self-center">ATAJOS:</span>
                    <button 
                      onClick={() => setSqlQuery('SELECT * FROM users')}
                      className="text-[10px] bg-slate-700 hover:bg-indigo-600 text-slate-300 hover:text-white px-2 py-1 rounded transition-colors"
                    >
                      SELECT ALL USERS
                    </button>
                    <button 
                      onClick={() => setSqlQuery('SELECT users.name, roles.role_name \nFROM users \nJOIN roles ON users.role_id = roles.id')}
                      className="text-[10px] bg-slate-700 hover:bg-indigo-600 text-slate-300 hover:text-white px-2 py-1 rounded transition-colors"
                    >
                      JOIN USERS + ROLES
                    </button>
                    <button 
                      onClick={() => setSqlQuery('SELECT * FROM users WHERE status = \'Activo\'')}
                      className="text-[10px] bg-slate-700 hover:bg-indigo-600 text-slate-300 hover:text-white px-2 py-1 rounded transition-colors"
                    >
                      FILTER ACTIVE
                    </button>
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
            )}
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