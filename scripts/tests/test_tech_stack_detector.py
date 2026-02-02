"""Tests for tech_stack_detector.py — Java/Kotlin/Spring Boot detection."""

from migrate.tech_stack_detector import (
    detect_java_version,
    detect_kotlin_version,
    is_spring_boot_project,
    is_devtools,
)
from migrate.pom_models import Dependency, MavenModule, Plugin


class TestDetectJavaVersion:
    def test_java_version_property(self):
        assert detect_java_version({"java.version": "21"}, []) == "21"

    def test_maven_compiler_release(self):
        assert detect_java_version({"maven.compiler.release": "17"}, []) == "17"

    def test_maven_compiler_source(self):
        assert detect_java_version({"maven.compiler.source": "11"}, []) == "11"

    def test_normalizes_1x_format(self):
        assert detect_java_version({"java.version": "1.8"}, []) == "8"

    def test_no_normalization_for_modern(self):
        assert detect_java_version({"java.version": "17"}, []) == "17"

    def test_compiler_plugin_fallback(self):
        plugin = Plugin(
            group_id="org.apache.maven.plugins",
            artifact_id="maven-compiler-plugin",
            configuration={"release": "21"},
        )
        assert detect_java_version({}, [plugin]) == "21"

    def test_compiler_plugin_with_property_ref(self):
        plugin = Plugin(
            group_id="org.apache.maven.plugins",
            artifact_id="maven-compiler-plugin",
            configuration={"source": "${java.version}"},
        )
        assert detect_java_version({"java.version": "17"}, [plugin]) == "17"

    def test_compiler_plugin_normalizes_1x_format(self):
        plugin = Plugin(
            group_id="org.apache.maven.plugins",
            artifact_id="maven-compiler-plugin",
            configuration={"source": "1.8"},
        )
        assert detect_java_version({}, [plugin]) == "8"

    def test_returns_none_when_not_detected(self):
        assert detect_java_version({}, []) is None

    def test_property_priority_order(self):
        props = {
            "maven.compiler.source": "11",
            "java.version": "21",
        }
        # java.version comes first in the search order
        assert detect_java_version(props, []) == "21"


class TestDetectKotlinVersion:
    def test_kotlin_maven_plugin(self):
        plugin = Plugin(
            group_id="org.jetbrains.kotlin",
            artifact_id="kotlin-maven-plugin",
            version="2.0.0",
        )
        assert detect_kotlin_version({}, [plugin]) == "2.0.0"

    def test_kotlin_version_property(self):
        assert detect_kotlin_version({"kotlin.version": "1.9.24"}, []) == "1.9.24"

    def test_kotlin_version_alt_property(self):
        assert detect_kotlin_version({"kotlin-version": "2.0.0"}, []) == "2.0.0"

    def test_returns_none_for_non_kotlin(self):
        assert detect_kotlin_version({}, []) is None

    def test_plugin_version_with_property_ref(self):
        plugin = Plugin(
            group_id="org.jetbrains.kotlin",
            artifact_id="kotlin-maven-plugin",
            version="${kotlin.version}",
        )
        assert detect_kotlin_version({"kotlin.version": "2.0.0"}, [plugin]) == "2.0.0"


class TestIsSpringBootProject:
    def test_detected_via_parent(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="app",
            parent_artifact_id="spring-boot-starter-parent",
        )
        assert is_spring_boot_project(module) is True

    def test_detected_via_plugin(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="app",
            plugins=[
                Plugin(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-maven-plugin",
                ),
            ],
        )
        assert is_spring_boot_project(module) is True

    def test_detected_via_plugin_management(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="app",
            plugin_management=[
                Plugin(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-maven-plugin",
                ),
            ],
        )
        assert is_spring_boot_project(module) is True

    def test_not_spring_boot(self):
        module = MavenModule(group_id="com.example", artifact_id="plain-java")
        assert is_spring_boot_project(module) is False


class TestIsDevtools:
    def test_devtools_detected(self):
        dep = Dependency(
            group_id="org.springframework.boot",
            artifact_id="spring-boot-devtools",
        )
        assert is_devtools(dep) is True

    def test_non_devtools(self):
        dep = Dependency(
            group_id="org.springframework.boot",
            artifact_id="spring-boot-starter-web",
        )
        assert is_devtools(dep) is False
