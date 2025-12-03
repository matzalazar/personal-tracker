# Personal Tracker

Sistema de captura y sincronización periódica de información personal proveniente de múltiples plataformas, diseñado para ejecutarse tanto en entornos de desarrollo como en producción con un despliegue limpio basado en FHS y automatización mediante systemd. Su propósito es centralizar información dispersa en servicios educativos, laborales y sociales, almacenándola en formatos normalizados (JSON) que permiten análisis posteriores, auditorías personales o integración con otros proyectos.

## Objetivos del Proyecto

Personal Tracker aborda la necesidad de reunir información distribuida en múltiples plataformas, resolviendo los siguientes problemas:

* **Fragmentación de datos personales** en Coursera, Goodreads, GitHub, LinkedIn, UPSO, etc.
* **Acceso manual** repetitivo para ver progresos, certificaciones, planes académicos o registros de actividad.
* **Dificultad para automatizar** este monitoreo en entornos Linux, especialmente cuando se requiere Selenium.
* **Falta de estandarización** de los datos exportados por cada plataforma.
* **Imposibilidad de integrarlo a sistemas más grandes** sin un backend unificado.

Con este proyecto:

* La captura se vuelve automática y programada.
* Los formatos de salida se homogeneizan.
* El sistema puede ejecutarse de forma aislada mediante un usuario dedicado.
* El desarrollo y la producción están claramente separados.

## Características Técnicas

* **Coursera** → Extrae progreso de cursos (Selenium).  
* **Goodreads** → Obtiene lecturas actuales (BeautifulSoup).  
* **UPSO** → Scrap de materias, estados y calificaciones (Selenium).  
* **GitHub** → Obtiene actividad diaria mediante API REST.  
* **LinkedIn** → Extrae About, Experiencia, Educación y Certificaciones (Selenium + BeautifulSoup).  

### Características comunes

* Salida estandarizada en JSON con timestamp en nombres de archivo.
* Configuración modular por entorno (`dev` y `prod`).
* Uso de un `BaseScraper` con manejo centralizado de:
  - sesiones,
  - paths,
  - carga de secretos,
  - logs,
  - escritura de outputs,
  - manejo de errores.
* Despliegue automatizado vía `scripts/deploy.sh`.
* Ejecución automática controlada por `systemd.timer`.

## Arquitectura del Proyecto

### Estructura general

```
personal-tracker/
 ├── main.py
 ├── src/
 │   ├── base_scraper.py
 │   ├── config_loader.py
 │   └── scrapers/
 │        ├── coursera_progress.py
 │        ├── github_daily_activity.py
 │        ├── goodreads_reading.py
 │        ├── linkedin_profile.py
 │        └── upso_study_plan.py
 ├── config/
 │   ├── .env.dev
 │   ├── .env.prod
 │   ├── .env.example
 │   ├── settings.yaml
 ├── data/
 │   ├── coursera/
 │   ├── github_daily/
 │   ├── goodreads/
 │   ├── linkedin/
 │   └── upso/
 ├── scripts/
 │   ├── deploy.sh
 │   └── systemd/
 │        ├── personal-track.service
 │        └── personal-track.timer
 └── README.md
```

## Flujo de Ejecución

```
Selenium / Requests
        ↓
  Scraper específico
        ↓
 Normalización del output
        ↓
 Escritura JSON con timestamp
        ↓
   Logging unificado
        ↓
Ejecución automática por systemd.timer
```

## Ejemplos Reales de Salida

### UPSO
```
[
  {
    "codigo": "443",
    "nombre": "GESTIÓN EMPRESARIAL EMPRENDEDORA",
    "estado": "En curso"
  }
]
```

### Goodreads
```
{
  "title": "El valor de la atención",
  "author": "Hari, Johann",
  "percent": 26
}
```

### GitHub Daily
```
{
  "repo": "matzalazar/personal-tracker",
  "message": "Working",
  "date": "2025-11-18T15:10:28Z"
}
```

### Coursera
```
{
  "title": "Combinatorics and Probability",
  "percent": 10
}
```

### LinkedIn
```
{
  "about": "Desarrollador de software orientado al backend...",
  "experience": [
    {
      "title": "Desarrollador de software",
      "meta": "mar. 2020 - actualidad"
    }
  ]
}
```

## Configuración del Proyecto

El sistema utiliza cuatro archivos principales en `config/`:

1. **`.env`** → secretos (emails, contraseñas, tokens).  
2. **`.env.dev`** → rutas relativas para desarrollo local.  
3. **`.env.prod`** → rutas absolutas FHS para producción.  
4. **`settings.yaml`** → timeouts, URLs base, flags de Selenium, defaults.

### Variables mínimas necesarias

Ejemplos:

```
COURSERA_EMAIL=
COURSERA_PASSWORD=
UPSO_USUARIO=
UPSO_CLAVE=
GOODREADS_PROFILE_URL=
LINKEDIN_EMAIL=
LINKEDIN_PASSWORD=
LINKEDIN_PROFILE_URL=
GITHUB_TOKEN=
```

### Configuración inicial

```
cp config/.env.example config/.env
nano config/.env
```

## Dependencias del Sistema

### Debian/Ubuntu

```
sudo apt install chromium-browser chromium-driver python3-venv \
libnss3 libgconf-2-4 libxi6 libxcursor1 libxcomposite1 libasound2
```

### Arch Linux

```
sudo pacman -S chromium chromedriver python-virtualenv
```

## Modo Desarrollo

### Crear entorno virtual

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Ejecutar scrapers

```
python main.py --list
python main.py --env dev -s goodreads,github_daily
python main.py -s all
```

### Nota sobre Coursera

Para generar cookies válidas:

```
headless=False
```

Luego copiarlas a `config/` y volver a activar:

```
headless=True
```

## Modo Producción (FHS + systemd)

El sistema se despliega automáticamente mediante:

```
sudo bash scripts/deploy.sh
```

### El deploy script hace lo siguiente:

1. Crea el usuario del sistema:  
   ```
   useradd -r -s /bin/bash track
   ```
2. Crea estructura FHS:  
   ```
   /opt/personal-track
   /etc/personal-track
   /var/lib/personal-track
   /var/log/personal-track
   ```
3. Copia el código fuente a `/opt/personal-track`.  
4. Crea entorno virtual en producción.  
5. Copia archivos `.env.dev`, `.env.prod`, `settings.yaml`, cookies a `/etc/personal-track/`.  
6. Instala servicios systemd:

```
personal-track.service
personal-track.timer
```

7. Habilita e inicia el timer:

```
sudo systemctl enable --now personal-track.timer
```

## Verificación de la Ejecución en Producción

### Estado del timer

```
systemctl list-timers | grep personal-track
```

### Logs en tiempo real

```
journalctl -u personal-track.service -f
```

### Últimos 50 registros

```
journalctl -u personal-track.service -n 50 --no-pager
```

### Exploración de datos generados

```
ls -l /var/lib/personal-track/
```

### Ejecución manual (misma línea que usa systemd)

```
sudo -u track /opt/personal-track/.venv/bin/python /opt/personal-track/main.py --env prod -s all
```

## Extender el Sistema

Para agregar un scraper (por ejemplo `twitter_scraper.py`):

1. Crear archivo en `src/scrapers/`.  
2. Definir clase:

```
class TwitterScraper(BaseScraper):
    def run(self):
        ...
```

3. Registrar scraper en `main.py`.

4. Ejecutar:

```
python main.py --env dev -s twitter
```

## Casos de Uso Reales y Potenciales

Personal Tracker no es simplemente un recopilador de datos: funciona como una **capa unificadora de información personal**, diseñada para actuar como backend multipropósito para automatización, auditoría personal, dashboards y workflows complejos.

A continuación se listan casos reales ya implementados, junto con casos potenciales altamente viables que muestran el alcance del sistema:

### Casos de uso actualmente implementados

#### 1. Actualización automática de sitio web personal (Jekyll + GitHub Actions)
* El sitio web del usuario se actualiza diariamente mostrando:
  - qué está leyendo,
  - qué está estudiando,
  - progreso en cursos,
  - actividad en repositorios personales.
* Se genera información en JSON normalizado y el pipeline de GitHub Actions la consume sin intervención manual.

#### 2. Integración con automatización de perfiles laborales (Bumeran, ZonaJobs)
* Basado en los datos extraídos de LinkedIn, GitHub y certificaciones:
  - actualiza descripciones,
  - sincroniza certificaciones,
  - mantiene habilidades al día,
  - registra actividad reciente del usuario.
* Permite mantener perfiles laborales "vivos" sin esfuerzo humano.

### Casos de uso adicionales posibles

