"""Maven to Gradle KTS + Version Catalogs migration package."""

from .cli import migrate, main
from .maven import parse_pom
from .models import Dependency, Plugin, MavenProfile, MavenModule

__all__ = ["migrate", "main", "parse_pom", "Dependency", "Plugin", "MavenProfile", "MavenModule"]
