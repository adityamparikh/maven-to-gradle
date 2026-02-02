#!/usr/bin/env python3
"""Maven to Gradle KTS + Version Catalogs migration script.

Parses Maven pom.xml files (single or multi-module) and generates:
  - gradle/libs.versions.toml (version catalog)
  - settings.gradle.kts
  - build.gradle.kts (root and per-module)

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
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

NS = {"m": "http://maven.apache.org/POM/4.0.0"}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Dependency:
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
    group_id: str
    artifact_id: str
    version: Optional[str] = None
    configuration: dict = field(default_factory=dict)
    executions: list = field(default_factory=list)


@dataclass
class MavenProfile:
    profile_id: str
    activation: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)
    plugins: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)


@dataclass
class MavenModule:
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
    modules: list = field(default_factory=list)  # child module directory names
    source_dir: Optional[str] = None  # relative path for multi-module


# ── POM parsing ───────────────────────────────────────────────────────────────

def _find(el, tag, ns=NS):
    """Find a child element, trying with and without namespace."""
    result = el.find(f"m:{tag}", ns)
    if result is not None:
        return result
    result = el.find(tag)
    if result is not None:
        return result
    return None


def _text(el, tag, ns=NS):
    """Get text of a child element, or None."""
    child = _find(el, tag, ns)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_dependency(dep_el) -> Dependency:
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
    """Flatten plugin <configuration> into a dict."""
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
    config_el = _find(plugin_el, "configuration")
    return Plugin(
        group_id=_text(plugin_el, "groupId") or "org.apache.maven.plugins",
        artifact_id=_text(plugin_el, "artifactId") or "",
        version=_text(plugin_el, "version"),
        configuration=_parse_plugin_config(config_el),
    )


def _parse_profile(profile_el) -> MavenProfile:
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
    """Parse a pom.xml into a MavenModule."""
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
    )


# ── Catalog alias generation ─────────────────────────────────────────────────

def _to_alias(group_id: str, artifact_id: str) -> str:
    """Generate a version catalog alias from Maven coordinates.

    Follows Gradle convention: segments separated by hyphens,
    with common prefixes collapsed for readability.
    """
    # Common group prefixes to strip for cleaner aliases
    prefix_map = {
        "org.springframework.boot": "spring-boot",
        "org.springframework.cloud": "spring-cloud",
        "org.springframework.data": "spring-data",
        "org.springframework.security": "spring-security",
        "org.springframework.kafka": "spring-kafka",
        "org.springframework": "spring",
        "org.apache.commons": "commons",
        "org.apache.kafka": "kafka",
        "org.apache.solr": "solr",
        "org.apache.lucene": "lucene",
        "org.apache.httpcomponents": "httpcomponents",
        "org.apache.logging.log4j": "log4j",
        "com.fasterxml.jackson.core": "jackson",
        "com.fasterxml.jackson.module": "jackson-module",
        "com.fasterxml.jackson.datatype": "jackson-datatype",
        "com.fasterxml.jackson.dataformat": "jackson-dataformat",
        "io.projectreactor": "reactor",
        "io.micrometer": "micrometer",
        "org.junit.jupiter": "junit-jupiter",
        "org.mockito": "mockito",
        "org.assertj": "assertj",
        "org.testcontainers": "testcontainers",
        "ch.qos.logback": "logback",
        "org.slf4j": "slf4j",
        "org.projectlombok": "lombok",
        "com.google.guava": "guava",
        "com.google.cloud.tools": "google-cloud-tools",
        "com.h2database": "h2",
        "org.postgresql": "postgresql",
        "com.mysql": "mysql",
        "org.flywaydb": "flyway",
        "org.liquibase": "liquibase",
        "org.mapstruct": "mapstruct",
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
    """Generate a version reference key."""
    return re.sub(r"[^a-zA-Z0-9\-]", "-", name).strip("-").lower()


def _to_plugin_alias(group_id: str, artifact_id: str) -> str:
    """Generate a plugin alias for version catalog."""
    # Strip common suffixes (longer suffixes first to avoid partial matches)
    name = artifact_id
    for suffix in ["-gradle-plugin", "-maven-plugin", "-plugin"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return _to_alias(group_id, name)


# ── Version catalog generation ────────────────────────────────────────────────

SCOPE_MAP = {
    "compile": "implementation",
    "provided": "compileOnly",
    "runtime": "runtimeOnly",
    "test": "testImplementation",
    "system": "compileOnly",
    "import": "platform",
}

# Known Maven plugin → Gradle plugin ID mapping
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

# Plugins that have no direct Gradle equivalent or are handled differently
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


def _resolve_property(value: str, properties: dict) -> Optional[str]:
    """Resolve ${property} references."""
    if not value:
        return value
    match = re.match(r"^\$\{(.+?)\}$", value)
    if match:
        prop_name = match.group(1)
        # Check direct properties and project.* variants
        for key in [prop_name, prop_name.replace("project.", "")]:
            if key in properties:
                return properties[key]
    return value


def _detect_java_version(properties: dict, plugins: list) -> Optional[str]:
    """Extract Java version from properties or compiler plugin config."""
    # Common property names for Java version
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
    """Detect if project uses Kotlin and extract version."""
    for p in plugins:
        if p.artifact_id == "kotlin-maven-plugin":
            if p.version:
                return _resolve_property(p.version, properties) or p.version
    for prop in ["kotlin.version", "kotlin-version"]:
        if prop in properties:
            return properties[prop]
    return None


def _is_spring_boot_project(module: MavenModule) -> bool:
    return module.parent_artifact_id == "spring-boot-starter-parent" or any(
        p.artifact_id == "spring-boot-maven-plugin" for p in module.plugins + module.plugin_management
    )


def _is_bom_import(dep: Dependency) -> bool:
    return dep.dep_type == "pom" and dep.scope == "import"


def build_version_catalog(
    root_module: MavenModule,
    child_modules: list[MavenModule],
) -> str:
    """Build a libs.versions.toml content string."""
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
                if ver:
                    vref = _to_version_key(alias)
                    versions[vref] = ver
                    libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}", version.ref = "{vref}" }}'
                elif dep.version and dep.version.startswith("${"):
                    libraries[alias] = f'{{ group = "{dep.group_id}", name = "{dep.artifact_id}", version = "{dep.version}" }}'
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

            if ver and not ver.startswith("$"):
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
        plugins_section["spring-dependency-management"] = '{ id = "io.spring.dependency-management", version = "1.1.7" }'
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
            if ver and not ver.startswith("$"):
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
    return SCOPE_MAP.get(scope, "implementation")


def _is_inter_module_dep(dep: Dependency, root_module: MavenModule, child_modules: list) -> Optional[str]:
    """Check if a dependency is an inter-module reference. Returns module dir name or None."""
    all_artifact_ids = {root_module.artifact_id: "."}
    for cm in child_modules:
        all_artifact_ids[cm.artifact_id] = cm.source_dir or cm.artifact_id
    if dep.artifact_id in all_artifact_ids and dep.group_id == root_module.group_id:
        return all_artifact_ids[dep.artifact_id]
    return None


def _is_devtools(dep: Dependency) -> bool:
    return dep.artifact_id == "spring-boot-devtools"


def generate_build_gradle_kts(
    module: MavenModule,
    root_module: MavenModule,
    catalog_aliases: dict,
    is_root: bool = True,
    is_multi_module: bool = False,
    child_modules: list = None,
) -> str:
    """Generate build.gradle.kts content for a module."""
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

        # Inter-module dependencies (for child modules referencing siblings)
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

def generate_settings_gradle_kts(root_module: MavenModule) -> str:
    lines = []

    project_name = root_module.artifact_id
    lines.append(f'rootProject.name = "{project_name}"')
    lines.append("")

    if root_module.modules:
        for mod_dir in root_module.modules:
            # Convention: module directory name becomes the Gradle project name
            lines.append(f'include("{mod_dir}")')
        lines.append("")

    return "\n".join(lines)


# ── Gradle wrapper + properties ───────────────────────────────────────────────

def generate_gradle_properties(root_module: MavenModule) -> str:
    lines = [
        "# Generated by Maven-to-Gradle migration",
        "org.gradle.daemon=true",
        "org.gradle.parallel=true",
        "org.gradle.caching=true",
        "org.gradle.configuration-cache=true",
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
    """Generate .gitignore entries for Gradle build artifacts."""
    return """\
