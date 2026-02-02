#!/usr/bin/env python3
"""Maven to Gradle KTS + Version Catalogs migration script.

Parses Maven pom.xml files (single or multi-module) and generates Gradle Kotlin
DSL build files with a centralized version catalog.

Generated files:
    - gradle/libs.versions.toml  — version catalog with dependencies, BOMs, plugins
    - settings.gradle.kts        — project name, module includes, repository config
    - build.gradle.kts           — root build file (and per-module for multi-module)
    - gradle.properties          — Gradle daemon, parallelism, caching settings

Usage:
    python migrate.py <path-to-maven-project> [--output <output-dir>] [--dry-run]
    python migrate.py <path-to-maven-project> --mode overlay [--dry-run]

Modes:
    migrate (default) — full migration, suggests removing pom.xml after verification
    overlay           — dual-build, keeps Maven and Gradle side by side

If --output is omitted, files are written alongside the Maven project.
If --dry-run is given, outputs are printed to stdout instead of written.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# XML namespace used by Maven POM files (POM model version 4.0.0).
NS = {"m": "http://maven.apache.org/POM/4.0.0"}


# ── Data classes ──────────────────────────────────────────────────────────────

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
        executions: Raw execution elements (currently unused, reserved for future).
    """
    group_id: str
    artifact_id: str
    version: Optional[str] = None
    configuration: dict = field(default_factory=dict)
    executions: list = field(default_factory=list)


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


# ── POM parsing ───────────────────────────────────────────────────────────────

def _find(el, tag, ns=NS):
    """Find a direct child XML element, trying with and without the Maven namespace.

    Args:
        el: Parent XML element to search within.
        tag: Tag name to look for (without namespace prefix).
        ns: Namespace mapping (defaults to Maven POM 4.0.0).

    Returns:
        The first matching child element, or ``None`` if not found.
    """
    result = el.find(f"m:{tag}", ns)
    if result is not None:
        return result
    result = el.find(tag)
    if result is not None:
        return result
    return None


def _text(el, tag, ns=NS):
    """Extract the text content of a child element.

    Args:
        el: Parent XML element.
        tag: Tag name of the child element.
        ns: Namespace mapping.

    Returns:
        Stripped text content, or ``None`` if the element doesn't exist or is empty.
    """
    child = _find(el, tag, ns)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_dependency(dep_el) -> Dependency:
    """Parse a ``<dependency>`` XML element into a Dependency dataclass.

    Extracts scope, optional flag, and any ``<exclusions>`` children.

    Args:
        dep_el: The ``<dependency>`` XML element.

    Returns:
        A populated Dependency instance.
    """
    scope = _text(dep_el, "scope") or "compile"
    optional_text = _text(dep_el, "optional")
    optional = optional_text and optional_text.lower() == "true"
    exclusions = []
    excl_el = _find(dep_el, "exclusions")
    if excl_el is not None:
        for ex in list(excl_el.findall("m:exclusion", NS)) + list(excl_el.findall("exclusion")):
            eg = _text(ex, "groupId")
            ea = _text(ex, "artifactId")
            if eg and ea:
                exclusions.append((eg, ea))
    return Dependency(
        group_id=_text(dep_el, "groupId") or "",
        artifact_id=_text(dep_el, "artifactId") or "",
        version=_text(dep_el, "version"),
        scope=scope,
        classifier=_text(dep_el, "classifier"),
        dep_type=_text(dep_el, "type"),
        optional=optional,
        exclusions=exclusions,
    )


def _parse_plugin_config(config_el) -> dict:
    """Recursively flatten a plugin ``<configuration>`` block into a Python dict.

    Nested elements with children become sub-dicts or lists of strings.
    Leaf elements become string values keyed by their tag name.

    Args:
        config_el: The ``<configuration>`` XML element, or ``None``.

    Returns:
        A dict mapping tag names to string values, lists, or nested dicts.
        Returns an empty dict if config_el is ``None``.
    """
    if config_el is None:
        return {}
    result = {}
    for child in config_el:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if len(child) > 0:
            # Nested — collect as list of text items or sub-dict
            items = []
            for sub in child:
                if sub.text and sub.text.strip():
                    items.append(sub.text.strip())
            if items:
                result[tag] = items
            else:
                result[tag] = _parse_plugin_config(child)
        elif child.text and child.text.strip():
            result[tag] = child.text.strip()
    return result


def _parse_plugin(plugin_el) -> Plugin:
    """Parse a ``<plugin>`` XML element into a Plugin dataclass.

    Args:
        plugin_el: The ``<plugin>`` XML element.

    Returns:
        A populated Plugin instance. If groupId is absent, defaults to
        ``org.apache.maven.plugins``.
    """
    config_el = _find(plugin_el, "configuration")
    return Plugin(
        group_id=_text(plugin_el, "groupId") or "org.apache.maven.plugins",
        artifact_id=_text(plugin_el, "artifactId") or "",
        version=_text(plugin_el, "version"),
        configuration=_parse_plugin_config(config_el),
    )


def _parse_profile(profile_el) -> MavenProfile:
    """Parse a ``<profile>`` XML element into a MavenProfile dataclass.

    Extracts activation conditions (activeByDefault, jdk, property, os),
    profile-scoped dependencies, plugins, and properties.

    Args:
        profile_el: The ``<profile>`` XML element.

    Returns:
        A populated MavenProfile instance.
    """
    pid = _text(profile_el, "id") or "default"
    activation = {}
    act_el = _find(profile_el, "activation")
    if act_el is not None:
        by_default = _text(act_el, "activeByDefault")
        if by_default:
            activation["activeByDefault"] = by_default.lower() == "true"
        jdk_el = _find(act_el, "jdk")
        if jdk_el is not None and jdk_el.text:
            activation["jdk"] = jdk_el.text.strip()
        prop_el = _find(act_el, "property")
        if prop_el is not None:
            activation["property"] = {
                "name": _text(prop_el, "name"),
                "value": _text(prop_el, "value"),
            }
        os_el = _find(act_el, "os")
        if os_el is not None:
            activation["os"] = {
                "name": _text(os_el, "name"),
                "family": _text(os_el, "family"),
            }

    deps = []
    deps_el = _find(profile_el, "dependencies")
    if deps_el is not None:
        for dep_el in list(deps_el.findall("m:dependency", NS)) + list(deps_el.findall("dependency")):
            deps.append(_parse_dependency(dep_el))

    plugins = []
    build_el = _find(profile_el, "build")
    if build_el is not None:
        plugins_el = _find(build_el, "plugins")
        if plugins_el is not None:
            for p in list(plugins_el.findall("m:plugin", NS)) + list(plugins_el.findall("plugin")):
                plugins.append(_parse_plugin(p))

    props = {}
    props_el = _find(profile_el, "properties")
    if props_el is not None:
        for child in props_el:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child.text:
                props[tag] = child.text.strip()

    return MavenProfile(
        profile_id=pid,
        activation=activation,
        dependencies=deps,
        plugins=plugins,
        properties=props,
    )


