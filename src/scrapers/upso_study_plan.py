#!/usr/bin/env python3
# src/scrapers/upso_study_plan.py

"""
Scraper para obtener el plan de estudios (Historia Académica)
desde el sistema SIU Guaraní 3 de UPSO.

Utiliza Selenium para:
1. Iniciar sesión en el portal.
2. Navegar a la página del plan de estudios.
3. Parsear la tabla de materias, incluyendo estado, año, correlativas, etc.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse, urlunparse
import os
import platform

# Importaciones de Terceros (Selenium)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# NOTA: webdriver_manager se importa condicionalmente dentro de _make_driver
# como un fallback.

# Importaciones Locales
from src.base_scraper import BaseScraper


@dataclass
class PlanItem:
    """Representa una materia en el plan de estudios."""
    codigo: Optional[str]
    nombre: str
    estado: str
    tipo: Optional[str]
    anio: Optional[str]
    periodo: Optional[str]
    creditos: Optional[str]
    correlativas: Optional[str]


class UPSOStudyPlanScraper(BaseScraper):
    """Scraper para plan de estudios de UPSO Guaraní."""
    
    def __init__(self, config_dir: Path, env_name: str = "dev"):
        """
        Inicializa el scraper de UPSO Guaraní.

        Args:
            config_dir: Ruta al directorio de configuración.
            env_name: Entorno de ejecución (ej. "dev", "prod").
        
        Raises:
            ValueError: Si faltan credenciales de UPSO.
        """
        super().__init__(config_dir, "upso", env_name=env_name)

        self.env_name = env_name

        # Configuración Específica
        self.usuario = self.config.get("upso.usuario")
        self.clave = self.config.get("upso.clave")
        self.plan_url = self.config.get("upso.plan_url", "https://guarani3w.upso.edu.ar/guarani3w/plan")
        self.puzzle_max_wait = self.config.get_int("upso.puzzle_max_wait", 120)
        
        if not self.usuario or not self.clave:
            raise ValueError("Faltan UPSO_USUARIO o UPSO_CLAVE")
            
        self.driver: Optional[webdriver.Chrome] = None

    # Métodos de Inicialización del Driver

    def _is_arm_architecture(self) -> bool:
        """Determina si estamos ejecutando en una arquitectura ARM (como Raspberry Pi)."""
        arch = platform.machine().lower()
        return any(a in arch for a in ["arm", "aarch64", "armv"])

    def _make_driver(self):
        """Inicializa el driver de Selenium con lógica multi-arquitectura."""
        
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        
        # Argumentos de Seguridad/Headless Agregados
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1280,1600")

        # Bloque de perfil de usuario para 'prod'
        if self.env_name == "prod":
            # Forzar a Chromium a usar directorios escribibles por el usuario 'track'.
            data_dir = "/var/lib/personal-track/chromium_data_upso"
            options.add_argument(f"--user-data-dir={data_dir}/user-data")
            options.add_argument(f"--disk-cache-dir={data_dir}/cache")
            options.add_argument(f"--crash-dumps-dir={data_dir}/crash-dumps")
        
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-features=UtilityProcessSandbox")
        options.add_argument("--disable-features=TranslateService") 
        options.add_argument("--disable-dbus")
        
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Flexibilizar búsqueda de binario
        chromium_binaries = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/lib/chromium/chromium",
        ]
        
        found_binary = None
        for path in chromium_binaries:
            if os.path.exists(path):
                found_binary = path
                break
        
        if found_binary:
             options.binary_location = found_binary
             self.logger.info(f"Usando binario de Chromium: {found_binary}")
        else:
            # Si no se encuentra, dejar que Selenium Manager intente (para PC local)
            pass
        
        is_arm = self._is_arm_architecture()
        
        # 1. Intento RPi/ARM: Usar driver pre-instalado
        if is_arm:
            arm_driver_paths = [
                "/usr/bin/chromedriver",
                "/usr/lib/chromium-browser/chromedriver",
                "/usr/lib/chromium/chromedriver",
            ]
            
            for path in arm_driver_paths:
                if os.path.exists(path):
                    self.logger.info(f"Usando chromedriver del sistema ARM: {path}")
                    try:
                        service = Service(executable_path=path) 
                        self.driver = webdriver.Chrome(service=service, options=options)
                        self.driver.set_page_load_timeout(self.timeout)
                        return
                    except Exception as e:
                        self.logger.warning(f"Driver ARM encontrado pero falló al iniciar: {e}")
                        continue
            
            raise RuntimeError("Driver ARM no encontrado/funcional. La descarga automática está deshabilitada en esta arquitectura (Exec format error).")

        # 2. Intento PC (Selenium Manager)
        try:
            self.logger.info("Intentando iniciar driver con Selenium Manager (PC x86-64).")
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(self.timeout)
            return
        except Exception as e1:
            self.logger.warning(f"Selenium Manager falló: {e1}")

        # 3. Intento PC (WebDriver Manager - Fallback)
        try:
            self.logger.info("Intentando descarga con WebDriver Manager (solo para PC).")
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(self.timeout)
            return
        except Exception as e3:
            raise RuntimeError(f"No se pudo inicializar Chrome (PC Error): {e3}")

    # Métodos de Navegación y Login

    def _normalize_url(self, url: str) -> str:
        """
        Normaliza URL agregando puerto 443 si es https y no lo tiene.
        (Soluciona un bug ocasional de Selenium/Geckodriver).
        """
        parsed = urlparse(url)
        if parsed.scheme == "https" and ":" not in parsed.netloc:
            netloc = f"{parsed.hostname}:443"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        return url

    def _login(self):
        """Realiza el login en UPSO Guaraní."""
        self.logger.info("Iniciando login en Guaraní UPSO...")
        self.driver.get("https://guarani3w.upso.edu.ar/guarani3w/acceso/login")
        
        # Esperar y completar campos de login
        try:
            username_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))
            )
            password_field = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            
            username_field.clear()
            username_field.send_keys(self.usuario)
            password_field.clear()
            password_field.send_keys(self.clave)
            password_field.send_keys(Keys.RETURN)
            
            # Esperar login completo (verificando URL o un elemento post-login)
            WebDriverWait(self.driver, 15).until(
                EC.url_contains("inicio_alumno")
            )
            self.logger.info("Login exitoso.")
            
        except Exception as e:
            raise RuntimeError(f"Fallo durante el login: {e}")

    def _goto_plan(self):
        """
        Navega a la página del plan de estudios.
        Intenta con varias URL normalizadas por si acaso.
        """
        self.logger.info("Navegando al plan de estudios...")
        urls_to_try = [
            self.plan_url,
            self._normalize_url(self.plan_url),
            "https://guarani3w.upso.edu.ar/guarani3w/plan",
            self._normalize_url("https://guarani3w.upso.edu.ar/guarani3w/plan")
        ]
        
        for url in urls_to_try:
            try:
                self.driver.get(url)
                time.sleep(2) # Dar tiempo a que JS redirija si es necesario
                if "plan" in self.driver.current_url.lower():
                    self.logger.info("Página del plan cargada.")
                    return
            except Exception:
                continue
                
        raise RuntimeError("No se pudo acceder a la URL del plan de estudios.")

    # Métodos de Parsing

    def _parse_materia_info(self, raw_text: str) -> Tuple[str, Optional[str]]:
        """Parsea 'Nombre de Materia (Codigo)' -> (Nombre, Codigo)."""
        text = (raw_text or "").strip()
        # Busca un código numérico entre paréntesis al final de la cadena
        match = re.search(r"^(.*?)[\s\u00A0]*\((\d+)\)\s*$", text)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return text, None # Devuelve solo nombre si no hay código

    def _find_column_index(self, headers: List[str], keywords: List[str]) -> Optional[int]:
        """Encuentra el índice de columna (case-insensitive) basado en keywords."""
        for i, header in enumerate(headers):
            header_lower = header.lower()
            for keyword in keywords:
                if keyword in header_lower:
                    return i
        return None

    # Método Principal de Ejecución

    def fetch_data(self) -> List[PlanItem]:
        """Obtiene el plan de estudios completo."""
        
        self._make_driver() # Inicializar el driver al inicio de la ejecución
        if not self.driver:
            raise RuntimeError("El driver de Selenium no se inicializó correctamente.")
            
        try:
            self._login()
            self._goto_plan()
            
            # Esperar a que la tabla de materias esté presente
            table = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table.table"))
            )
            
            # Mapeo de Columnas
            header_cells = table.find_elements(By.CSS_SELECTOR, "thead th")
            headers = [cell.text.strip() for cell in header_cells]
            
            # Mapear columnas dinámicamente por palabras clave
            col_materia = self._find_column_index(headers, ["materia", "code"])
            col_nombre = self._find_column_index(headers, ["nombre", "name"])
            col_estado = self._find_column_index(headers, ["estado", "status"])
            col_tipo = self._find_column_index(headers, ["tipo", "type"])
            col_anio = self._find_column_index(headers, ["año", "anio", "year"])
            col_periodo = self._find_column_index(headers, ["periodo", "cuatrimestre"])
            col_creditos = self._find_column_index(headers, ["credito", "credit"])
            col_correl = self._find_column_index(headers, ["correl", "prerequisite"])
            
            self.logger.info(f"Mapeo de columnas: Nombre(idx={col_nombre}), Estado(idx={col_estado})")

            # Procesamiento de Filas
            rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
            plan_items = []
            
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if not cells:
                    continue # Ignorar filas vacías
                    
                def get_cell_text(idx: Optional[int]) -> str:
                    """Helper para obtener texto de celda por índice de forma segura."""
                    return cells[idx].text.strip() if idx is not None and idx < len(cells) else ""
                
                # Extraer información de celdas
                materia_text = get_cell_text(col_materia) # Suele ser el código
                nombre_text = get_cell_text(col_nombre)  # Suele ser "Nombre (Codigo)"
                
                nombre, codigo = self._parse_materia_info(nombre_text)
                
                # Usar el texto de la columna 'materia' si no se encontró código
                if not codigo and materia_text.isdigit():
                    codigo = materia_text
                    
                plan_items.append(PlanItem(
                    codigo=codigo,
                    nombre=nombre or "(Sin nombre)",
                    estado=get_cell_text(col_estado),
                    tipo=get_cell_text(col_tipo),
                    anio=get_cell_text(col_anio),
                    periodo=get_cell_text(col_periodo),
                    creditos=get_cell_text(col_creditos),
                    correlativas=get_cell_text(col_correl)
                ))
            
            self.logger.info(f"Procesadas {len(plan_items)} materias del plan.")
            return plan_items
            
        except Exception as e:
            self.logger.error(f"Falló el scraping de UPSO: {e}", exc_info=True)
            # Guardar dump en caso de error inesperado
            try:
                debug_path = self.outdir / f"debug_error_upso_{int(time.time())}.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                self.logger.info(f"Se guardó dump del error en {debug_path}")
            except Exception as de:
                self.logger.error(f"No se pudo guardar el dump del error: {de}")
            return [] # Devolver lista vacía en caso de error
            
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None