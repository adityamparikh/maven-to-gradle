"""Maven POM parsing, XML helpers, and property resolution.

Handles all interaction with pom.xml files: parsing dependencies, plugins,
profiles, modules, repositories, and resolving Maven property expressions.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .pom_models import Dependency, MavenModule, MavenProfile, Plugin

# XML namespace used by Maven POM files (POM model version 4.0.0).
NS = {"m": "http://maven.apache.org/POM/4.0.0"}


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


def resolve_property(value: str, properties: dict, _depth: int = 0) -> Optional[str]:
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
                    return resolve_property(resolved, properties, _depth + 1)
                return resolved
    return value


def is_bom_import(dep: Dependency) -> bool:
    """Check whether a dependency is a BOM import (``type=pom``, ``scope=import``).

    Args:
        dep: The dependency to check.

    Returns:
        ``True`` if this is a BOM import in ``<dependencyManagement>``.
    """
    return dep.dep_type == "pom" and dep.scope == "import"
