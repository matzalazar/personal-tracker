#!/usr/bin/env python3
# src/base_scraper.py

"""
Define la clase abstracta base (BaseScraper) para todos los scrapers.

Proporciona funcionalidad común para:
- Carga de configuración (a través de ConfigLoader).
- Configuración de logging.
- Creación de directorios de salida.
- Guardado de datos en JSON.
- Un método 'run' estandarizado.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
import csv
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

# Importaciones Locales
from .config_loader import Config

class BaseScraper(ABC):
    """Clase base abstracta para todos los scrapers."""
    
    def __init__(self, config_dir: Path, scraper_name: str, env_name: str = "dev"):
        """
        Inicializa el scraper base.

        Args:
            config_dir: Ruta al directorio de configuración (ej. 'config/').
            scraper_name: Nombre único del scraper (ej. 'coursera').
            env_name: Entorno de ejecución (ej. 'dev', 'prod').
        """
        self.config_dir = config_dir
        self.scraper_name = scraper_name
        self.env_name = env_name
        # Carga la configuración unificada
        self.config = Config(config_dir, env_name=env_name)
        self.logger = logging.getLogger(scraper_name)
        
        # Configura el directorio de salida (ej. 'data/coursera/')
        default_outdir = (
            f"/var/lib/personal-track/{self.scraper_name}"
            if self.env_name == "prod"
            else f"data/{self.scraper_name}"
        )
        self.outdir = Path(self.config.get(f"{scraper_name}.outdir", default_outdir))
        self.outdir.mkdir(parents=True, exist_ok=True)
        
        # Configuración común (usando helpers de Config para consistencia)
        self.headless = self.config.get_bool(f"{scraper_name}.headless", True)
        self.timeout = self.config.get_int(f"{scraper_name}.timeout", 25)

    @abstractmethod
    def fetch_data(self) -> List[Any]:
        """
        Método principal que debe implementar cada scraper.
        
        Debe devolver una lista de objetos (idealmente dataclasses)
        o una lista de diccionarios.
        """
        pass

    def save_data(self, data: List[Any]) -> Optional[Path]:
        """
        Guarda los datos en JSON de forma consistente.
        Retorna la ruta a json_path o None si no hay datos.
        """
        if not data:
            self.logger.warning("No hay datos para guardar.")
            return None

        # Generar nombres de archivo con timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base_name = f"{self.scraper_name}_{timestamp}"

        json_path = self.outdir / f"{base_name}.json"
        
        # Determinar si los datos son dataclasses
        is_dc = is_dataclass(data[0])

        # Guardar JSON
        # Convertir dataclasses a dicts; si ya son dicts, usarlos tal cual.
        json_data = [asdict(item) for item in data] if is_dc else data
        
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Error al guardar JSON en {json_path}: {e}")
            return None # Falló el guardado

        self.logger.info(f"Datos guardados: {json_path.name}")
        return json_path

    def run(self) -> Optional[Path]:
        """
        Ejecuta el ciclo completo: fetch -> save.
        """
        try:
            self.logger.info(f"Iniciando scraper: {self.scraper_name}")
            data = self.fetch_data()
            return self.save_data(data)
        except Exception as e:
            self.logger.error(f"Error fatal en {self.scraper_name}: {e}", exc_info=True)
            raise # Re-lanzar la excepción
