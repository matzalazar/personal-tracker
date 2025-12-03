#!/usr/bin/env python3
# src/scrapers/github_daily_activity.py

"""
Scraper para obtener la actividad diaria de commits en GitHub.

Este script utiliza la API de GitHub para:
1. Encontrar todos los repositorios del usuario.
2. Para cada repositorio, buscar commits realizados en el día actual
   (según la zona horaria local).
3. Filtrar commits por el 'author_login' y/o una lista de 'author_emails'.
4. Manejar la paginación y los límites de tasa (rate limiting) de la API.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo
import time

# Importaciones de Terceros
import requests

# Importaciones Locales
from src.base_scraper import BaseScraper


@dataclass
class CommitItem:
    """Representa un único commit de GitHub con metadatos relevantes."""
    repo: str
    sha: str
    html_url: str
    message: str
    date: str
    author_login: Optional[str]
    author_email: Optional[str]


class GitHubDailyActivityScraper(BaseScraper):
    """Scraper para actividad diaria en GitHub (commits del día)."""
    
    def __init__(self, config_dir: Path, env_name: str = "dev"):
        """
        Inicializa el scraper de actividad de GitHub.

        Args:
            config_dir: Ruta al directorio de configuración.
            env_name: Entorno de ejecución (ej. "dev", "prod").
        """
        super().__init__(config_dir, "github_daily", env_name=env_name)
        
        # Configuración de la API
        self.token = self.config.get("github.token")
        self.visibility = self.config.get("github.visibility", "all")
        self.per_page = self.config.get_int("github.per_page", 100)
        
        # Configuración de Autor
        self.author_login = self.config.get("github.author_login", "").strip()
        emails_str = self.config.get("github.author_emails", "")
        self.author_emails = set(email.strip() for email in emails_str.split(",") if email.strip())
        
        if not self.token:
            raise ValueError("Falta GITHUB_TOKEN en la configuración")
            
        # Configuración de Tiempo
        tz_name = self.config.get("general.timezone", "America/Argentina/Buenos_Aires")
        self.tz = ZoneInfo(tz_name)
        
        # Configurar Sesión HTTP
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "personal-sync/1.0",
            "Authorization": f"Bearer {self.token}",
        })
        
        # Resolver Autor
        # Resuelve el login del autor usando el token si no se proveyó
        if not self.author_login:
            self.author_login = self._resolve_author_login()

    def _resolve_author_login(self) -> str:
        """Resuelve el login del autor (username) usando el token de API."""
        self.logger.info("Resolviendo 'author_login' desde la API de GitHub (/user)...")
        response = self.session.get("https://api.github.com/user", timeout=self.timeout)
        response.raise_for_status()
        login = response.json().get("login")
        self.logger.info(f"'author_login' resuelto como: {login}")
        return login

    def _today_window(self) -> tuple[str, str]:
        """
        Calcula la ventana de tiempo (ISO 8601 UTC) para 'hoy'.
        
        Utiliza la zona horaria local para definir 'hoy' (00:00:00 a 23:59:59)
        y luego convierte esos límites a UTC para la consulta de la API.
        """
        now_local = datetime.now(self.tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = (start_local + timedelta(days=1)) - timedelta(microseconds=1)
        
        # Convertir a UTC y formatear para la API (Formato Zulu)
        start_utc = start_local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
        end_utc = end_local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
        
        return start_utc, end_utc

    def _paginate(self, url: str, params: Optional[Dict[str, Any]] = None) -> Iterator[dict]:
        """
        Iterador genérico para manejar la paginación de la API de GitHub.
        
        Maneja automáticamente el seguimiento de 'Link' headers y la
        espera por rate limiting.
        """
        while url:
            response = self.session.get(url, params=params, timeout=self.timeout)
            
            # Manejar Rate Limiting
            if response.status_code == 403 and "rate limit" in response.text.lower():
                reset_time_unix = response.headers.get("X-RateLimit-Reset")
                wait_seconds = 60
                if reset_time_unix:
                    wait_seconds = max(5, int(reset_time_unix) - int(time.time()))
                
                self.logger.warning(f"Rate limit detectado. Esperando {wait_seconds}s...")
                time.sleep(wait_seconds)
                response = self.session.get(url, params=params, timeout=self.timeout) # Reintentar

            response.raise_for_status()
            data = response.json()
            
            if isinstance(data, list):
                yield from data
            elif isinstance(data, dict):
                # Para endpoints que no devuelven listas (ej. /user)
                yield data
            else:
                # Si la respuesta no es ni lista ni dict, parar.
                break
                
            # Siguiente Página
            link_header = response.headers.get("Link", "")
            next_url = None
            for part in link_header.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.find("<") + 1:part.find(">")]
                    break
            
            # Si hay 'next_url', se usará en la próxima iteración.
            # 'params' solo se usa en la primera solicitud.
            url, params = next_url, None

    def _iter_repos(self) -> Iterator[dict]:
        """Itera sobre todos los repositorios del usuario."""
        params = {
            "per_page": self.per_page,
            "sort": "pushed", # Priorizar repos con actividad reciente
            "direction": "desc",
            "visibility": self.visibility
        }
        url = "https://api.github.com/user/repos"
        self.logger.info("Iniciando paginación de /user/repos...")
        yield from self._paginate(url, params)

    def _repo_commits_today(self, owner: str, repo: str, since_utc: str, until_utc: str) -> List[CommitItem]:
        """Obtiene commits del día para un repositorio específico."""
        base_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        seen_shas = set()
        commits = []

        # Estrategia 1: Buscar por 'author' (login)
        params_author = {
            "since": since_utc,
            "until": until_utc,
            "author": self.author_login,
            "per_page": self.per_page
        }
        
        for commit_data in self._paginate(base_url, params=params_author):
            sha = commit_data.get("sha")
            if not sha or sha in seen_shas:
                continue
                
            seen_shas.add(sha)
            commit_info = commit_data.get("commit", {})
            author_info = commit_info.get("author", {})
            
            commits.append(CommitItem(
                repo=f"{owner}/{repo}",
                sha=sha,
                html_url=commit_data.get("html_url", ""),
                message=(commit_info.get("message") or "").split("\n")[0].strip(), # Tomar solo la primera línea
                date=author_info.get("date", ""),
                author_login=(commit_data.get("author") or {}).get("login"),
                author_email=author_info.get("email")
            ))

        # Estrategia 2: Buscar por emails adicionales
        if self.author_emails:
            lower_emails = {email.lower() for email in self.author_emails}
            
            params_all = {
                "since": since_utc,
                "until": until_utc,
                "per_page": self.per_page
            }
            
            for commit_data in self._paginate(base_url, params=params_all):
                sha = commit_data.get("sha") or ""
                if sha in seen_shas:
                    # Ya lo encontramos en la Estrategia 1
                    continue
                    
                commit_info = commit_data.get("commit", {})
                author_info = commit_info.get("author", {})
                author_email = (author_info.get("email") or "").lower()
                
                # Verificar si el email del commit coincide con nuestra lista
                if author_email in lower_emails:
                    seen_shas.add(sha)
                    commits.append(CommitItem(
                        repo=f"{owner}/{repo}",
                        sha=sha,
                        html_url=commit_data.get("html_url", ""),
                        message=(commit_info.get("message") or "").split("\n")[0].strip(),
                        date=author_info.get("date", ""),
                        author_login=(commit_data.get("author") or {}).get("login"),
                        author_email=author_info.get("email")
                    ))

        return commits

    def fetch_data(self) -> List[CommitItem]:
        """
        Método principal para ejecutar el scraper.
        
        Obtiene todos los commits del día para todos los repos del usuario.
        
        Returns:
            Una lista de objetos CommitItem.
        """
        since_utc, until_utc = self._today_window()
        self.logger.info(f"Buscando commits entre {since_utc} y {until_utc}")
        self.logger.info(f"Filtrando por login: '{self.author_login}' y emails: {self.author_emails}")
        
        all_commits = []
        
        for repo in self._iter_repos():
            owner = (repo.get("owner") or {}).get("login")
            repo_name = repo.get("name")
            
            if not owner or not repo_name:
                continue
                
            try:
                commits = self._repo_commits_today(owner, repo_name, since_utc, until_utc)
                if commits:
                    all_commits.extend(commits)
                    self.logger.info(f"Encontrados {len(commits)} commits en {owner}/{repo_name}")
            except Exception as e:
                # No detener el scraper si falla un solo repositorio
                self.logger.warning(f"Error obteniendo commits de {owner}/{repo_name}: {e}")
                continue

        self.logger.info(f"Scraping finalizado. Total de commits del día: {len(all_commits)}")
        return all_commits