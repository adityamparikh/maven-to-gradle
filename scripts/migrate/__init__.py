"""Maven to Gradle KTS + Version Catalogs migration package."""

from .migration_pipeline import migrate, main
from .pom_parser import parse_pom
from .pom_models import Dependency, Plugin, MavenProfile, MavenModule

__all__ = ["migrate", "main", "parse_pom", "Dependency", "Plugin", "MavenProfile", "MavenModule"]