def parse_pom(pom_path: Path) -> MavenModule:
    """Parse a ``pom.xml`` file into a MavenModule.

    Handles both namespaced and non-namespaced POM files. Extracts parent
    info, properties, dependencies, dependency management, plugins, plugin
    management, profiles, modules, and repositories.

    Args:
        pom_path: Filesystem path to the pom.xml file.

    Returns:
        A fully populated MavenModule instance. Fields not present in the
        POM (e.g. groupId) are inherited from the parent if available.
    """
    tree = ET.parse(pom_path)
    root = tree.getroot()

    # Parent info
    parent_el = _find(root, "parent")
    parent_gid = parent_aid = parent_ver = None
    if parent_el is not None:
        parent_gid = _text(parent_el, "groupId")
        parent_aid = _text(parent_el, "artifactId")
        parent_ver = _text(parent_el, "version")

    group_id = _text(root, "groupId") or parent_gid or ""
    artifact_id = _text(root, "artifactId") or ""
    version = _text(root, "version") or parent_ver
    packaging = _text(root, "packaging") or "jar"

    # Properties
    properties = {}
    props_el = _find(root, "properties")
    if props_el is not None:
        for child in props_el:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child.text:
                properties[tag] = child.text.strip()

    # Dependencies
    dependencies = []
    deps_el = _find(root, "dependencies")
    if deps_el is not None:
        for dep_el in list(deps_el.findall("m:dependency", NS)) + list(deps_el.findall("dependency")):
            dependencies.append(_parse_dependency(dep_el))

    # Dependency management
    dep_mgmt = []
    dm_el = _find(root, "dependencyManagement")
    if dm_el is not None:
        dm_deps = _find(dm_el, "dependencies")
        if dm_deps is not None:
            for dep_el in list(dm_deps.findall("m:dependency", NS)) + list(dm_deps.findall("dependency")):
                dep_mgmt.append(_parse_dependency(dep_el))

    # Plugins
    plugins = []
    plugin_management = []
    build_el = _find(root, "build")
    if build_el is not None:
        plugins_el = _find(build_el, "plugins")
        if plugins_el is not None:
            for p in list(plugins_el.findall("m:plugin", NS)) + list(plugins_el.findall("plugin")):
                plugins.append(_parse_plugin(p))
        pm_el = _find(build_el, "pluginManagement")
        if pm_el is not None:
            pm_plugins = _find(pm_el, "plugins")
            if pm_plugins is not None:
                for p in list(pm_plugins.findall("m:plugin", NS)) + list(pm_plugins.findall("plugin")):
                    plugin_management.append(_parse_plugin(p))

    # Profiles
    profiles = []
    profiles_el = _find(root, "profiles")
    if profiles_el is not None:
        for prof_el in list(profiles_el.findall("m:profile", NS)) + list(profiles_el.findall("profile")):
            profiles.append(_parse_profile(prof_el))

    # Modules
    modules = []
    modules_el = _find(root, "modules")
    if modules_el is not None:
        for mod_el in list(modules_el.findall("m:module", NS)) + list(modules_el.findall("module")):
            if mod_el.text:
                modules.append(mod_el.text.strip())

    # Repositories
    repositories = []
    repos_el = _find(root, "repositories")
    if repos_el is not None:
        for repo_el in list(repos_el.findall("m:repository", NS)) + list(repos_el.findall("repository")):
            repo_id = _text(repo_el, "id")
            repo_url = _text(repo_el, "url")
            if repo_url:
                repositories.append((repo_id or "unknown", repo_url))

    return MavenModule(
        group_id=group_id,
        artifact_id=artifact_id,
        version=version,
        packaging=packaging,
        name=_text(root, "name"),
        description=_text(root, "description"),
        parent_artifact_id=parent_aid,
        parent_group_id=parent_gid,
        parent_version=parent_ver,
        properties=properties,
        dependencies=dependencies,
        dep_management=dep_mgmt,
        plugins=plugins,
        plugin_management=plugin_management,
        profiles=profiles,
        modules=modules,
        repositories=repositories,
    )


# ── Catalog alias generation ─────────────────────────────────────────────────

