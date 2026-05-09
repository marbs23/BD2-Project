import os
import json
import traceback
from typing import Dict, List, Any, Optional
from datetime import datetime

# FastAPI imports
from fastapi import FastAPI, HTTPException, Request, Response, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Importaciones del proyecto
from parser_sql import parsear
from ejecutor import Ejecutor, ResultadoEjecucion

# Modelos de datos para la API
class SQLQuery(BaseModel):
    query: str
    database_path: Optional[str] = "."

class CreateTableRequest(BaseModel):
    table_name: str
    columns: Dict[str, str]
    database_path: Optional[str] = "."

class CreateTableFromFileRequest(BaseModel):
    table_name: str
    columns: Dict[str, str]
    file_path: str
    database_path: Optional[str] = "."

class IndexInfo(BaseModel):
    table_name: str
    index_type: str  # BPTREE, SEQUENTIAL, HASH, RTREE
    column_name: str

class TransactionRequest(BaseModel):
    transaction_id: str
    operations: List[str]
    database_path: Optional[str] = "."

class ConcurrencyStats(BaseModel):
    active_transactions: int
    completed_transactions: int
    conflicts_detected: int
    total_operations: int

class ExecuteResponse(BaseModel):
    success: bool
    data: Optional[List[Dict[str, Any]]] = None
    message: str
    execution_time_ms: Optional[float] = None
    io_stats: Optional[Dict[str, int]] = None
    error: Optional[str] = None

# Inicialización de la aplicación FastAPI
app = FastAPI(
    title="BD2 API",
    description="Backend para el parser SQL del proyecto BD2",
    version="1.0.0"
)

# Configuración de CORS para comunicación con el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Variable global para el ejecutor
ejecutor_global: Optional[Ejecutor] = None

# Variables para el simulador de concurrencia
transaction_log: List[Dict[str, Any]] = []
active_transactions: Dict[str, Dict[str, Any]] = {}
concurrency_stats = {
    "active_transactions": 0,
    "completed_transactions": 0,
    "conflicts_detected": 0,
    "total_operations": 0
}

def get_ejecutor(database_path: str = ".") -> Ejecutor:
    """Obtiene o crea una instancia del ejecutor"""
    global ejecutor_global
    if ejecutor_global is None:
        ejecutor_global = Ejecutor(database_path)
    return ejecutor_global

@app.on_event("shutdown")
async def shutdown_event():
    """Limpia recursos al cerrar la aplicación"""
    global ejecutor_global
    if ejecutor_global:
        ejecutor_global.cerrar_todo()