# Gradle
.gradle/
build/
!gradle/wrapper/gradle-wrapper.jar
!**/src/main/**/build/
!**/src/test/**/build/
"""


def migrate(project_path: Path, output_path: Optional[Path] = None, dry_run: bool = False, mode: str = "migrate"):
    """Run the full migration.

    Args:
        mode: 'migrate' for full migration, 'overlay' for dual-build (keeps Maven).
    """
    root_pom = project_path / "pom.xml"
    if not root_pom.exists():
        print(f"ERROR: No pom.xml found at {root_pom}", file=sys.stderr)
        sys.exit(1)

    out = output_path or project_path
    root_module = parse_pom(root_pom)
    root_module.source_dir = "."

    is_multi = bool(root_module.modules)

    # Parse child modules
    child_modules = []
    if is_multi:
        for mod_dir in root_module.modules:
            child_pom = project_path / mod_dir / "pom.xml"
            if child_pom.exists():
                child = parse_pom(child_pom)
                child.source_dir = mod_dir
                child_modules.append(child)
            else:
                print(f"WARNING: Module '{mod_dir}' has no pom.xml, skipping", file=sys.stderr)

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
    settings_content = generate_settings_gradle_kts(root_module)
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
        print("  2. Run: gradle wrapper --gradle-version=8.12")
        print("  3. Run: ./gradlew build")
        print("  4. Fix any compilation or test issues")
        if is_overlay:
            print("  5. Both Maven and Gradle builds are now available side by side")
            print("     Keep pom.xml and build.gradle.kts in sync when adding dependencies")
            print("     See references/dual-build.md for maintenance guidance")
        else:
            print("  5. Delete pom.xml files once migration is verified")


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  ✓ {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
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