def _to_alias(group_id: str, artifact_id: str) -> str:
    """Generate a Gradle version catalog alias from Maven GAV coordinates.

    Uses a ``prefix_map`` to collapse common groupId prefixes into short,
    idiomatic catalog aliases. For example:

        org.springframework.boot : spring-boot-starter-web → spring-boot-starter-web
        org.testcontainers       : testcontainers-postgresql → testcontainers-postgresql
        io.awspring.cloud        : spring-cloud-aws-starter-s3 → spring-cloud-aws-starter-s3

    The prefix_map is ordered with more-specific prefixes before less-specific
    ones (e.g. ``io.micronaut.data`` before ``io.micronaut``) because matching
    uses ``str.startswith()`` with first-match-wins semantics.

    Anti-stutter logic prevents aliases like ``spring-boot-spring-boot-starter-web``
    by detecting when the artifact_id already starts with the prefix words.

    Args:
        group_id: Maven groupId.
        artifact_id: Maven artifactId.

    Returns:
        A sanitized kebab-case alias string suitable for ``libs.versions.toml``.
    """
    # Common group prefixes to strip for cleaner aliases.
    # ORDERING MATTERS: more-specific prefixes must come before less-specific
    # ones because matching uses startswith() with first-match-wins.
    prefix_map = {
        # Spring ecosystem
        "org.springframework.boot": "spring-boot",
        "org.springframework.cloud": "spring-cloud",
        "org.springframework.data": "spring-data",
        "org.springframework.security": "spring-security",
        "org.springframework.kafka": "spring-kafka",
        "org.springframework": "spring",
        "io.awspring.cloud": "spring-cloud-aws",
        # Apache
        "org.apache.commons": "commons",
        "org.apache.kafka": "kafka",
        "org.apache.solr": "solr",
        "org.apache.lucene": "lucene",
        "org.apache.httpcomponents": "httpcomponents",
        "org.apache.logging.log4j": "log4j",
        # Jackson
        "com.fasterxml.jackson.core": "jackson",
        "com.fasterxml.jackson.module": "jackson-module",
        "com.fasterxml.jackson.datatype": "jackson-datatype",
        "com.fasterxml.jackson.dataformat": "jackson-dataformat",
        # Reactive / observability
        "io.projectreactor": "reactor",
        "io.micrometer": "micrometer",
        # Quarkus (more-specific before less-specific)
        "io.quarkus.platform": "quarkus-platform",
        "io.quarkus": "quarkus",
        # Micronaut (more-specific before less-specific)
        "io.micronaut.data": "micronaut-data",
        "io.micronaut.sql": "micronaut-sql",
        "io.micronaut.serde": "micronaut-serde",
        "io.micronaut.test": "micronaut-test",
        "io.micronaut.testresources": "micronaut-testresources",
        "io.micronaut.flyway": "micronaut-flyway",
        "io.micronaut.validation": "micronaut-validation",
        "io.micronaut": "micronaut",
        # Networking / gRPC
        "io.grpc": "grpc",
        "io.netty": "netty",
        # Resilience
        "io.github.resilience4j": "resilience4j",
        # Testing
        "org.junit.jupiter": "junit-jupiter",
        "org.mockito": "mockito",
        "org.assertj": "assertj",
        "org.testcontainers": "testcontainers",
        "org.mock-server": "mockserver",
        "org.wiremock": "wiremock",
        "com.github.tomakehurst": "wiremock",
        "org.awaitility": "awaitility",
        # Logging
        "ch.qos.logback": "logback",
        "org.slf4j": "slf4j",
        # ORM / database
        "org.hibernate.orm": "hibernate",
        "org.hibernate.validator": "hibernate-validator",
        "org.mongodb": "mongodb",
        "org.postgresql": "postgresql",
        "com.h2database": "h2",
        "com.mysql": "mysql",
        "org.flywaydb": "flyway",
        "org.liquibase": "liquibase",
        "redis.clients": "redis",
        # AWS
        "software.amazon.awssdk": "aws",
        "com.amazonaws": "aws-classic",
        # Build / annotation processing
        "org.projectlombok": "lombok",
        "org.mapstruct": "mapstruct",
        "com.google.guava": "guava",
        "com.google.cloud.tools": "google-cloud-tools",
        # Jakarta / Javax
        "jakarta.": "jakarta",
        "javax.": "javax",
    }

    alias_prefix = None
    for maven_prefix, catalog_prefix in prefix_map.items():
        if group_id.startswith(maven_prefix):
            alias_prefix = catalog_prefix
            break

    if alias_prefix:
        # Check if prefix alone is the artifact (e.g., "lombok" for lombok, "h2" for h2)
        if alias_prefix == artifact_id or (
            alias_prefix.replace("-", "") == artifact_id.replace("-", "")
        ):
            alias = alias_prefix
        else:
            # If artifact_id already starts with prefix content, avoid stuttering
            # e.g., spring-boot + spring-boot-starter-web → spring-boot-starter-web
            prefix_words = alias_prefix.replace("-", " ").split()
            artifact_words = artifact_id.replace("-", " ").split()
            # Check overlap
            overlap = 0
            for i, pw in enumerate(prefix_words):
                if i < len(artifact_words) and artifact_words[i] == pw:
                    overlap = i + 1
                else:
                    break
            if overlap > 0:
                alias = artifact_id
            else:
                alias = f"{alias_prefix}-{artifact_id}"
    else:
        # Use groupId last segment + artifactId
        group_last = group_id.split(".")[-1]
        if artifact_id.startswith(group_last):
            alias = artifact_id
        else:
            alias = f"{group_last}-{artifact_id}"

    # Sanitize: Gradle catalog aliases use kebab-case (hyphens, dots, or underscores)
    alias = re.sub(r"[^a-zA-Z0-9\-]", "-", alias)
    alias = re.sub(r"-+", "-", alias).strip("-").lower()
    return alias


def _to_version_key(name: str) -> str:
    """Sanitize a name into a kebab-case version reference key.

    Replaces non-alphanumeric characters (except hyphens) with hyphens,
    collapses runs of hyphens, and lowercases.

    Args:
        name: Raw name string to sanitize.

    Returns:
        A clean kebab-case key suitable for the ``[versions]`` section.
    """
    return re.sub(r"[^a-zA-Z0-9\-]", "-", name).strip("-").lower()