@app.get("/")
async def root():
    """Endpoint principal - información de la API"""
    return {
        "message": "BD2 Parser API Backend",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    """Endpoint de health check"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "parser_available": True,
        "executor_available": True
    }

@app.post("/api/execute", response_model=ExecuteResponse)
async def execute_sql(request: SQLQuery):
    """
    Ejecuta una consulta SQL usando el parser y ejecutor del proyecto
    
    Args:
        request: Objeto con la consulta SQL y ruta de la base de datos
        
    Returns:
        Resultado de la ejecución con datos, estadísticas y mensajes
    """
    try:
        # Obtener instancia del ejecutor
        ejecutor = get_ejecutor(request.database_path)
        
        # Parsear y ejecutar la consulta
        start_time = datetime.now()
        resultados = ejecutor.ejecutar(request.query)
        end_time = datetime.now()
        
        execution_time_ms = (end_time - start_time).total_seconds() * 1000
        
        # Procesar resultados
        if not resultados:
            return ExecuteResponse(
                success=True,
                message="Consulta ejecutada sin resultados",
                execution_time_ms=execution_time_ms
            )
        
        # Convertir resultados a formato JSON
        response_data = []
        total_io = 0
        total_reads = 0
        total_writes = 0
        success_count = 0
        
        for resultado in resultados:
            if resultado.ok:
                success_count += 1
                
                # Convertir registros a diccionarios
                registros_data = []
                # Campos internos de indexación que no deben mostrarse al usuario
                _campos_internos = {"next_file", "next_pos"}
                for registro in resultado.registros:
                    if hasattr(registro, '__dict__'):
                        d = {k: v for k, v in registro.__dict__.items()
                             if k not in _campos_internos}
                        registros_data.append(d)
                    elif hasattr(registro, '_asdict'):
                        d = {k: v for k, v in registro._asdict().items()
                             if k not in _campos_internos}
                        registros_data.append(d)
                    else:
                        registros_data.append({"data": str(registro)})
                
                response_data.extend(registros_data)
                total_io += resultado.total_io
                total_reads += resultado.reads
                total_writes += resultado.writes
        
        # Determinar si la operación fue exitosa
        overall_success = success_count == len(resultados)
        
        return ExecuteResponse(
            success=overall_success,
            data=response_data if response_data else None,
            message=f"Se ejecutaron {len(resultados)} sentencias. {success_count} exitosas.",
            execution_time_ms=execution_time_ms,
            io_stats={"total_io": total_io, "reads": total_reads, "writes": total_writes},
            error=None if overall_success else f"Algunas sentencias fallaron: {len(resultados) - success_count}"
        )
        
    except SyntaxError as e:
        return ExecuteResponse(
            success=False,
            message="Error de sintaxis SQL",
            error=str(e)
        )
    except Exception as e:
        return ExecuteResponse(
            success=False,
            message="Error interno del servidor",
            error=f"{str(e)}\n{traceback.format_exc()}"
        )

@app.post("/api/parse")
async def parse_sql(request: SQLQuery):
    """
    Parsea una consulta SQL sin ejecutarla (solo análisis sintáctico)
    
    Args:
        request: Objeto con la consulta SQL
        
    Returns:
        AST del análisis sintáctico
    """
    try:
        resultado_parseo = parsear(request.query)
        
        return {
            "success": True,
            "message": "Consulta parseada exitosamente",
            "ast": str(resultado_parseo),
            "sentences_count": len(resultado_parseo.sentencias) if hasattr(resultado_parseo, 'sentencias') else 0
        }
        
    except SyntaxError as e:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "Error de sintaxis SQL",
                "error": str(e)
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error interno del servidor",
                "error": str(e)
            }
        )

@app.get("/api/tables")
async def list_tables(database_path: str = "."):
    """
    Lista las tablas disponibles en la base de datos
    
    Args:
        database_path: Ruta a la base de datos
        
    Returns:
        Lista de tablas existentes
    """
    try:
        ejecutor = get_ejecutor(database_path)
        
        # Intentar obtener información de tablas existentes
        # Esto depende de la implementación específica del ejecutor
        tables_info = []
        
        # Escanear archivos de índices en el directorio
        if os.path.exists(database_path):
            for file in os.listdir(database_path):
                if file.endswith('.bin'):
                    table_name = file.replace('.bin', '')
                    tables_info.append({
                        "name": table_name,
                        "type": "index_file",
                        "file": file
                    })
        
        return {
            "success": True,
            "tables": tables_info,
            "database_path": database_path
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error al listar tablas",
                "error": str(e)
            }
        )

@app.post("/api/create-table")
async def create_table(request: CreateTableRequest):
    """
    Crea una nueva tabla usando la sintaxis CREATE TABLE
    
    Args:
        request: Información para crear la tabla
        
    Returns:
        Resultado de la operación
    """
    try:
        # Construir consulta CREATE TABLE
        columns_def = []
        for col_name, col_type in request.columns.items():
            columns_def.append(f"{col_name} {col_type}")
        
        create_sql = f"CREATE TABLE {request.table_name} ({', '.join(columns_def)});"
        
        # Ejecutar la consulta
        ejecutor = get_ejecutor(request.database_path)
        resultados = ejecutor.ejecutar(create_sql)
        
        success = all(r.ok for r in resultados)
        
        return {
            "success": success,
            "message": f"Tabla '{request.table_name}' {'creada exitosamente' if success else 'falló al crearse'}",
            "sql": create_sql,
            "results": [{"ok": r.ok, "message": r.mensaje} for r in resultados]
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error al crear tabla",
                "error": str(e)
            }
        )

@app.post("/api/create-table-from-file")
async def create_table_from_file(table_name: str = Form(...), 
                           columns: str = Form(...), 
                           file: UploadFile = File(...),
                           file_path: str = Form(...),
                           database_path: str = Form(default=".")):
    """
    Crea una nueva tabla y carga datos desde un archivo CSV
    
    Args:
        table_name: Nombre de la tabla
        columns: Definición de columnas en formato JSON
        file_path: Ruta del archivo CSV
        database_path: Ruta de la base de datos
        
    Returns:
        Resultado de la operación
    """
    try:
        # Parsear columnas desde JSON
        import json
        columns_dict = json.loads(columns)
        
        # Guardar el archivo CSV temporalmente
        temp_file_path = f"temp_{file_path}"
        with open(temp_file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        # Construir consulta CREATE TABLE FROM FILE
        columns_def = []
        for col_name, col_type in columns_dict.items():
            columns_def.append(f"{col_name} {col_type}")
        
        create_sql = f"CREATE TABLE {table_name} ({', '.join(columns_def)}) FROM FILE \"{temp_file_path}\";"
        
        # Ejecutar la consulta
        ejecutor = get_ejecutor(database_path)
        resultados = ejecutor.ejecutar(create_sql)
        
        success = all(r.ok for r in resultados)
        
        # Obtener estadísticas de carga
        total_records = 0
        for r in resultados:
            if r.ok and r.afectados > 0:
                total_records += r.afectados
        
        # Limpiar archivo temporal
        try:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        except:
            pass
        
        return {
            "success": success,
            "message": f"Tabla '{table_name}' {'creada y cargada exitosamente' if success else 'falló al crearse'}",
            "sql": create_sql,
            "records_loaded": total_records,
            "results": [{"ok": r.ok, "message": r.mensaje, "affected": r.afectados} for r in resultados]
        }
        
    except Exception as e:
        # Limpiar archivo temporal en caso de error
        try:
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        except:
            pass
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error al crear tabla desde archivo",
                "error": str(e)
            }
        )

@app.get("/api/stats")
async def get_database_stats(database_path: str = "."):
    """
    Obtiene estadísticas de la base de datos y los índices
    
    Args:
        database_path: Ruta a la base de datos
        
    Returns:
        Estadísticas de rendimiento y uso
    """
    try:
        ejecutor = get_ejecutor(database_path)
        
        stats = {
            "database_path": database_path,
            "timestamp": datetime.now().isoformat(),
            "index_files": [],
            "total_operations": {
                "reads": 0,
                "writes": 0,
                "total_io": 0
            }
        }
        
        # Escanear archivos de índices y obtener estadísticas
        if os.path.exists(database_path):
            for file in os.listdir(database_path):
                if file.endswith('.bin'):
                    file_path = os.path.join(database_path, file)
                    file_stats = os.stat(file_path)
                    
                    index_info = {
                        "name": file.replace('.bin', ''),
                        "file": file,
                        "size_bytes": file_stats.st_size,
                        "size_kb": round(file_stats.st_size / 1024, 2),
                        "modified": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                        "index_type": "UNKNOWN"
                    }
                    
                    # Determinar tipo de índice por el nombre del archivo
                    if "bpt" in file.lower():
                        index_info["index_type"] = "BPTREE"
                    elif "seq" in file.lower():
                        index_info["index_type"] = "SEQUENTIAL"
                    elif "hash" in file.lower():
                        index_info["index_type"] = "HASH"
                    elif "rtree" in file.lower():
                        index_info["index_type"] = "RTREE"
                    
                    stats["index_files"].append(index_info)
        
        return {
            "success": True,
            "stats": stats
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error al obtener estadísticas",
                "error": str(e)
            }
        )

@app.get("/api/index-types")
async def get_index_types():
    """
    Retorna los tipos de índices disponibles en el sistema
    
    Returns:
        Lista de tipos de índices soportados
    """
    try:
        index_types = {
            "BPTREE": {
                "name": "B+ Tree",
                "operations": ["add", "search", "rangeSearch", "remove"],
                "supports_range_search": True,
                "description": "Índice de árbol B+ para búsquedas eficientes y por rango"
            },
            "SEQUENTIAL": {
                "name": "Sequential File",
                "operations": ["add", "search", "rangeSearch", "remove"],
                "supports_range_search": True,
                "description": "Archivo secuencial con archivo auxiliar de desbordamiento"
            },
            "HASH": {
                "name": "Extendible Hashing",
                "operations": ["add", "search", "remove"],
                "supports_range_search": False,
                "description": "Hashing extensibles con directorio dinámico"
            },
            "RTREE": {
                "name": "R-Tree",
                "operations": ["rangeSearch", "kNN"],
                "supports_range_search": True,
                "description": "Índice espacial para datos geográficos"
            }
        }
        
        return {
            "success": True,
            "index_types": index_types
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error al obtener tipos de índices",
                "error": str(e)
            }
        )

@app.post("/api/index-performance")
async def benchmark_index(index_info: IndexInfo):
    """
    Ejecuta pruebas de rendimiento sobre un índice específico
    
    Args:
        index_info: Información del índice a probar
        
    Returns:
        Resultados de benchmark
    """
    try:
        ejecutor = get_ejecutor(".")
        
        # Ejecutar operaciones de prueba según el tipo de índice
        test_operations = []
        
        if index_info.index_type == "BPTREE":
            test_operations = [
                f"INSERT INTO {index_info.table_name} VALUES (1, 'test1');",
                f"SELECT * FROM {index_info.table_name} WHERE {index_info.column_name} = 1;",
                f"SELECT * FROM {index_info.table_name} WHERE {index_info.column_name} BETWEEN 1 AND 10;",
                f"DELETE FROM {index_info.table_name} WHERE {index_info.column_name} = 1;"
            ]
        elif index_info.index_type == "SEQUENTIAL":
            test_operations = [
                f"INSERT INTO {index_info.table_name} VALUES (1, 'test1');",
                f"SELECT * FROM {index_info.table_name} WHERE {index_info.column_name} = 1;",
                f"SELECT * FROM {index_info.table_name} WHERE {index_info.column_name} BETWEEN 1 AND 10;",
                f"DELETE FROM {index_info.table_name} WHERE {index_info.column_name} = 1;"
            ]
        elif index_info.index_type == "HASH":
            test_operations = [
                f"INSERT INTO {index_info.table_name} VALUES (1, 'test1');",
                f"SELECT * FROM {index_info.table_name} WHERE {index_info.column_name} = 1;",
                f"DELETE FROM {index_info.table_name} WHERE {index_info.column_name} = 1;"
            ]
        elif index_info.index_type == "RTREE":
            test_operations = [
                f"INSERT INTO {index_info.table_name} VALUES (1, 'test1', 12.5, -77.0);",
                f"SELECT * FROM {index_info.table_name} WHERE coords IN (POINT(12.5, -77.0), RADIUS 10);",
                f"SELECT * FROM {index_info.table_name} WHERE coords IN (POINT(12.5, -77.0), K 5);"
            ]
        
        benchmark_results = []
        
        for operation in test_operations:
            try:
                start_time = datetime.now()
                resultados = ejecutor.ejecutar(operation)
                end_time = datetime.now()
                
                execution_time_ms = (end_time - start_time).total_seconds() * 1000
                
                if resultados:
                    result = resultados[0]
                    benchmark_results.append({
                        "operation": operation,
                        "success": result.ok,
                        "execution_time_ms": execution_time_ms,
                        "disk_reads": result.reads,
                        "disk_writes": result.writes,
                        "total_io": result.total_io,
                        "message": result.mensaje
                    })
                else:
                    benchmark_results.append({
                        "operation": operation,
                        "success": False,
                        "execution_time_ms": execution_time_ms,
                        "disk_reads": 0,
                        "disk_writes": 0,
                        "total_io": 0,
                        "message": "No results returned"
                    })
                    
            except Exception as e:
                benchmark_results.append({
                    "operation": operation,
                    "success": False,
                    "execution_time_ms": 0,
                    "disk_reads": 0,
                    "disk_writes": 0,
                    "total_io": 0,
                    "message": str(e)
                })
        
        # Calcular estadísticas agregadas
        successful_ops = [r for r in benchmark_results if r["success"]]
        avg_time = sum(r["execution_time_ms"] for r in successful_ops) / len(successful_ops) if successful_ops else 0
        total_io = sum(r["total_io"] for r in successful_ops)
        
        return {
            "success": True,
            "index_info": index_info.dict(),
            "benchmark_results": benchmark_results,
            "summary": {
                "total_operations": len(benchmark_results),
                "successful_operations": len(successful_ops),
                "average_execution_time_ms": round(avg_time, 2),
                "total_disk_io": total_io
            }
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error en benchmark de índice",
                "error": str(e)
            }
        )

@app.post("/api/transaction/execute")
async def execute_transaction(request: TransactionRequest):
    """
    Ejecuta una transacción con múltiples operaciones SQL
    
    Args:
        request: Información de la transacción
        
    Returns:
        Resultados de la transacción
    """
    try:
        global active_transactions, transaction_log, concurrency_stats
        
        # Registrar inicio de transacción
        start_time = datetime.now()
        active_transactions[request.transaction_id] = {
            "start_time": start_time,
            "operations": request.operations,
            "status": "running"
        }
        
        concurrency_stats["active_transactions"] += 1
        concurrency_stats["total_operations"] += len(request.operations)
        
        # Ejecutar operaciones
        ejecutor = get_ejecutor(request.database_path)
        transaction_results = []
        
        for i, operation in enumerate(request.operations):
            try:
                operation_start = datetime.now()
                resultados = ejecutor.ejecutar(operation)
                operation_end = datetime.now()
                
                # Registrar operación en el log
                log_entry = {
                    "transaction_id": request.transaction_id,
                    "operation_number": i + 1,
                    "operation": operation,
                    "timestamp": operation_start.isoformat(),
                    "execution_time_ms": (operation_end - operation_start).total_seconds() * 1000,
                    "success": True,
                    "results": []
                }
                
                if resultados:
                    for result in resultados:
                        log_entry["results"].append({
                            "operation": result.operacion,
                            "table": result.tabla,
                            "ok": result.ok,
                            "affected": result.afectados,
                            "reads": result.reads,
                            "writes": result.writes,
                            "total_io": result.total_io,
                            "message": result.mensaje
                        })
                
                transaction_log.append(log_entry)
                transaction_results.extend(resultados)
                
            except Exception as e:
                # Registrar error en operación
                error_entry = {
                    "transaction_id": request.transaction_id,
                    "operation_number": i + 1,
                    "operation": operation,
                    "timestamp": datetime.now().isoformat(),
                    "success": False,
                    "error": str(e)
                }
                transaction_log.append(error_entry)
                break
        
        # Finalizar transacción
        end_time = datetime.now()
        active_transactions[request.transaction_id]["status"] = "completed"
        active_transactions[request.transaction_id]["end_time"] = end_time
        
        concurrency_stats["active_transactions"] -= 1
        concurrency_stats["completed_transactions"] += 1
        
        # Detectar conflictos simples (mismo recurso en tiempo similar)
        conflicts = detect_conflicts(request.transaction_id)
        concurrency_stats["conflicts_detected"] += len(conflicts)
        
        return {
            "success": True,
            "transaction_id": request.transaction_id,
            "execution_time_ms": (end_time - start_time).total_seconds() * 1000,
            "operations_executed": len(transaction_results),
            "conflicts_detected": conflicts,
            "results": [{"ok": r.ok, "message": r.mensaje, "io": r.total_io} for r in transaction_results]
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error ejecutando transacción",
                "error": str(e)
            }
        )

def detect_conflicts(transaction_id: str) -> List[str]:
    """
    Detecta conflictos simples entre transacciones
    
    Args:
        transaction_id: ID de la transacción a verificar
        
    Returns:
        Lista de conflictos detectados
    """
    conflicts = []
    current_ops = [entry for entry in transaction_log if entry["transaction_id"] == transaction_id]
    
    # Buscar operaciones sobre las mismas tablas en tiempo similar
    for entry in transaction_log:
        if entry["transaction_id"] != transaction_id and entry.get("success", True):
            for current_op in current_ops:
                if current_op.get("success", True):
                    # Simple detección: misma tabla y operaciones de escritura
                    if ("INSERT" in entry["operation"] or "DELETE" in entry["operation"] or "UPDATE" in entry["operation"]) and \
                       ("INSERT" in current_op["operation"] or "DELETE" in current_op["operation"] or "UPDATE" in current_op["operation"]):
                        conflicts.append(f"Conflicto con transacción {entry['transaction_id']} en operación {entry['operation']}")
    
    return conflicts

@app.get("/api/concurrency/stats")
async def get_concurrency_stats():
    """
    Obtiene estadísticas del simulador de concurrencia
    
    Returns:
        Estadísticas de concurrencia
    """
    try:
        return {
            "success": True,
            "stats": concurrency_stats,
            "active_transactions": list(active_transactions.keys()),
            "recent_operations": transaction_log[-10:] if transaction_log else []
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error obteniendo estadísticas de concurrencia",
                "error": str(e)
            }
        )

@app.get("/api/concurrency/log")
async def get_transaction_log(limit: int = 50):
    """
    Obtiene el log de transacciones
    
    Args:
        limit: Número máximo de entradas a retornar
        
    Returns:
        Log de transacciones
    """
    try:
        return {
            "success": True,
            "log": transaction_log[-limit:] if transaction_log else [],
            "total_entries": len(transaction_log)
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error obteniendo log de transacciones",
                "error": str(e)
            }
        )

@app.delete("/api/concurrency/reset")
async def reset_concurrency_stats():
    """
    Reinicia las estadísticas de concurrencia
    
    Returns:
        Confirmación de reinicio
    """
    try:
        global transaction_log, active_transactions, concurrency_stats
        
        transaction_log.clear()
        active_transactions.clear()
        concurrency_stats = {
            "active_transactions": 0,
            "completed_transactions": 0,
            "conflicts_detected": 0,
            "total_operations": 0
        }
        
        return {
            "success": True,
            "message": "Estadísticas de concurrencia reiniciadas"
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error reiniciando estadísticas de concurrencia",
                "error": str(e)
            }
        )

@app.delete("/api/cleanup")
async def cleanup_database(database_path: str = "."):
    """
    Limpia los archivos de base de datos (uso con precaución)
    
    Args:
        database_path: Ruta a la base de datos
        
    Returns:
        Resultado de la limpieza
    """
    try:
        # Cerrar el ejecutor primero
        global ejecutor_global
        if ejecutor_global:
            ejecutor_global.cerrar_todo()
            ejecutor_global = None
        
        # Eliminar archivos .bin
        deleted_files = []
        if os.path.exists(database_path):
            for file in os.listdir(database_path):
                if file.endswith('.bin'):
                    file_path = os.path.join(database_path, file)
                    os.remove(file_path)
                    deleted_files.append(file)
        
        return {
            "success": True,
            "message": f"Se eliminaron {len(deleted_files)} archivos de base de datos",
            "deleted_files": deleted_files
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Error al limpiar base de datos",
                "error": str(e)
            }
        )

class SpatialQueryRequest(BaseModel):
    table: str
    lon: float
    lat: float
    radius: Optional[float] = None
    k: Optional[int] = None
    database_path: Optional[str] = "."


@app.post("/api/spatial-search")
async def spatial_search(request: SpatialQueryRequest):
    """
    Ejecuta una búsqueda espacial (RADIUS o KNN) y devuelve los puntos
    con coordenadas para visualización en el plano.
    """
    try:
        ejecutor = get_ejecutor(request.database_path)

        if request.table not in ejecutor._catalogo:
            return JSONResponse(status_code=404, content={
                "success": False,
                "message": f"Tabla '{request.table}' no existe"
            })

        info = ejecutor._catalogo[request.table]
        if info["tecnica"] != "RTREE":
            return JSONResponse(status_code=400, content={
                "success": False,
                "message": "La tabla no usa índice RTREE"
            })

        indice = info["indice"]
        t0 = datetime.now()

        if request.radius is not None:
            result = indice.range_search(request.lon, request.lat, request.radius)
        elif request.k is not None:
            result = indice.knn(request.lon, request.lat, request.k)
        else:
            return JSONResponse(status_code=400, content={
                "success": False,
                "message": "Debes especificar radius o k"
            })

        elapsed = (datetime.now() - t0).total_seconds() * 1000

        # Convertir TIDs a puntos con coordenadas aproximadas desde los MBRs visitados
        tids = [{"page_id": t.page_id, "slot_id": t.slot_id} for t in result.tids]
        visited = [{"min_lon": m[0], "max_lon": m[1], "min_lat": m[2], "max_lat": m[3]}
                   for m in result.visited_mbrs]

        return {
            "success": True,
            "query_point": {"lon": request.lon, "lat": request.lat},
            "query_radius": request.radius,
            "results_count": len(tids),
            "tids": tids,
            "visited_mbrs": visited,
            "io_reads": result.io_reads,
            "io_writes": result.io_writes,
            "execution_time_ms": elapsed
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": "Error en búsqueda espacial",
            "error": str(e)
        })


@app.get("/api/rtree-points/{table}")
async def get_rtree_points(table: str, database_path: str = "."):
    """
    Devuelve todos los puntos almacenados en un índice R-Tree para visualización.
    Recorre todas las hojas del árbol y extrae las coordenadas de los MBRs.
    """
    try:
        ejecutor = get_ejecutor(database_path)

        if table not in ejecutor._catalogo:
            return JSONResponse(status_code=404, content={
                "success": False,
                "message": f"Tabla '{table}' no existe"
            })

        info = ejecutor._catalogo[table]
        if info["tecnica"] != "RTREE":
            return JSONResponse(status_code=400, content={
                "success": False,
                "message": "La tabla no usa índice RTREE"
            })

        indice = info["indice"]
        points = []

        # Recorrer el árbol para extraer puntos de las hojas
        def collect_points(pid: int):
            node = indice._read_node(pid)
            if node.is_leaf:
                for e in node.entries:
                    mbr = e["mbr"]
                    # Para puntos, min == max en ambas dimensiones
                    points.append({
                        "lon": (mbr.min_lon + mbr.max_lon) / 2,
                        "lat": (mbr.min_lat + mbr.max_lat) / 2,
                        "page_id": e.get("tid_page", 0),
                        "slot_id": e.get("tid_slot", 0)
                    })
            else:
                for e in node.entries:
                    collect_points(e["child_pid"])

        collect_points(indice._root_pid())

        return {
            "success": True,
            "table": table,
            "points": points,
            "total": len(points)
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": "Error obteniendo puntos del R-Tree",
            "error": str(e)
        })


@app.get("/api/benchmark")
async def get_benchmark_results():
    """
    Devuelve los resultados del último benchmark ejecutado (benchmark_results.json).
    Si no existe, devuelve un objeto vacío con instrucciones.
    """
    results_file = "benchmark_results.json"
    if not os.path.exists(results_file):
        return {
            "success": False,
            "message": "No hay resultados de benchmark. Ejecuta: python3 benchmark.py",
            "results": {},
            "sizes": [],
        }
    try:
        with open(results_file) as f:
            data = json.load(f)
        return {"success": True, **data}
    except Exception as e:
        return JSONResponse(status_code=500, content={
            "success": False,
            "message": "Error leyendo benchmark_results.json",
            "error": str(e),
        })


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejador global de excepciones"""
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "Error interno del servidor",
            "error": str(exc),
            "traceback": traceback.format_exc()
        }
    )

if __name__ == "__main__":
    import uvicorn
    
    print("Iniciando BD2 Parser API Backend...")
    print("Endpoints disponibles:")
    print("  GET  /           - Información de la API")
    print("  GET  /health     - Health check")
    print("  POST /api/execute - Ejecutar consultas SQL")
    print("  POST /api/parse   - Parsear consultas SQL")
    print("  GET  /api/tables  - Listar tablas")
    print("  POST /api/create-table - Crear tablas")
    print("  GET  /api/stats   - Estadísticas de la BD")
    print("  DELETE /api/cleanup - Limpiar archivos")
    print()
    print("Servidor iniciando en http://localhost:8000")
    
    uvicorn.run(
        "endpoints:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )