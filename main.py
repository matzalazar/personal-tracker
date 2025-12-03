#!/usr/bin/env python3
# main.py

"""
Punto de entrada principal para ejecutar el framework de scrapers.

Este script maneja:
- Parseo de argumentos de línea de comandos (CLI).
- Configuración del entorno (dev vs. prod) para rutas de config y logs.
- Configuración global del logging.
- Configuración de un log de éxito separado.
- Selección y ejecución de los scrapers solicitados.
- Reporte de un resumen final.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
import argparse
import logging
import sys
from datetime import datetime 
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Importaciones Locales
from src.scrapers import (
    CourseraProgressScraper,
    GitHubDailyActivityScraper,
    GoodreadsReadingScraper,
    UPSOStudyPlanScraper,
    LinkedInProfileScraper,
)

# Variables Globales de Entorno
CONFIG_DIR = Path(__file__).parent / "config"
ENV_NAME = "dev"
LOG_FILE_PATH = Path(__file__).parent / "data/personal_sync.log"


def setup_logging(level: int = logging.INFO, log_file: str | Path = "data/personal_sync.log"):
    """
    Configura el logging global (principal) para la consola y un archivo.
    """
    log_file = Path(log_file)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    except PermissionError:
        print(f"[ERROR] No se pudo crear/escribir en {log_file}. "
              f"Verifica permisos o la ruta de log para el entorno '{ENV_NAME}'. "
              "Continuando con logs solo en consola.")

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True
    )

def setup_success_logger(log_dir: Path) -> logging.Logger:
    """
    Configura un logger simple separado SOLO para los éxitos.
    Guardará en 'data/scrapers_success.log' (o el log_dir de prod).
    """
    log_file = log_dir / "scrapers_success.log"
    
    # Usar un nombre único para el logger
    logger = logging.getLogger("ScraperSuccessLog")
    logger.setLevel(logging.INFO)
    
    # Evitar que los logs se propaguen al logger root (el de debug)
    logger.propagate = False
    
    # Formato: solo el mensaje, ya que lo formatearemos manualmente
    formatter = logging.Formatter('%(message)s')
    
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Modo 'a' para (append)
        fh = logging.FileHandler(log_file, encoding='utf-8', mode='a')
        fh.setFormatter(formatter)
        
        # Limpiar handlers viejos si se reconfigura
        if logger.hasHandlers():
            logger.handlers.clear()
            
        logger.addHandler(fh)
    except Exception as e:
        print(f"[ERROR] No se pudo crear el log de éxito {log_file}: {e}")
        logger.addHandler(logging.NullHandler()) # Evita errores
        
    return logger

# ---------------------------
# Runners de Scrapers
# ---------------------------

def run_coursera() -> Optional[Path]:
    """Ejecuta el scraper de Coursera."""
    scraper = CourseraProgressScraper(config_dir=CONFIG_DIR, env_name=ENV_NAME)
    json_path = scraper.run()
    print(f"[Coursera] Datos guardados en: {json_path}")
    return json_path

def run_goodreads() -> Optional[Path]:
    """Ejecuta el scraper de Goodreads."""
    scraper = GoodreadsReadingScraper(config_dir=CONFIG_DIR, env_name=ENV_NAME)
    json_path = scraper.run()
    print(f"[Goodreads] Datos guardados en: {json_path}")
    return json_path

def run_upso() -> Optional[Path]:
    """Ejecuta el scraper de UPSO."""
    scraper = UPSOStudyPlanScraper(config_dir=CONFIG_DIR, env_name=ENV_NAME)
    json_path = scraper.run()
    print(f"[UPSO] Datos guardados en: {json_path}")
    return json_path

def run_github_today() -> Optional[Path]:
    """Ejecuta el scraper de actividad diaria de GitHub."""
    scraper = GitHubDailyActivityScraper(config_dir=CONFIG_DIR, env_name=ENV_NAME)
    json_path = scraper.run()
    print(f"[GitHub] Datos guardados en: {json_path}")
    return json_path

def run_linkedin() -> Optional[Path]:
    """Ejecuta el scraper de perfil de LinkedIn."""
    scraper = LinkedInProfileScraper(config_dir=CONFIG_DIR, env_name=ENV_NAME)
    json_path = scraper.run()
    print(f"[LinkedIn] Datos guardados en: {json_path}")
    return json_path


# Mapeo de argumentos CLI a funciones runner
RUNNERS: Dict[str, Callable[[], Optional[Path]]] = {
    "coursera": run_coursera,
    "goodreads": run_goodreads,
    "upso": run_upso,
    "github_today": run_github_today,
    "linkedin": run_linkedin,
}

# ---------------------------
# Interfaz de Línea de Comandos (CLI)
# ---------------------------

def parse_args() -> argparse.Namespace:
    """Parsea los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Personal Sync: ejecuta scrapers de fuentes personales."
    )
    parser.add_argument(
        "--sources", "-s",
        default="coursera,goodreads,github_today",
        help=(
            "Fuentes a ejecutar separadas por coma. "
            f"Opciones: {', '.join(RUNNERS)}. "
            "Usá 'all' para todas. Ej: -s upso,linkedin"
        ),
    )
    parser.add_argument(
        "--env",
        default="dev",
        choices=["dev", "prod"],
        help="Entorno a usar (dev o prod). Afecta rutas de config y logs. Default: dev"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Muestra las fuentes (scrapers) disponibles y sale."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Habilita logging detallado (DEBUG level)."
    )
    return parser.parse_args()