def _to_plugin_alias(group_id: str, artifact_id: str) -> str:
    """Generate a plugin alias for the version catalog ``[plugins]`` section.

    Strips common Maven/Gradle plugin suffixes (``-maven-plugin``,
    ``-gradle-plugin``, ``-plugin``) before delegating to ``_to_alias()``.
    Longer suffixes are checked first to avoid partial matches.

    Args:
        group_id: Plugin groupId.
        artifact_id: Plugin artifactId.

    Returns:
        A sanitized plugin alias string.
    """
    name = artifact_id
    for suffix in ["-gradle-plugin", "-maven-plugin", "-plugin"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return _to_alias(group_id, name)


# ── Version catalog generation ────────────────────────────────────────────────

# Maven scope → Gradle configuration mapping.
# Key difference: Maven's "compile" is transitive; Gradle's "implementation" is NOT.
# Use the `java-library` plugin and `api` configuration when transitivity is needed.
SCOPE_MAP = {
    "compile": "implementation",
    "provided": "compileOnly",
    "runtime": "runtimeOnly",
    "test": "testImplementation",
    "system": "compileOnly",
    "import": "platform",
}

# Known Maven plugin → Gradle plugin ID mapping.
# Plugins not in this map and not in PLUGIN_SKIP are silently skipped with no
# catalog entry. Add new mappings here when supporting additional plugins.
PLUGIN_ID_MAP = {
    "spring-boot-maven-plugin": "org.springframework.boot",
    "kotlin-maven-plugin": "org.jetbrains.kotlin.jvm",
    "kotlin-allopen": "org.jetbrains.kotlin.plugin.allopen",
    "kotlin-noarg": "org.jetbrains.kotlin.plugin.noarg",
    "jib-maven-plugin": "com.google.cloud.tools.jib",
    "jacoco-maven-plugin": "jacoco",
    "maven-checkstyle-plugin": "checkstyle",
    "maven-pmd-plugin": "pmd",
    "spotbugs-maven-plugin": "com.github.spotbugs",
    "spotless-maven-plugin": "com.diffplug.spotless",
    "maven-shade-plugin": "com.github.johnrengelman.shadow",
    "maven-war-plugin": "war",
    "maven-ear-plugin": "ear",
    "maven-application-plugin": "application",
    "asciidoctor-maven-plugin": "org.asciidoctor.jvm.convert",
    "flyway-maven-plugin": "org.flywaydb.flyway",
    "jooq-codegen-maven": "nu.studer.jooq",
    "openapi-generator-maven-plugin": "org.openapi.generator",
    "protobuf-maven-plugin": "com.google.protobuf",
    "git-commit-id-plugin": "com.gorylenko.gradle-git-properties",
}

# Maven plugins that have no direct Gradle plugin equivalent.
# These are handled via built-in Gradle tasks, conventions, or are unnecessary.
# Each entry documents the Gradle alternative as an inline comment.
PLUGIN_SKIP = {
    "maven-compiler-plugin",        # → java toolchain / kotlin options
    "maven-surefire-plugin",        # → test task config
    "maven-failsafe-plugin",        # → custom integration test task
    "maven-resources-plugin",       # → processResources task
    "maven-jar-plugin",             # → jar task config
    "maven-source-plugin",          # → java { withSourcesJar() }
    "maven-javadoc-plugin",         # → java { withJavadocJar() }
    "maven-deploy-plugin",          # → maven-publish plugin
    "maven-install-plugin",         # → not needed
    "maven-clean-plugin",           # → built-in
    "maven-site-plugin",            # → not needed
    "maven-project-info-reports-plugin",  # → not needed
    "maven-dependency-plugin",      # → dependencies task / configuration
    "maven-enforcer-plugin",        # → see references/gotchas.md
    "maven-release-plugin",         # → see references/gotchas.md
    "versions-maven-plugin",        # → version catalog + dependabot
    "flatten-maven-plugin",         # → not needed in Gradle
    "maven-antrun-plugin",          # → ant integration in Gradle
}


def _resolve_property(value: str, properties: dict, _depth: int = 0) -> Optional[str]:
    """Resolve ``${property}`` references against a properties dict.

    Only resolves values that are entirely a single ``${...}`` reference
    (anchored regex). Concatenated values like ``${prefix}/${suffix}`` are
    returned unchanged — this is intentional and documented as a known
    limitation.

    Supports chained resolution: if the resolved value is itself a ``${...}``
    reference, recursion follows the chain up to a maximum depth of 10 to
    guard against circular references.

    Also tries stripping the ``project.`` prefix for Maven's ``${project.version}``
    style properties.

    Args:
        value: The string potentially containing a ``${property}`` reference.
        properties: Merged property dict from all parsed Maven modules.
        _depth: Internal recursion counter (callers should not set this).

    Returns:
        The resolved value string, or the original value if unresolvable.
        Returns ``None`` if value is ``None``.
    """
    if not value or _depth > 10:
        return value
    match = re.match(r"^\$\{(.+?)\}$", value)
    if match:
        prop_name = match.group(1)
        # Check direct properties and project.* variants
        for key in [prop_name, prop_name.replace("project.", "")]:
            if key in properties:
                resolved = properties[key]
                # Follow chains: ${foo} → ${bar} → "1.0"
                if resolved and "${" in resolved:
                    return _resolve_property(resolved, properties, _depth + 1)
                return resolved
    return value


def _detect_java_version(properties: dict, plugins: list) -> Optional[str]:
    """Extract the target Java version from Maven properties or compiler plugin config.

    Checks common property names in order of preference, then falls back to
    inspecting the ``maven-compiler-plugin`` ``<configuration>`` block.
    Normalizes legacy ``1.x`` format to just ``x`` (e.g. ``1.8`` → ``8``).

    Args:
        properties: Merged properties dict from all modules.
        plugins: Combined list of plugins and pluginManagement entries.

    Returns:
        Java version string (e.g. ``"21"``), or ``None`` if not detected.
    """
    for prop_name in [
        "java.version", "maven.compiler.release", "maven.compiler.source",
        "maven.compiler.target", "jdk.version", "java.source.version",
    ]:
        if prop_name in properties:
            ver = properties[prop_name]
            # Normalize: 1.8 → 8, 11 → 11
            if ver.startswith("1.") and len(ver) <= 4:
                ver = ver[2:]
            return ver
    # Check compiler plugin configuration
    for p in plugins:
        if p.artifact_id == "maven-compiler-plugin":
            for key in ["release", "source", "target"]:
                if key in p.configuration:
                    ver = p.configuration[key]
                    ver = _resolve_property(ver, properties) or ver
                    if ver.startswith("1.") and len(ver) <= 4:
                        ver = ver[2:]
                    return ver
    return None


def _detect_kotlin_version(properties: dict, plugins: list) -> Optional[str]:
    """Detect if the project uses Kotlin and extract its version.

    Checks for ``kotlin-maven-plugin`` in the plugins list first, then
    falls back to ``kotlin.version`` or ``kotlin-version`` properties.

    Args:
        properties: Merged properties dict.
        plugins: Combined plugins and pluginManagement entries.

    Returns:
        Kotlin version string (e.g. ``"2.2.10"``), or ``None`` if not a Kotlin project.
    """
    for p in plugins:
        if p.artifact_id == "kotlin-maven-plugin":
            if p.version:
                return _resolve_property(p.version, properties) or p.version
    for prop in ["kotlin.version", "kotlin-version"]:
        if prop in properties:
            return properties[prop]
    return None


def _is_spring_boot_project(module: MavenModule) -> bool:
    """Check whether the module is a Spring Boot project.

    Detection checks:
        1. Parent POM is ``spring-boot-starter-parent``
        2. ``spring-boot-maven-plugin`` is in plugins or pluginManagement

    Args:
        module: The root MavenModule to check.

    Returns:
        ``True`` if Spring Boot is detected.
    """
    return module.parent_artifact_id == "spring-boot-starter-parent" or any(
        p.artifact_id == "spring-boot-maven-plugin" for p in module.plugins + module.plugin_management
    )


def _is_bom_import(dep: Dependency) -> bool:
    """Check whether a dependency is a BOM import (``type=pom``, ``scope=import``).

    Args:
        dep: The dependency to check.

    Returns:
        ``True`` if this is a BOM import in ``<dependencyManagement>``.
    """
    return dep.dep_type == "pom" and dep.scope == "import"


def build_version_catalog(
    root_module: MavenModule,
    child_modules: list[MavenModule],
) -> str:
    """Build a ``libs.versions.toml`` content string from parsed Maven modules.

    Produces three TOML sections:
        - ``[versions]``  — version constants (Spring Boot, Java, Kotlin, etc.)
        - ``[libraries]`` — dependency aliases with group/name/version.ref
        - ``[plugins]``   — Gradle plugin aliases with id/version.ref

    Dependencies with unresolvable ``${...}`` versions are commented out with
    a ``# TODO`` marker instead of producing invalid TOML.

    Args:
        root_module: The parsed root pom.xml module.
        child_modules: List of parsed child module pom.xml files.

    Returns:
        A complete ``libs.versions.toml`` file content as a string.
    """
    all_modules = [root_module] + child_modules
    all_properties = dict(root_module.properties)
    for m in child_modules:
        all_properties.update(m.properties)

    # Inter-module artifact IDs to skip
    module_artifact_ids = {root_module.artifact_id}
    for cm in child_modules:
        module_artifact_ids.add(cm.artifact_id)

    versions = OrderedDict()      # version-ref → version string
    libraries = OrderedDict()     # alias → toml line
    plugins_section = OrderedDict()  # alias → toml line

    # Track seen coordinates to deduplicate
    seen_libs = set()
    seen_plugins = set()

    # ── Spring Boot special handling ──
    is_boot = _is_spring_boot_project(root_module)
    boot_version = None
    if is_boot and root_module.parent_version:
        boot_version = _resolve_property(root_module.parent_version, all_properties) or root_module.parent_version
        versions["spring-boot"] = boot_version

    # ── Java / Kotlin versions (stored as versions for reference) ──
    java_ver = _detect_java_version(all_properties, root_module.plugins + root_module.plugin_management)
    if java_ver:
        versions["java"] = java_ver

    kotlin_ver = _detect_kotlin_version(all_properties, root_module.plugins + root_module.plugin_management)
    if kotlin_ver:
        versions["kotlin"] = kotlin_ver

    # ── Collect BOMs from dependencyManagement ──
    for mod in all_modules:
        for dep in mod.dep_management:
            if _is_bom_import(dep):
                coord = (dep.group_id, dep.artifact_id)
                if coord in seen_libs:
                    continue
                seen_libs.add(coord)
                alias = _to_alias(dep.group_id, dep.artifact_id)
                ver = _resolve_property(dep.version, all_properties) if dep.version else None
                if ver and ver.startswith("$"):
                    # Unresolvable property — comment out to avoid invalid TOML
                    print(f"WARNING: Could not resolve version '{dep.version}' for "
                          f"{dep.group_id}:{dep.artifact_id}, commenting out in catalog",
                          file=sys.stderr)
                    libraries[alias] = (
                        f'# {{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'
                        f"  # TODO: resolve version from {dep.version}"
                    )
                elif ver:
                    vref = _to_version_key(alias)
                    versions[vref] = ver
                    libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}", version.ref = "{vref}" }}'
                else:
                    libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'

    # ── Collect non-BOM managed dependencies (these set default versions) ──
    managed_versions = {}  # (groupId, artifactId) → version
    for mod in all_modules:
        for dep in mod.dep_management:
            if not _is_bom_import(dep) and dep.version:
                ver = _resolve_property(dep.version, all_properties) or dep.version
                managed_versions[(dep.group_id, dep.artifact_id)] = ver

    # ── Collect all dependencies ──
    for mod in all_modules:
        for dep in mod.dependencies:
            coord = (dep.group_id, dep.artifact_id)
            if coord in seen_libs:
                continue
            # Skip inter-module dependencies
            if dep.artifact_id in module_artifact_ids and dep.group_id == root_module.group_id:
                continue
            seen_libs.add(coord)
            alias = _to_alias(dep.group_id, dep.artifact_id)

            # Resolve version: explicit > managed > property
            ver = None
            if dep.version:
                ver = _resolve_property(dep.version, all_properties) or dep.version
            elif coord in managed_versions:
                ver = managed_versions[coord]

            if ver and ver.startswith("$"):
                # Unresolvable property — comment out
                print(f"WARNING: Could not resolve version '{ver}' for "
                      f"{dep.group_id}:{dep.artifact_id}, commenting out in catalog",
                      file=sys.stderr)
                libraries[alias] = (
                    f'# {{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'
                    f"  # TODO: resolve version from {ver}"
                )
            elif ver:
                vref = _to_version_key(alias)
                # Deduplicate version refs if same version already tracked
                existing_vref = None
                for k, v in versions.items():
                    if v == ver and k != "java":
                        existing_vref = k
                        break
                if existing_vref:
                    vref = existing_vref
                else:
                    versions[vref] = ver
                libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}", version.ref = "{vref}" }}'
            else:
                # Version managed by BOM or Spring Boot parent — no version in catalog
                libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}" }}'

    # ── Collect plugins ──
    if is_boot:
        plugins_section["spring-boot"] = f'{{ id = "org.springframework.boot", version.ref = "spring-boot" }}'
        # spring-dependency-management version is a Gradle-only concept — omit
        # version to let the Spring Boot plugin manage compatibility
        plugins_section["spring-dependency-management"] = '{ id = "io.spring.dependency-management" }'
        seen_plugins.add("spring-boot-maven-plugin")

    if kotlin_ver:
        plugins_section["kotlin-jvm"] = f'{{ id = "org.jetbrains.kotlin.jvm", version.ref = "kotlin" }}'
        plugins_section["kotlin-spring"] = f'{{ id = "org.jetbrains.kotlin.plugin.spring", version.ref = "kotlin" }}'
        seen_plugins.add("kotlin-maven-plugin")

    for mod in all_modules:
        for p in mod.plugins + mod.plugin_management:
            if p.artifact_id in seen_plugins or p.artifact_id in PLUGIN_SKIP:
                continue
            seen_plugins.add(p.artifact_id)
            gradle_id = PLUGIN_ID_MAP.get(p.artifact_id)
            if not gradle_id:
                continue
            alias = _to_plugin_alias(p.group_id, p.artifact_id)
            ver = _resolve_property(p.version, all_properties) if p.version else None
            if ver and ver.startswith("$"):
                print(f"WARNING: Could not resolve plugin version '{p.version}' for "
                      f"{p.artifact_id}, omitting version in catalog",
                      file=sys.stderr)
                plugins_section[alias] = f'{{ id = "{gradle_id}" }}'
            elif ver:
                vref = _to_version_key(alias)
                versions[vref] = ver
                plugins_section[alias] = f'{{ id = "{gradle_id}", version.ref = "{vref}" }}'
            else:
                plugins_section[alias] = f'{{ id = "{gradle_id}" }}'

    # ── Render TOML ──
    lines = ["[versions]"]
    for k, v in versions.items():
        lines.append(f'{k} = "{v}"')

    lines.append("")
    lines.append("[libraries]")
    for alias, definition in libraries.items():
        lines.append(f"{alias} = {definition}")

    if plugins_section:
        lines.append("")
        lines.append("[plugins]")
        for alias, definition in plugins_section.items():
            lines.append(f"{alias} = {definition}")

    lines.append("")
    return "\n".join(lines)


