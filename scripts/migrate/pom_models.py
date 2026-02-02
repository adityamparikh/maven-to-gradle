"""Maven data model classes.

Pure data structures representing parsed Maven POM elements.
No behavior or imports from other migrate modules.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Dependency:
    """A Maven ``<dependency>`` element.

    Captures the full GAV (group, artifact, version) coordinates along with
    scope, classifier, type, optional flag, and exclusion list.

    Attributes:
        group_id: Maven groupId (e.g. ``org.springframework.boot``).
        artifact_id: Maven artifactId (e.g. ``spring-boot-starter-web``).
        version: Explicit version string, or ``None`` if managed by a BOM.
        scope: Maven scope — one of compile, provided, runtime, test, system, import.
        classifier: Optional classifier (e.g. ``sources``, ``tests``).
        dep_type: Optional packaging type (e.g. ``pom`` for BOM imports).
        optional: Whether the dependency is marked ``<optional>true</optional>``.
        exclusions: List of ``(groupId, artifactId)`` tuples to exclude.
    """
    group_id: str
    artifact_id: str
    version: Optional[str] = None
    scope: str = "compile"
    classifier: Optional[str] = None
    dep_type: Optional[str] = None
    optional: bool = False
    exclusions: list = field(default_factory=list)


@dataclass
class Plugin:
    """A Maven ``<plugin>`` element.

    Attributes:
        group_id: Plugin groupId (defaults to ``org.apache.maven.plugins``).
        artifact_id: Plugin artifactId (e.g. ``maven-compiler-plugin``).
        version: Explicit version, or ``None`` if inherited from pluginManagement.
        configuration: Flattened ``<configuration>`` block as a nested dict.
    """
    group_id: str
    artifact_id: str
    version: Optional[str] = None
    configuration: dict = field(default_factory=dict)


@dataclass
class MavenProfile:
    """A Maven ``<profile>`` element.

    Profiles are recorded but not automatically converted to Gradle logic.
    Instead, the generator emits comment hints in build.gradle.kts pointing
    to the profiles reference documentation.

    Attributes:
        profile_id: The ``<id>`` of the profile.
        activation: Parsed activation conditions (activeByDefault, jdk, property, os).
        dependencies: Dependencies declared within the profile.
        plugins: Plugins declared within the profile.
        properties: Properties declared within the profile.
    """
    profile_id: str
    activation: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)
    plugins: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass
class MavenModule:
    """Central parse result for a single ``pom.xml`` file.

    Represents both root and child modules. For multi-module projects, the
    root module has ``packaging="pom"`` and a non-empty ``modules`` list.

    Attributes:
        group_id: Maven groupId (inherited from parent if not declared).
        artifact_id: Maven artifactId.
        version: Version string (inherited from parent if not declared).
        packaging: Packaging type — jar, pom, or war.
        name: Human-readable ``<name>`` element.
        description: ``<description>`` element.
        parent_artifact_id: Parent POM artifactId, if any.
        parent_group_id: Parent POM groupId, if any.
        parent_version: Parent POM version, if any.
        properties: Merged ``<properties>`` dict.
        dependencies: Direct ``<dependencies>`` list.
        dep_management: ``<dependencyManagement>`` dependencies (BOMs and managed deps).
        plugins: ``<build><plugins>`` list.
        plugin_management: ``<build><pluginManagement><plugins>`` list.
        profiles: ``<profiles>`` list.
        modules: Child module directory names from ``<modules>``.
        repositories: List of ``(id, url)`` tuples from ``<repositories>``.
        source_dir: Relative filesystem path for multi-module (set by the orchestrator).
    """
    group_id: str
    artifact_id: str
    version: Optional[str] = None
    packaging: str = "jar"
    name: Optional[str] = None
    description: Optional[str] = None
    parent_artifact_id: Optional[str] = None
    parent_group_id: Optional[str] = None
    parent_version: Optional[str] = None
    properties: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)
    dep_management: list = field(default_factory=list)
    plugins: list = field(default_factory=list)
    plugin_management: list = field(default_factory=list)
    profiles: list = field(default_factory=list)
    modules: list = field(default_factory=list)
    repositories: list = field(default_factory=list)
    source_dir: Optional[str] = None
