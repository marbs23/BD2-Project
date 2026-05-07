#!/usr/bin/env python3
"""
Punto de entrada principal para el backend del BD2 Parser API
Ejecuta: python main.py
"""

import uvicorn
import os
from endpoints import app

def main():
    """Inicia el servidor FastAPI"""
    
    # Configuración del servidor
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    reload = os.getenv("RELOAD", "true").lower() == "true"
    
    print("🚀 Iniciando BD2 Parser API Backend...")
    print("=" * 50)
    print(f"📍 Host: {host}")
    print(f"🔌 Port: {port}")
    print(f"🔄 Reload: {reload}")
    print("=" * 50)
    print("\n📋 Endpoints disponibles:")
    print("  GET  /           - Información de la API")
    print("  GET  /health     - Health check")
    print("  POST /api/execute - Ejecutar consultas SQL")
    print("  POST /api/parse   - Parsear consultas SQL")
    print("  GET  /api/tables  - Listar tablas")
    print("  POST /api/create-table - Crear tablas")
    print("  GET  /api/stats   - Estadísticas de la BD")
    print("  DELETE /api/cleanup - Limpiar archivos")
    print("\n🔗 Frontend URL: http://localhost:5173")
    print("🔗 Backend URL: http://localhost:8000")
    print("=" * 50)
    
    try:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n👋 Servidor detenido por el usuario")
    except Exception as e:
        print(f"❌ Error iniciando el servidor: {e}")

if __name__ == "__main__":
    main()