# ── build.gradle.kts generation ───────────────────────────────────────────────

def _gradle_config(scope: str) -> str:
    """Map a Maven dependency scope to the corresponding Gradle configuration.

    Args:
        scope: Maven scope string (compile, provided, runtime, test, system, import).

    Returns:
        Gradle configuration name (e.g. ``"implementation"``, ``"testImplementation"``).
        Defaults to ``"implementation"`` for unknown scopes.
    """
    return SCOPE_MAP.get(scope, "implementation")


def _is_inter_module_dep(dep: Dependency, root_module: MavenModule, child_modules: list) -> Optional[str]:
    """Check if a dependency refers to another module in the same multi-module project.

    Matches by artifactId + groupId against the root and all child modules.

    Args:
        dep: The dependency to check.
        root_module: The root MavenModule.
        child_modules: List of child MavenModule instances.

    Returns:
        The module's source directory name if it's an inter-module dependency,
        or ``None`` if it's an external dependency.
    """
    all_artifact_ids = {root_module.artifact_id: "."}
    for cm in child_modules:
        all_artifact_ids[cm.artifact_id] = cm.source_dir or cm.artifact_id
    if dep.artifact_id in all_artifact_ids and dep.group_id == root_module.group_id:
        return all_artifact_ids[dep.artifact_id]
    return None


def _is_devtools(dep: Dependency) -> bool:
    """Check if a dependency is Spring Boot DevTools.

    DevTools should use the ``developmentOnly`` configuration in Gradle.

    Args:
        dep: The dependency to check.

    Returns:
        ``True`` if the artifactId is ``spring-boot-devtools``.
    """
    return dep.artifact_id == "spring-boot-devtools"


