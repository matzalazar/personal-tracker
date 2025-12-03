#!/usr/bin/env python3
# src/config_loader.py

"""
Cargador de configuración unificado (Config).

Carga configuraciones desde múltiples fuentes con un orden de prioridad:
1. Variables de entorno (cargadas desde 'config/.env' - secretos).
2. Variables de entorno (cargadas desde 'config/.env.{env_name}' - config de entorno).
3. Archivo 'config/settings.yaml' (valores por defecto).

Prioridad de obtención: .env (secretos) > .env.{entorno} > settings.yaml > default.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Importaciones de Terceros
from dotenv import load_dotenv
import yaml


class Config:
    """Cargador unificado de configuración desde .env y YAML."""
    
    def __init__(self, config_dir: Path, env_name: str = "dev"):
        """
        Inicializa el cargador de configuración.

        Args:
            config_dir: Ruta al directorio de configuración (ej. 'config/').
            env_name: Entorno de ejecución (ej. 'dev', 'prod').
        """
        self.config_dir = config_dir
        # Carga las variables de entorno primero
        self._env_config = self._load_env(env_name)
        # Carga el YAML después
        self._yaml_config = self._load_yaml()

    def _load_env(self, env_name: str) -> Dict[str, str]:
        """
        Carga variables de entorno desde archivos .env.
        
        Prioridad (el último gana):
        1. Variables del sistema existentes.
        2. Variables en .env.{env_name} (ej. .env.dev)
        3. Variables en .env (secretos)
        
        Args:
            env_name: El nombre del entorno (dev, prod).

        Returns:
            Un diccionario de os.environ actualizado.
        """
        
        # 1. Cargar config de entorno (ej: config/.env.dev)
        env_path = self.config_dir / f".env.{env_name}"
        load_dotenv(dotenv_path=env_path, override=True) # Pisa vars del sistema

        # 2. Cargar secretos (ej: config/.env)
        secret_path = self.config_dir / ".env"
        load_dotenv(dotenv_path=secret_path, override=True) # Pisa vars del sistema Y de .env.dev
        
        return dict(os.environ)

    def _load_yaml(self) -> Dict[str, Any]:
        """Carga la configuración base desde settings.yaml."""
        yaml_path = self.config_dir / "settings.yaml"
        if not yaml_path.exists():
            return {}
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error al cargar settings.yaml: {e}")
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        Obtiene un valor de configuración con prioridad.

        Prioridad: .env (MAYUSCULAS_CON_GUION) > YAML (dot.notation) > default.
        
        Ej: `get('coursera.email')` buscará:
        1. OS.environ['COURSERA_EMAIL']
        2. YAML `{'coursera': {'email': 'valor'}}`
        3. `default`
        
        Args:
            key: La clave a buscar (ej. 'db.host' o 'GITHUB_TOKEN').
            default: Valor a devolver si no se encuentra.

        Returns:
            El valor de configuración.
        """
        # 1. Buscar en .env (convertir 'db.host' a 'DB_HOST')
        env_key = key.upper().replace('.', '_')
        if env_key in self._env_config:
            return self._env_config[env_key]

        # 2. Buscar en YAML (usando notación con puntos)
        yaml_keys = key.split('.')
        current = self._yaml_config
        try:
            for k in yaml_keys:
                if isinstance(current, dict) and k in current:
                    current = current[k]
                else:
                    # No encontrado en YAML, devolver default
                    return default
            # Encontrado en YAML
            return current
        except Exception:
            return default # Fallback por si la estructura no es un dict

    def get_list(self, key: str, default: Optional[List[str]] = None) -> List[str]:
        """
        Obtiene una lista de valores.
        Si el valor es un string, lo divide por comas.
        """
        # Usar (default or []) para evitar el mutable default
        value = self.get(key, default or [])
        if isinstance(value, str):
            return [item.strip() for item in value.split(',') if item.strip()]
        
        return value if isinstance(value, list) else (default or [])

    def get_int(self, key: str, default: int = 0) -> int:
        """Obtiene un valor y lo convierte a entero."""
        value = self.get(key, default)
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """
        Obtiene un valor y lo convierte a booleano.
        Maneja 'true', '1', 'yes', 'on' como True.
        """
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        
        # Fallback (ej. 0 -> False, 1 -> True)
        return bool(value)