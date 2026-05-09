import React, { useState } from 'react';
import {
  Table as TableIcon,
  ChevronDown,
  ChevronRight,
  Columns,
  Key,
} from 'lucide-react';

const TableSidebar = ({ tables }) => {
  // Estado local para manejar la expansión
  const [expandedTable, setExpandedTable] = useState(null);

  const getColumnsForTable = (tableName) => {
    const tableInfo = tables[tableName]?.info;

    // Si aún no se han cargado las columnas, retornar null
    if (!tableInfo || !tableInfo.columnas) {
      return null;
    }

    return tableInfo.columnas.map((col) => ({
      name: col[0],
      type: col[1],
      primary: col[0] === tableInfo.col_clave,
    }));
  };

  const toggleTable = (tableName) => {
    setExpandedTable(expandedTable === tableName ? null : tableName);
  };

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase mb-3 px-2">
          Tablas (Public)
        </p>

        <div className="space-y-1">
          {Object.keys(tables).length > 0 ? (
            Object.keys(tables).map((tableName) => {
              const columns = getColumnsForTable(tableName);

              return (
                <div key={tableName} className="group">
                  {/* Botón de la tabla */}
                  <button
                    onClick={() => toggleTable(tableName)}
                    className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors ${
                      expandedTable === tableName
                        ? 'bg-indigo-600/10 text-indigo-400'
                        : 'hover:bg-slate-800 text-slate-400'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <TableIcon
                        size={16}
                        className={
                          expandedTable === tableName
                            ? 'text-indigo-400'
                            : 'text-slate-500'
                        }
                      />
                      <span className="font-medium">{tableName}</span>
                    </div>

                    {expandedTable === tableName ? (
                      <ChevronDown size={14} className="text-indigo-400" />
                    ) : (
                      <ChevronRight
                        size={14}
                        className="text-slate-600 group-hover:text-slate-400"
                      />
                    )}
                  </button>

                  {/* Columnas */}
                  <div
                    className={`overflow-hidden transition-all duration-300 ease-in-out ${
                      expandedTable === tableName
                        ? 'max-h-64 opacity-100 mt-1'
                        : 'max-h-0 opacity-0'
                    }`}
                  >
                    <div className="ml-7 space-y-1 border-l border-slate-700 pl-3 py-1">
                      {/* Mostrar "Cargando..." si las columnas aún no están disponibles */}
                      {columns === null ? (
                        <div className="px-2 py-2 text-xs text-slate-500 italic">
                          Cargando columnas...
                        </div>
                      ) : columns.length > 0 ? (
                        columns.map((col, index) => (
                          <div
                            key={index}
                            className="flex items-center justify-between py-1 px-2 rounded hover:bg-slate-800/50 transition-colors"
                          >
                            {/* Nombre de la columna */}
                            <div className="flex items-center gap-2">
                              {col.primary ? (
                                <Key
                                  size={12}
                                  className="text-amber-400"
                                />
                              ) : (
                                <Columns
                                  size={12}
                                  className="text-slate-500"
                                />
                              )}

                              <span className="text-xs text-slate-300">
                                {col.name}
                              </span>
                            </div>

                            {/* Badges */}
                            <div className="flex items-center gap-1">
                              {/* Badge PRIMARY KEY */}
                              {col.primary && (
                                <span className="text-[9px] font-semibold uppercase tracking-wide text-amber-300 bg-amber-500/10 px-1.5 py-0.5 rounded border border-amber-500/20">
                                  PK
                                </span>
                              )}

                              {/* Badge del tipo */}
                              <span className="text-[10px] font-mono text-slate-500 bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800">
                                {col.type}
                              </span>
                            </div>
                          </div>
                        ))
                      ) : (
                        <div className="px-2 py-2 text-xs text-slate-500 italic">
                          No hay columnas
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="text-slate-500 text-sm px-3 py-2">
              No hay tablas creadas aún
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default TableSidebar;