def generate_build_gradle_kts(
    module: MavenModule,
    root_module: MavenModule,
    catalog_aliases: dict,
    is_root: bool = True,
    is_multi_module: bool = False,
    child_modules: list = None,
) -> str:
    """Generate ``build.gradle.kts`` content for a single module.

    Produces a complete build file including plugins, group/version, Java
    toolchain, Kotlin compiler options, configurations, repositories,
    dependencies, allprojects/subprojects blocks, test config, and profile
    conversion hints.

    Args:
        module: The MavenModule to generate a build file for.
        root_module: The root MavenModule (used for Spring Boot detection, etc.).
        catalog_aliases: Mapping of ``(groupId, artifactId)`` → catalog alias.
        is_root: Whether this is the root module.
        is_multi_module: Whether the project is multi-module.
        child_modules: List of child MavenModule instances (for inter-module deps).

    Returns:
        Complete ``build.gradle.kts`` file content as a string.
    """
    lines = []
    all_properties = dict(root_module.properties)
    all_properties.update(module.properties)

    is_boot = _is_spring_boot_project(root_module)
    has_kotlin = _detect_kotlin_version(all_properties, root_module.plugins + root_module.plugin_management) is not None
    java_ver = _detect_java_version(all_properties, root_module.plugins + root_module.plugin_management)

    # ── Plugins block ──
    if is_root or not is_multi_module:
        lines.append("plugins {")

        if module.packaging == "pom" and is_multi_module:
            # Root POM in multi-module — plugins applied with apply false
            if is_boot:
                lines.append("    alias(libs.plugins.spring.boot) apply false")
                lines.append("    alias(libs.plugins.spring.dependency.management) apply false")
            if has_kotlin:
                lines.append("    alias(libs.plugins.kotlin.jvm) apply false")
                lines.append("    alias(libs.plugins.kotlin.spring) apply false")
        else:
            # Apply Java or Kotlin plugin
            if has_kotlin:
                lines.append("    alias(libs.plugins.kotlin.jvm)")
                lines.append("    alias(libs.plugins.kotlin.spring)")
            else:
                lines.append("    java")

            if is_boot:
                lines.append("    alias(libs.plugins.spring.boot)")
                lines.append("    alias(libs.plugins.spring.dependency.management)")

        # Additional plugins
        for p in module.plugins:
            if p.artifact_id in PLUGIN_SKIP or p.artifact_id in (
                "spring-boot-maven-plugin", "kotlin-maven-plugin"
            ):
                continue
            gradle_id = PLUGIN_ID_MAP.get(p.artifact_id)
            if gradle_id:
                alias = _to_plugin_alias(p.group_id, p.artifact_id)
                safe_alias = alias.replace("-", ".")
                lines.append(f"    alias(libs.plugins.{safe_alias})")

        lines.append("}")
        lines.append("")
    else:
        # Child module in multi-module
        lines.append("plugins {")
        if has_kotlin:
            lines.append("    alias(libs.plugins.kotlin.jvm)")
            lines.append("    alias(libs.plugins.kotlin.spring)")
        else:
            lines.append("    java")
        if is_boot:
            lines.append("    alias(libs.plugins.spring.boot)")
            lines.append("    alias(libs.plugins.spring.dependency.management)")
        for p in module.plugins:
            if p.artifact_id in PLUGIN_SKIP or p.artifact_id in (
                "spring-boot-maven-plugin", "kotlin-maven-plugin"
            ):
                continue
            gradle_id = PLUGIN_ID_MAP.get(p.artifact_id)
            if gradle_id:
                alias = _to_plugin_alias(p.group_id, p.artifact_id)
                safe_alias = alias.replace("-", ".")
                lines.append(f"    alias(libs.plugins.{safe_alias})")
        lines.append("}")
        lines.append("")

    # ── Group / Version ──
    if is_root or not is_multi_module:
        lines.append(f'group = "{module.group_id}"')
        if module.version:
            lines.append(f'version = "{module.version}"')
        lines.append("")

    # ── Java toolchain ──
    if java_ver and not (is_multi_module and module.packaging == "pom"):
        lines.append("java {")
        lines.append("    toolchain {")
        lines.append(f"        languageVersion = JavaLanguageVersion.of({java_ver})")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Kotlin compiler options ──
    if has_kotlin and not (is_multi_module and module.packaging == "pom"):
        lines.append("kotlin {")
        lines.append("    compilerOptions {")
        lines.append("        freeCompilerArgs.addAll(\"-Xjsr305=strict\")")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Configurations (for optional/compileOnly patterns) ──
    has_annotation_processor = any(
        d.scope == "provided" or d.artifact_id in ("lombok", "mapstruct-processor")
        for d in module.dependencies
    )

    if has_annotation_processor and has_kotlin:
        lines.append("configurations {")
        lines.append("    compileOnly {")
        lines.append("        extendsFrom(configurations.annotationProcessor.get())")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Repositories ──
    if is_root:
        lines.append("repositories {")
        lines.append("    mavenCentral()")
        lines.append("}")
        lines.append("")

    # ── Dependencies ──
    if module.dependencies and not (is_multi_module and module.packaging == "pom" and is_root):
        lines.append("dependencies {")

        # BOMs from dependencyManagement
        for dep in module.dep_management:
            if _is_bom_import(dep):
                alias = _to_alias(dep.group_id, dep.artifact_id)
                safe_alias = alias.replace("-", ".")
                lines.append(f"    implementation(platform(libs.{safe_alias}))")

        for dep in module.dependencies:
            # Check if inter-module dependency
            mod_dir = _is_inter_module_dep(dep, root_module, child_modules or [])
            if mod_dir:
                config = _gradle_config(dep.scope)
                lines.append(f'    {config}(project(":{dep.artifact_id}"))')
                continue

            alias = _to_alias(dep.group_id, dep.artifact_id)
            safe_alias = alias.replace("-", ".")
            config = _gradle_config(dep.scope)

            # DevTools → developmentOnly
            if _is_devtools(dep):
                lines.append(f"    developmentOnly(libs.{safe_alias})")
                continue

            # Annotation processors
            is_apt = dep.artifact_id in (
                "lombok", "mapstruct-processor", "hibernate-jpamodelgen",
                "spring-boot-configuration-processor",
            )

            if is_apt:
                dep_ref = f"libs.{safe_alias}"
                if dep.scope == "test":
                    # Test-only annotation processor
                    lines.append(f"    testCompileOnly({dep_ref})")
                    lines.append(f"    testAnnotationProcessor({dep_ref})")
                else:
                    if dep.scope == "provided" or dep.optional:
                        lines.append(f"    compileOnly({dep_ref})")
                    lines.append(f"    annotationProcessor({dep_ref})")
            elif dep.exclusions:
                lines.append(f"    {config}(libs.{safe_alias}) {{")
                for eg, ea in dep.exclusions:
                    lines.append(f'        exclude(group = "{eg}", module = "{ea}")')
                lines.append("    }")
            else:
                if dep.optional and config == "implementation":
                    config = "compileOnly"
                lines.append(f"    {config}(libs.{safe_alias})")

        lines.append("}")
        lines.append("")

    # ── allprojects / subprojects for multi-module root ──
    if is_multi_module and module.packaging == "pom" and is_root:
        lines.append("allprojects {")
        lines.append(f'    group = "{module.group_id}"')
        if module.version:
            lines.append(f'    version = "{module.version}"')
        lines.append("}")
        lines.append("")
        lines.append("subprojects {")
        lines.append("    repositories {")
        lines.append("        mavenCentral()")
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # ── Test configuration ──
    has_tests = any(d.scope == "test" for d in module.dependencies)
    if has_tests and not (is_multi_module and module.packaging == "pom"):
        lines.append("tasks.withType<Test> {")
        lines.append("    useJUnitPlatform()")
        lines.append("}")
        lines.append("")

    # ── Profile conversion hints (as comments) ──
    if module.profiles:
        lines.append("// ── Maven profile equivalents ─────────────────────────────────")
        lines.append("// See references/profiles.md in the migration skill for patterns.")
        for prof in module.profiles:
            lines.append(f"// Profile '{prof.profile_id}':")
            if prof.activation.get("activeByDefault"):
                lines.append("//   → Active by default: apply unconditionally or use a Gradle property")
            if "property" in prof.activation:
                prop = prof.activation["property"]
                lines.append(f'//   → Activated by property: -P{prop.get("name", "?")}={prop.get("value", "")}')
                lines.append(f'//   → Gradle equivalent: if (project.hasProperty("{prop.get("name", "")}")) {{ ... }}')
            if "jdk" in prof.activation:
                lines.append(f"//   → JDK activation: {prof.activation['jdk']}")
            if prof.dependencies:
                lines.append(f"//   → Has {len(prof.dependencies)} dependencies")
            if prof.plugins:
                lines.append(f"//   → Has {len(prof.plugins)} plugins")
        lines.append("")

    return "\n".join(lines)