def main() -> int:
    """
    Función principal:
    1. Parsea args.
    2. Configura globales (ENV_NAME, CONFIG_DIR, LOG_FILE_PATH).
    3. Configura logging (principal y de éxito).
    4. Ejecuta los scrapers solicitados.
    5. Imprime resumen y retorna código de salida.
    """
    global CONFIG_DIR, ENV_NAME, LOG_FILE_PATH
    
    args = parse_args()
    
    # 1. Configurar Entorno
    ENV_NAME = args.env
    log_level = logging.DEBUG if args.verbose else logging.INFO

    if ENV_NAME == "prod":
        CONFIG_DIR = Path("/etc/personal-track")
        LOG_FILE_PATH = Path("/var/log/personal-track/personal_sync.log")
    else:
        CONFIG_DIR = Path(__file__).parent / "config"
        LOG_FILE_PATH = Path(__file__).parent / "data/personal_sync.log"

    # 2. Configurar Logging
    setup_logging(level=log_level, log_file=LOG_FILE_PATH)
    success_logger = setup_success_logger(LOG_FILE_PATH.parent)
    
    logger = logging.getLogger("main")
    logger.info(f"Iniciando Personal Sync. Entorno: {ENV_NAME.upper()}")

    # 3. Manejar --list
    if args.list:
        print("Fuentes disponibles:")
        for k in RUNNERS:
            print(f" - {k}")
        return 0

    # 4. Seleccionar Scrapers
    if args.sources.strip().lower() == "all":
        requested = list(RUNNERS.keys())
    else:
        requested = [s.strip().lower() for s in args.sources.split(",") if s.strip()]

    unknown = [s for s in requested if s not in RUNNERS]
    if unknown:
        logger.error(f"Fuente(s) desconocida(s): {', '.join(unknown)}")
        print(f"[ERROR] Fuente(s) desconocida(s): {', '.join(unknown)}. "
              f"Opciones válidas: {', '.join(RUNNERS)}")
        return 2

    # 5. Ejecutar Scrapers
    exit_code = 0
    outputs: Dict[str, str] = {}

    logger.info(f"Ejecutando {len(requested)} scraper(s): {', '.join(requested)}")
    print(f"Ejecutando {len(requested)} scraper(s): {', '.join(requested)}")
    print(f"Entorno: {ENV_NAME.upper()}")
    print("=" * 50)

    for src in requested:
        try:
            logger.info(f"[RUN] Iniciando scraper: {src}")
            print(f"\n[RUN] {src}…")
            
            json_path = RUNNERS[src]()
            outputs[src] = f"JSON: {json_path}"
            logger.info(f"[RUN] {src} completado.")

            # Solo escribimos en el log de éxito si se generó un archivo
            if json_path: 
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_line = f"{timestamp} - {src} - ejecutada con éxito."
                success_logger.info(log_line)
                
        except Exception as e:
            exit_code = 1
            outputs[src] = f"ERROR: {e}"
            print(f"[{src.upper()}][ERROR] {e}")
            logger.exception(f"Error fatal durante la ejecución de {src}")

    # 6. Resumen Final
    print("\n" + "=" * 50)
    print("=== RESUMEN EJECUCIÓN ===")
    logger.info("=== RESUMEN EJECUCIÓN ===")
    for src in requested:
        status = "Ok." if "ERROR" not in outputs[src] else "Error."
        log_msg = f"{status} {src}: {outputs[src]}"
        print(log_msg)
        logger.info(log_msg)

    if exit_code == 0:
        logger.info("Todos los scrapers completados exitosamente.")
        print(f"\nTodos los scrapers completados exitosamente")
    else:
        logger.warning("Algunos scrapers fallaron (revisa los logs).")
        print(f"\nAlgunos scrapers fallaron (revisa los logs)")

    return exit_code

# Punto de Entrada
if __name__ == "__main__":
    sys.exit(main())