#### Dashboards personales en tiempo real (Grafana / Superset / Kibana)

Los JSON exportados pueden usarse para construir dashboards como:

* Evolución de commits por día.
* Progreso semanal de cursos.
* Historial de actividad académica (UPSO).
* Promedio de lecturas mensuales y hábitos de lectura.
* Trazabilidad completa del ciclo de aprendizaje.

Gracias al formato JSON uniforme, se puede indexar fácilmente en:
- Elasticsearch,
- DuckDB,
- SQLite local,
- InfluxDB,
- o exportar a CSV para análisis en pandas.

#### Generador de informes automáticos semanales o mensuales
El sistema puede integrarse a un job adicional que genere reportes:

* PDF mensual de progreso educativo.
* Informe semanal de actividad laboral/técnica.
* Reporte de crecimiento profesional (certificaciones, cursos iniciados, completados).
* Consolidación de actividades para CV dinámico.

#### Integración con asistentes IA propios
La data unificada permite:

* entrenar modelos livianos para recomendaciones personalizadas de estudio,
* chatbots personalizados que recuerden el historial de aprendizaje,
* análisis automatizado sobre qué temas conviene estudiar según tendencias del usuario.

Ejemplo:  
* “Según tus últimos 14 días, tus horas de estudio se redujeron un 25%. Recomiendo retomar X curso…”*

#### Sincronización con servicios de productividad (Notion, Obsidian, Todoist)
Mediante exportación JSON, se puede:

* generar páginas automáticas en Notion,
* actualizar notas en Obsidian (lecturas, cursos, progreso),
* crear tareas recurrentes basadas en hábitos detectados,
* llevar un "journal" automático diario.

#### Auditoría personal para portafolios profesionales
Almacenar historial de commits, certificaciones y cursos habilita:

* generación automática de portafolios profesionales actualizados,
* documentación continua del progreso técnico,
* exportación rápida para entrevistas laborales.

#### Orquestación ampliada con Airflow, Prefect o Cron
El tracker puede integrarse en pipelines complejos:

* ejecutar scrapers que dependen de otros (por ejemplo: LinkedIn → GitHub → informes),
* programar ejecución más frecuente de algunos scrapers,
* mantener logs estructurados para monitoreo.

Gracias al diseño modular, cada scraper es una tarea independiente.

#### Uso dentro de servidores hogareños o Raspberry Pi como "Digital Life Hub"
Una Raspberry Pi puede actuar como:

* centro de sincronización de información digital,
* concentrador de datos personales,
* recolector cronológico de eventos,
* mirror diario de tu historial educativo/laboral.

#### Exportación para CV automatizado o perfil profesional dinámico
Con la información de LinkedIn + GitHub + Coursera + certificaciones, se puede:

* generar CVs PDF automáticamente,
* mantener un CV HTML actualizado en tiempo real,
* tener una sección "Actividad reciente" autogenerada.

#### Machine Learning personal
La data histórica permite:

* detectar hábitos,
* analizar correlaciones entre estudio y productividad,
* predecir qué días se estudia más,
* construir un "modelo de vida digital".

Es información que normalmente se dispersa, pero aquí queda archivada cronológicamente.

#### Integración con bots de mensajería (Telegram, WhatsApp)
Un microservicio puede leer los JSON y enviarte:

* resumen diario,
* progreso de cursos,
* recordatorios automáticos,
* novedades laborales,
* alertas cuando aparece una certificación nueva.

## Conclusión de la sección

Los casos de uso reales y potenciales muestran que Personal Tracker no es un conjunto de scrapers, sino una **plataforma unificadora de información personal**, ampliable, modular y lista para integrarse con cualquier stack moderno de automatización, visualización o análisis.

## Seguridad y Buenas Prácticas

* Usuario dedicado `track` sin permisos de login.  
* Umask recomendado:

```
umask 027
```

* Permisos sugeridos para configuración:

```
sudo chmod 750 /etc/personal-track
sudo chown root:track /etc/personal-track/*
```

* Cromium aislado con perfiles separados por scraper.

## Estado del Proyecto

Personal Tracker se encuentra totalmente funcional con scrapers para Coursera, Goodreads, GitHub, UPSO y LinkedIn, todos con salidas reales verificadas.

## Disclaimer

Este proyecto es para uso personal y educativo. El autor no se hace responsable del uso indebido de la herramienta en contra de los Términos de Servicio de las plataformas.