# ── settings.gradle.kts generation ────────────────────────────────────────────

def generate_settings_gradle_kts(
    root_module: MavenModule,
    child_modules: list[MavenModule] = None,
) -> str:
    """Generate ``settings.gradle.kts`` content with repository and module configuration.

    When custom repositories are detected (beyond Maven Central), generates
    ``pluginManagement`` and ``dependencyResolutionManagement`` blocks.
    Well-known repository URLs (e.g. Spring milestones) are mapped to
    descriptive names.

    For nested multi-module projects, module includes use colon-separated
    Gradle paths (e.g. ``include("parent:child")``).

    Args:
        root_module: The parsed root MavenModule.
        child_modules: Optional list of all child modules (for repository aggregation
            and nested module include paths).

    Returns:
        Complete ``settings.gradle.kts`` file content as a string.
    """
    lines = []
    child_modules = child_modules or []

    # Collect all custom repositories from root + children
    all_repos = list(root_module.repositories)
    for cm in child_modules:
        all_repos.extend(cm.repositories)

    # Deduplicate by URL
    seen_urls = set()
    unique_repos = []
    for repo_id, repo_url in all_repos:
        normalized = repo_url.rstrip("/")
        if normalized not in seen_urls and "repo1.maven.org" not in normalized and "central" not in repo_id.lower():
            seen_urls.add(normalized)
            unique_repos.append((repo_id, normalized))

    has_custom_repos = bool(unique_repos)

    # Generate pluginManagement block (always for multi-module or custom repos)
    if has_custom_repos or root_module.modules:
        lines.append("pluginManagement {")
        lines.append("    repositories {")
        lines.append("        mavenCentral()")
        lines.append("        gradlePluginPortal()")
        for _repo_id, repo_url in unique_repos:
            lines.append(f'        maven {{ url = uri("{repo_url}") }}')
        lines.append("    }")
        lines.append("}")
        lines.append("")

    # Generate dependencyResolutionManagement block if custom repos exist
    if has_custom_repos:
        lines.append("dependencyResolutionManagement {")
        lines.append("    repositories {")
        lines.append("        mavenCentral()")
        for _repo_id, repo_url in unique_repos:
            lines.append(f'        maven {{ url = uri("{repo_url}") }}')
        lines.append("    }")
        lines.append("}")
        lines.append("")

    project_name = root_module.artifact_id
    lines.append(f'rootProject.name = "{project_name}"')
    lines.append("")

    if child_modules:
        for child in child_modules:
            # Convert filesystem path (a/b) to Gradle include path (a:b)
            gradle_path = child.source_dir.replace("/", ":")
            lines.append(f'include("{gradle_path}")')
        lines.append("")

    return "\n".join(lines)


# ── Gradle wrapper + properties ───────────────────────────────────────────────

def generate_gradle_properties(root_module: MavenModule) -> str:
    """Generate ``gradle.properties`` content with build performance settings.

    Enables Gradle daemon, parallel execution, and local build caching.
    Configuration cache is included as a commented-out suggestion since not
    all plugins support it.

    Custom Maven properties (excluding standard Maven/Java/Kotlin prefixes)
    are carried over as comments for reference.

    Args:
        root_module: The root MavenModule (for custom property extraction).

    Returns:
        Complete ``gradle.properties`` file content as a string.
    """
    lines = [
        "# Generated by Maven-to-Gradle migration",
        "org.gradle.daemon=true",
        "org.gradle.parallel=true",
        "org.gradle.caching=true",
        "# org.gradle.configuration-cache=true  # Enable after verifying all plugins support it",
    ]
    # Carry over relevant Maven properties
    for key, value in root_module.properties.items():
        if key.startswith("project.build.sourceEncoding"):
            lines.append(f"# Source encoding: {value}")
        elif key.startswith("project.reporting.outputEncoding"):
            continue
        elif not key.startswith("maven.") and not key.startswith("java.") and not key.startswith("kotlin."):
            # Custom properties — include as Gradle project properties
            safe_key = key.replace(".", "_")
            lines.append(f"# {safe_key}={value}")
    lines.append("")
    return "\n".join(lines)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def generate_gradle_gitignore_entries() -> str:
    """Generate ``.gitignore`` entries for Gradle build artifacts.

    Returns:
        A string of gitignore rules covering ``.gradle/``, ``build/``,
        and the Gradle wrapper JAR exception.
    """
    return """\
# Gradle
.gradle/
build/
!gradle/wrapper/gradle-wrapper.jar
!**/src/main/**/build/
!**/src/test/**/build/
"""


def _parse_modules_recursive(
    project_path: Path,
    module_dirs: list[str],
    parent_path: str = "",
    _visited: set = None,
) -> list[MavenModule]:
    """Recursively parse child modules, handling nested multi-module structures.

    If a child module itself declares ``<modules>``, those nested modules are
    also parsed and included in the result. Visited paths are tracked to
    prevent infinite recursion from circular module references.

    Args:
        project_path: Filesystem path to the root project.
        module_dirs: List of module directory names from the parent's ``<modules>``.
        parent_path: The relative path prefix for nested modules (e.g. ``"parent-mod"``).
        _visited: Internal set of visited paths (callers should not set this).

    Returns:
        Flat list of all MavenModule instances (depth-first), with ``source_dir``
        set to the relative filesystem path from the project root.
    """
    if _visited is None:
        _visited = set()

    result = []
    for mod_dir in module_dirs:
        relative_dir = f"{parent_path}/{mod_dir}" if parent_path else mod_dir
        # Guard against circular references
        abs_path = (project_path / relative_dir).resolve()
        if abs_path in _visited:
            continue
        _visited.add(abs_path)

        child_pom = project_path / relative_dir / "pom.xml"
        if child_pom.exists():
            child = parse_pom(child_pom)
            child.source_dir = relative_dir
            result.append(child)
            # Recurse into nested modules
            if child.modules:
                nested = _parse_modules_recursive(
                    project_path, child.modules, relative_dir, _visited
                )
                result.extend(nested)
        else:
            print(f"WARNING: Module '{relative_dir}' has no pom.xml, skipping",
                  file=sys.stderr)
    return result


