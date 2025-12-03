# src/scrapers/__init__.py
from .coursera_progress import CourseraProgressScraper
from .github_daily_activity import GitHubDailyActivityScraper
from .goodreads_reading import GoodreadsReadingScraper
from .upso_study_plan import UPSOStudyPlanScraper
from .linkedin_profile import LinkedInProfileScraper # <--- AÑADIDO

__all__ = [
    'CourseraProgressScraper',
    'GitHubDailyActivityScraper', 
    'GoodreadsReadingScraper',
    'UPSOStudyPlanScraper',
    'LinkedInProfileScraper' # <--- AÑADIDO
]