def migrate(project_path: Path, output_path: Optional[Path] = None, dry_run: bool = False, mode: str = "migrate"):
    """Run the full Maven-to-Gradle migration.

    Orchestrates the entire migration pipeline: parses all pom.xml files,
    generates the version catalog, settings, build files, and properties,
    then either prints (dry-run) or writes the output.

    Args:
        project_path: Filesystem path to the Maven project root (containing pom.xml).
        output_path: Directory to write generated files to. Defaults to ``project_path``.
        dry_run: If ``True``, prints generated content to stdout instead of writing files.
        mode: ``"migrate"`` for full migration, ``"overlay"`` for dual-build (keeps Maven).
    """
    root_pom = project_path / "pom.xml"
    if not root_pom.exists():
        print(f"ERROR: No pom.xml found at {root_pom}", file=sys.stderr)
        sys.exit(1)

    out = output_path or project_path
    root_module = parse_pom(root_pom)
    root_module.source_dir = "."

    is_multi = bool(root_module.modules)

    # Parse child modules (recursively for nested multi-module projects)
    child_modules = []
    if is_multi:
        child_modules = _parse_modules_recursive(project_path, root_module.modules)

    # Build catalog alias lookup
    all_deps = root_module.dependencies + root_module.dep_management
    for cm in child_modules:
        all_deps += cm.dependencies + cm.dep_management
    catalog_aliases = {}
    # Track inter-module artifact IDs to exclude from catalog
    module_artifact_ids = {root_module.artifact_id}
    for cm in child_modules:
        module_artifact_ids.add(cm.artifact_id)
    for dep in all_deps:
        if dep.artifact_id not in module_artifact_ids or dep.group_id != root_module.group_id:
            alias = _to_alias(dep.group_id, dep.artifact_id)
            catalog_aliases[(dep.group_id, dep.artifact_id)] = alias

    # Generate files
    catalog_content = build_version_catalog(root_module, child_modules)
    settings_content = generate_settings_gradle_kts(root_module, child_modules)
    root_build_content = generate_build_gradle_kts(
        root_module, root_module, catalog_aliases,
        is_root=True, is_multi_module=is_multi, child_modules=child_modules,
    )
    gradle_props = generate_gradle_properties(root_module)

    is_overlay = mode == "overlay"

    if dry_run:
        print("=" * 60)
        print("gradle/libs.versions.toml")
        print("=" * 60)
        print(catalog_content)
        print()
        print("=" * 60)
        print("settings.gradle.kts")
        print("=" * 60)
        print(settings_content)
        print()
        print("=" * 60)
        print("build.gradle.kts (root)")
        print("=" * 60)
        print(root_build_content)
        print()
        print("=" * 60)
        print("gradle.properties")
        print("=" * 60)
        print(gradle_props)

        for child in child_modules:
            child_build = generate_build_gradle_kts(
                child, root_module, catalog_aliases,
                is_root=False, is_multi_module=True, child_modules=child_modules,
            )
            print()
            print("=" * 60)
            print(f"{child.source_dir}/build.gradle.kts")
            print("=" * 60)
            print(child_build)

        if is_overlay:
            print()
            print("=" * 60)
            print(".gitignore (append)")
            print("=" * 60)
            print(generate_gradle_gitignore_entries())
    else:
        _write(out / "gradle" / "libs.versions.toml", catalog_content)
        _write(out / "settings.gradle.kts", settings_content)
        _write(out / "build.gradle.kts", root_build_content)
        _write(out / "gradle.properties", gradle_props)

        for child in child_modules:
            child_build = generate_build_gradle_kts(
                child, root_module, catalog_aliases,
                is_root=False, is_multi_module=True, child_modules=child_modules,
            )
            _write(out / child.source_dir / "build.gradle.kts", child_build)

        if is_overlay:
            # Append Gradle entries to .gitignore
            gitignore_path = out / ".gitignore"
            gitignore_entries = generate_gradle_gitignore_entries()
            if gitignore_path.exists():
                existing = gitignore_path.read_text(encoding="utf-8")
                if ".gradle/" not in existing:
                    with open(gitignore_path, "a", encoding="utf-8") as f:
                        f.write("\n" + gitignore_entries)
                    print(f"  ✓ {gitignore_path} (appended Gradle entries)")
                else:
                    print(f"  ⏭ {gitignore_path} (Gradle entries already present)")
            else:
                _write(gitignore_path, gitignore_entries)

        if is_overlay:
            print(f"\n✅ Gradle overlay complete! Generated files in: {out}")
        else:
            print(f"\n✅ Migration complete! Generated files in: {out}")
        print("\nGenerated files:")
        print("  gradle/libs.versions.toml")
        print("  settings.gradle.kts")
        print("  build.gradle.kts")
        print("  gradle.properties")
        for child in child_modules:
            print(f"  {child.source_dir}/build.gradle.kts")
        print("\n⚠️  Next steps:")
        print("  1. Review generated files and adjust as needed")
        print("  2. Run: gradle wrapper  # uses your installed Gradle version")
        print("  3. Run: ./gradlew build")
        print("  4. Fix any compilation or test issues")
        if is_overlay:
            print("  5. Both Maven and Gradle builds are now available side by side")
            print("     Keep pom.xml and build.gradle.kts in sync when adding dependencies")
            print("     See references/dual-build.md for maintenance guidance")
        else:
            print("  5. Delete pom.xml files once migration is verified")


def _write(path: Path, content: str):
    """Write content to a file, creating parent directories as needed.

    Args:
        path: Filesystem path to write to.
        content: File content string (UTF-8 encoded).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  ✓ {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point. Parses arguments and delegates to ``migrate()``."""
    parser = argparse.ArgumentParser(
        description="Migrate Maven project to Gradle KTS with version catalogs"
    )
    parser.add_argument("project", type=Path, help="Path to Maven project root")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output directory (default: project dir)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Print output without writing files")
    parser.add_argument(
        "--mode", "-m", choices=["migrate", "overlay"], default="migrate",
        help="'migrate' (default) for full migration, 'overlay' for dual-build (keeps Maven)"
    )
    args = parser.parse_args()

    migrate(args.project, args.output, args.dry_run, args.mode)


if __name__ == "__main__":
    main()
