"""Tests for gradle_file_generator.py — Gradle file generation."""

from migrate.gradle_file_generator import (
    _is_inter_module_dep,
    build_version_catalog,
    generate_build_gradle_kts,
    generate_settings_gradle_kts,
    generate_gradle_properties,
    generate_gradle_gitignore_entries,
)
from migrate.pom_models import Dependency, MavenModule, MavenProfile, Plugin


class TestBuildVersionCatalog:
    def test_spring_boot_versions(self, spring_boot_module):
        catalog = build_version_catalog(spring_boot_module, [])
        assert '[versions]' in catalog
        assert 'spring-boot = "3.4.1"' in catalog

    def test_java_version_in_catalog(self, spring_boot_module):
        catalog = build_version_catalog(spring_boot_module, [])
        assert 'java = "21"' in catalog

    def test_libraries_section(self, spring_boot_module):
        catalog = build_version_catalog(spring_boot_module, [])
        assert '[libraries]' in catalog
        assert 'spring-boot-starter-web' in catalog
        assert 'spring-boot-starter-test' in catalog

    def test_plugins_section_for_spring_boot(self, spring_boot_module):
        catalog = build_version_catalog(spring_boot_module, [])
        assert '[plugins]' in catalog
        assert 'spring-boot' in catalog
        assert 'spring-dependency-management' in catalog

    def test_bom_in_catalog(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            dep_management=[
                Dependency(
                    group_id="org.springframework.cloud",
                    artifact_id="spring-cloud-dependencies",
                    version="2024.0.0",
                    dep_type="pom",
                    scope="import",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert 'spring-cloud-dependencies' in catalog
        assert '2024.0.0' in catalog

    def test_inter_module_deps_excluded(self, multi_module_root, child_core_module):
        catalog = build_version_catalog(multi_module_root, [child_core_module])
        # 'core' is a child module, should not appear as a library
        lines = catalog.split("\n")
        lib_lines = [l for l in lines if l.startswith("core =")]
        assert len(lib_lines) == 0

    def test_kotlin_version_in_catalog(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert 'kotlin = "2.0.0"' in catalog
        assert 'kotlin-jvm' in catalog

    def test_unresolvable_version_commented_out(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            dependencies=[
                Dependency(
                    group_id="com.acme",
                    artifact_id="lib",
                    version="${unresolvable}",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert "# TODO" in catalog

    def test_bom_with_unresolvable_version_commented_out(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            dep_management=[
                Dependency(
                    group_id="org.springframework.cloud",
                    artifact_id="spring-cloud-dependencies",
                    version="${spring-cloud.version}",
                    dep_type="pom",
                    scope="import",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert "# TODO" in catalog
        assert "spring-cloud-dependencies" in catalog

    def test_bom_with_no_version(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            dep_management=[
                Dependency(
                    group_id="org.springframework.cloud",
                    artifact_id="spring-cloud-dependencies",
                    dep_type="pom",
                    scope="import",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert 'spring-cloud-dependencies = { group = "org.springframework.cloud", name = "spring-cloud-dependencies" }' in catalog

    def test_managed_dependency_version_used_as_fallback(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            dep_management=[
                Dependency(
                    group_id="com.google.guava",
                    artifact_id="guava",
                    version="33.0.0-jre",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="com.google.guava",
                    artifact_id="guava",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert '33.0.0-jre' in catalog
        assert 'guava' in catalog

    def test_version_deduplication_reuses_existing_ref(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            dependencies=[
                Dependency(
                    group_id="com.acme",
                    artifact_id="lib-a",
                    version="2.0.0",
                ),
                Dependency(
                    group_id="com.acme",
                    artifact_id="lib-b",
                    version="2.0.0",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        # Both should share a version ref — only one version entry for "2.0.0"
        versions_section = catalog.split("[libraries]")[0]
        assert versions_section.count('"2.0.0"') == 1

    def test_dependency_no_version_no_managed(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            parent_artifact_id="spring-boot-starter-parent",
            parent_group_id="org.springframework.boot",
            parent_version="3.4.1",
            dependencies=[
                Dependency(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-starter-web",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        # No version.ref for BOM-managed dependency
        assert 'spring-boot-starter-web = { group = "org.springframework.boot", name = "spring-boot-starter-web" }' in catalog

    def test_duplicate_coordinates_deduplicated_across_modules(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            dependencies=[
                Dependency(group_id="com.google.guava", artifact_id="guava", version="33.0.0-jre"),
            ],
        )
        child = MavenModule(
            group_id="com.example",
            artifact_id="child",
            dependencies=[
                Dependency(group_id="com.google.guava", artifact_id="guava", version="33.0.0-jre"),
            ],
            source_dir="child",
        )
        catalog = build_version_catalog(root, [child])
        # guava should appear only once in libraries
        lib_section = catalog.split("[libraries]")[1]
        if "[plugins]" in lib_section:
            lib_section = lib_section.split("[plugins]")[0]
        assert lib_section.count("guava =") == 1

    def test_plugin_with_resolved_version(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            plugins=[
                Plugin(
                    group_id="com.google.cloud.tools",
                    artifact_id="jib-maven-plugin",
                    version="3.4.0",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert "[plugins]" in catalog
        assert "jib" in catalog
        assert '"3.4.0"' in catalog

    def test_plugin_with_unresolvable_version(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            plugins=[
                Plugin(
                    group_id="com.google.cloud.tools",
                    artifact_id="jib-maven-plugin",
                    version="${jib.version}",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert "[plugins]" in catalog
        # Should omit version, not crash
        assert 'id = "com.google.cloud.tools.jib"' in catalog

    def test_plugin_with_no_version(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            plugins=[
                Plugin(
                    group_id="com.google.cloud.tools",
                    artifact_id="jib-maven-plugin",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert "[plugins]" in catalog
        assert 'id = "com.google.cloud.tools.jib"' in catalog

    def test_kotlin_spring_plugin_in_catalog(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        assert 'kotlin-spring' in catalog
        assert 'org.jetbrains.kotlin.plugin.spring' in catalog

    def test_duplicate_bom_across_modules_deduplicated(self):
        bom = Dependency(
            group_id="org.springframework.cloud",
            artifact_id="spring-cloud-dependencies",
            version="2024.0.0",
            dep_type="pom",
            scope="import",
        )
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            dep_management=[bom],
        )
        child = MavenModule(
            group_id="com.example",
            artifact_id="svc",
            dep_management=[bom],
            source_dir="svc",
        )
        catalog = build_version_catalog(root, [child])
        lib_section = catalog.split("[libraries]")[1]
        if "[plugins]" in lib_section:
            lib_section = lib_section.split("[plugins]")[0]
        # Count lines that define this alias (not substring occurrences within a line)
        lib_lines = [l for l in lib_section.split("\n") if l.startswith("spring-cloud-dependencies =")]
        assert len(lib_lines) == 1

    def test_unknown_plugin_skipped_in_catalog(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            plugins=[
                Plugin(
                    group_id="com.example.custom",
                    artifact_id="custom-build-tool",
                    version="1.0.0",
                ),
            ],
        )
        catalog = build_version_catalog(module, [])
        # Unknown plugin not in PLUGIN_ID_MAP — should not appear in [plugins]
        assert "custom-build-tool" not in catalog

    def test_inter_module_dep_in_dependencies_excluded_from_catalog(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            packaging="pom",
            modules=["core", "web"],
        )
        child_web = MavenModule(
            group_id="com.example",
            artifact_id="web",
            dependencies=[
                Dependency(group_id="com.example", artifact_id="core"),
            ],
            source_dir="web",
        )
        child_core = MavenModule(
            group_id="com.example",
            artifact_id="core",
            source_dir="core",
        )
        catalog = build_version_catalog(root, [child_core, child_web])
        lib_section = catalog.split("[libraries]")[1]
        if "[plugins]" in lib_section:
            lib_section = lib_section.split("[plugins]")[0]
        # 'core' is inter-module — should not appear
        assert "core =" not in lib_section


class TestGenerateBuildGradleKts:
    def test_single_module_has_plugins_block(self, simple_module):
        build = generate_build_gradle_kts(simple_module, simple_module)
        assert "plugins {" in build
        assert "java" in build

    def test_group_and_version(self, simple_module):
        build = generate_build_gradle_kts(simple_module, simple_module)
        assert 'group = "com.example"' in build
        assert 'version = "1.0.0"' in build

    def test_java_toolchain(self, simple_module):
        build = generate_build_gradle_kts(simple_module, simple_module)
        assert "JavaLanguageVersion.of(21)" in build

    def test_repositories_on_root(self, simple_module):
        build = generate_build_gradle_kts(simple_module, simple_module, is_root=True)
        assert "mavenCentral()" in build

    def test_dependencies_block(self, simple_module):
        build = generate_build_gradle_kts(simple_module, simple_module)
        assert "dependencies {" in build
        assert "implementation(libs." in build
        assert "testImplementation(libs." in build

    def test_junit_platform(self, simple_module):
        build = generate_build_gradle_kts(simple_module, simple_module)
        assert "useJUnitPlatform()" in build

    def test_spring_boot_plugins(self, spring_boot_module):
        build = generate_build_gradle_kts(spring_boot_module, spring_boot_module)
        assert "alias(libs.plugins.spring.boot)" in build
        assert "alias(libs.plugins.spring.dependency.management)" in build

    def test_devtools_uses_development_only(self, spring_boot_module):
        build = generate_build_gradle_kts(spring_boot_module, spring_boot_module)
        assert "developmentOnly(libs." in build

    def test_multi_module_root_apply_false(self, multi_module_root):
        build = generate_build_gradle_kts(
            multi_module_root, multi_module_root,
            is_root=True, is_multi_module=True,
        )
        assert "apply false" in build

    def test_multi_module_root_allprojects(self, multi_module_root):
        build = generate_build_gradle_kts(
            multi_module_root, multi_module_root,
            is_root=True, is_multi_module=True,
        )
        assert "allprojects {" in build
        assert "subprojects {" in build

    def test_child_module_no_group_version(self, child_core_module, multi_module_root):
        build = generate_build_gradle_kts(
            child_core_module, multi_module_root,
            is_root=False, is_multi_module=True,
        )
        assert 'group = ' not in build

    def test_inter_module_dependency(self, child_web_module, multi_module_root, child_core_module):
        build = generate_build_gradle_kts(
            child_web_module, multi_module_root,
            is_root=False, is_multi_module=True,
            child_modules=[child_core_module, child_web_module],
        )
        assert 'project(":core")' in build

    def test_exclusions_in_dependency(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            dependencies=[
                Dependency(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-starter-web",
                    exclusions=[("org.springframework.boot", "spring-boot-starter-tomcat")],
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert 'exclude(group = "org.springframework.boot", module = "spring-boot-starter-tomcat")' in build

    def test_annotation_processor_handling(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21"},
            dependencies=[
                Dependency(
                    group_id="org.projectlombok",
                    artifact_id="lombok",
                    scope="provided",
                    optional=True,
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "compileOnly(" in build
        assert "annotationProcessor(" in build

    def test_optional_dependency_becomes_compile_only(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            dependencies=[
                Dependency(
                    group_id="com.google.guava",
                    artifact_id="guava",
                    optional=True,
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "compileOnly(" in build

    def test_profile_hints_as_comments(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            profiles=[
                MavenProfile(
                    profile_id="dev",
                    activation={"activeByDefault": True},
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "// Profile 'dev':" in build

    def test_kotlin_plugins_single_module(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21", "kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-stdlib",
                ),
            ],
        )
        build = generate_build_gradle_kts(root, root)
        assert "alias(libs.plugins.kotlin.jvm)" in build
        assert "alias(libs.plugins.kotlin.spring)" in build

    def test_kotlin_compiler_options(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21", "kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-stdlib",
                ),
            ],
        )
        build = generate_build_gradle_kts(root, root)
        assert "kotlin {" in build
        assert "compilerOptions {" in build
        assert 'freeCompilerArgs.addAll("-Xjsr305=strict")' in build

    def test_kotlin_annotation_processor_configurations_block(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21", "kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.projectlombok",
                    artifact_id="lombok",
                    scope="provided",
                ),
            ],
        )
        build = generate_build_gradle_kts(root, root)
        assert "configurations {" in build
        assert "extendsFrom(configurations.annotationProcessor.get())" in build

    def test_no_configurations_block_without_kotlin(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21"},
            dependencies=[
                Dependency(
                    group_id="org.projectlombok",
                    artifact_id="lombok",
                    scope="provided",
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        # Java projects don't need the extendsFrom hack
        assert "extendsFrom" not in build

    def test_bom_imports_in_dependencies_block(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            dep_management=[
                Dependency(
                    group_id="org.springframework.cloud",
                    artifact_id="spring-cloud-dependencies",
                    version="2024.0.0",
                    dep_type="pom",
                    scope="import",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.springframework.cloud",
                    artifact_id="spring-cloud-starter-gateway",
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "implementation(platform(libs." in build

    def test_test_annotation_processor(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21"},
            dependencies=[
                Dependency(
                    group_id="org.projectlombok",
                    artifact_id="lombok",
                    scope="test",
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "testCompileOnly(" in build
        assert "testAnnotationProcessor(" in build

    def test_additional_plugin_in_build(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21"},
            plugins=[
                Plugin(
                    group_id="com.google.cloud.tools",
                    artifact_id="jib-maven-plugin",
                    version="3.4.0",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-starter-web",
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "alias(libs.plugins." in build

    def test_child_module_with_kotlin_plugins(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            version="1.0.0",
            packaging="pom",
            properties={"java.version": "21", "kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
            modules=["core"],
        )
        child = MavenModule(
            group_id="com.example",
            artifact_id="core",
            source_dir="core",
            dependencies=[
                Dependency(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-stdlib",
                ),
            ],
        )
        build = generate_build_gradle_kts(
            child, root, is_root=False, is_multi_module=True,
        )
        assert "alias(libs.plugins.kotlin.jvm)" in build
        assert "alias(libs.plugins.kotlin.spring)" in build

    def test_child_module_with_additional_plugins(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            version="1.0.0",
            packaging="pom",
            properties={"java.version": "21"},
            modules=["svc"],
        )
        child = MavenModule(
            group_id="com.example",
            artifact_id="svc",
            source_dir="svc",
            plugins=[
                Plugin(
                    group_id="com.google.cloud.tools",
                    artifact_id="jib-maven-plugin",
                    version="3.4.0",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-starter-web",
                ),
            ],
        )
        build = generate_build_gradle_kts(
            child, root, is_root=False, is_multi_module=True,
        )
        assert "alias(libs.plugins." in build

    def test_multi_module_root_with_kotlin_apply_false(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            version="1.0.0",
            packaging="pom",
            properties={"java.version": "21", "kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
                Plugin(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-maven-plugin",
                ),
            ],
            parent_artifact_id="spring-boot-starter-parent",
            parent_group_id="org.springframework.boot",
            parent_version="3.4.1",
            modules=["core"],
        )
        build = generate_build_gradle_kts(
            root, root, is_root=True, is_multi_module=True,
        )
        assert "alias(libs.plugins.kotlin.jvm) apply false" in build
        assert "alias(libs.plugins.kotlin.spring) apply false" in build

    def test_no_java_toolchain_for_multi_module_root_pom(self, multi_module_root):
        build = generate_build_gradle_kts(
            multi_module_root, multi_module_root,
            is_root=True, is_multi_module=True,
        )
        assert "JavaLanguageVersion" not in build

    def test_no_kotlin_options_for_multi_module_root_pom(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            version="1.0.0",
            packaging="pom",
            properties={"kotlin.version": "2.0.0"},
            plugins=[
                Plugin(
                    group_id="org.jetbrains.kotlin",
                    artifact_id="kotlin-maven-plugin",
                    version="${kotlin.version}",
                ),
            ],
            modules=["core"],
        )
        build = generate_build_gradle_kts(
            root, root, is_root=True, is_multi_module=True,
        )
        assert "kotlin {" not in build
        assert "compilerOptions" not in build

    def test_no_dependencies_block_for_multi_module_root_pom(self, multi_module_root):
        build = generate_build_gradle_kts(
            multi_module_root, multi_module_root,
            is_root=True, is_multi_module=True,
        )
        assert "dependencies {" not in build

    def test_no_repositories_for_non_root(self, child_core_module, multi_module_root):
        build = generate_build_gradle_kts(
            child_core_module, multi_module_root,
            is_root=False, is_multi_module=True,
        )
        assert "repositories {" not in build

    def test_profile_property_activation_hint(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            profiles=[
                MavenProfile(
                    profile_id="ci",
                    activation={"property": {"name": "ci", "value": "true"}},
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "// Profile 'ci':" in build
        assert '-Pci=true' in build
        assert 'hasProperty("ci")' in build

    def test_profile_jdk_activation_hint(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            profiles=[
                MavenProfile(
                    profile_id="jdk21",
                    activation={"jdk": "21"},
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "JDK activation: 21" in build

    def test_profile_with_dependencies_count(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            profiles=[
                MavenProfile(
                    profile_id="extras",
                    dependencies=[
                        Dependency(group_id="com.acme", artifact_id="extra-lib"),
                        Dependency(group_id="com.acme", artifact_id="extra-lib2"),
                    ],
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "Has 2 dependencies" in build

    def test_profile_with_plugins_count(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            profiles=[
                MavenProfile(
                    profile_id="release",
                    plugins=[
                        Plugin(group_id="org.apache.maven.plugins", artifact_id="maven-gpg-plugin"),
                    ],
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "Has 1 plugins" in build

    def test_no_version_in_output_when_module_has_no_version(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
        )
        build = generate_build_gradle_kts(module, module)
        assert 'group = "com.example"' in build
        assert 'version =' not in build

    def test_child_module_skips_spring_boot_plugin(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            version="1.0.0",
            packaging="pom",
            properties={"java.version": "21"},
            parent_artifact_id="spring-boot-starter-parent",
            parent_group_id="org.springframework.boot",
            parent_version="3.4.1",
            modules=["svc"],
            plugins=[
                Plugin(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-maven-plugin",
                ),
            ],
        )
        child = MavenModule(
            group_id="com.example",
            artifact_id="svc",
            source_dir="svc",
            plugins=[
                Plugin(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-maven-plugin",
                ),
                Plugin(
                    group_id="com.google.cloud.tools",
                    artifact_id="jib-maven-plugin",
                    version="3.4.0",
                ),
            ],
            dependencies=[
                Dependency(
                    group_id="org.springframework.boot",
                    artifact_id="spring-boot-starter-web",
                ),
            ],
        )
        build = generate_build_gradle_kts(
            child, root, is_root=False, is_multi_module=True,
        )
        # spring-boot-maven-plugin should be skipped (handled via alias)
        # jib should appear
        lines = build.split("\n")
        plugin_lines = [l.strip() for l in lines if "alias(libs.plugins." in l]
        assert any("jib" in l or "google.cloud" in l for l in plugin_lines)

    def test_annotation_processor_without_provided_scope(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            version="1.0.0",
            properties={"java.version": "21"},
            dependencies=[
                Dependency(
                    group_id="org.mapstruct",
                    artifact_id="mapstruct-processor",
                ),
            ],
        )
        build = generate_build_gradle_kts(module, module)
        assert "annotationProcessor(" in build
        # Not provided/optional → no compileOnly
        assert "compileOnly(" not in build


class TestGenerateSettingsGradleKts:
    def test_root_project_name(self, simple_module):
        settings = generate_settings_gradle_kts(simple_module)
        assert 'rootProject.name = "demo"' in settings

    def test_multi_module_includes(self, multi_module_root, child_core_module, child_web_module):
        settings = generate_settings_gradle_kts(
            multi_module_root, [child_core_module, child_web_module]
        )
        assert 'include("core")' in settings
        assert 'include("web")' in settings

    def test_custom_repository(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            repositories=[("spring-milestones", "https://repo.spring.io/milestone")],
        )
        settings = generate_settings_gradle_kts(module)
        assert "https://repo.spring.io/milestone" in settings
        assert "pluginManagement" in settings

    def test_no_plugin_management_without_custom_repos_or_modules(self, simple_module):
        settings = generate_settings_gradle_kts(simple_module)
        assert "pluginManagement" not in settings

    def test_dependency_resolution_management_with_custom_repos(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            repositories=[("spring-milestones", "https://repo.spring.io/milestone")],
        )
        settings = generate_settings_gradle_kts(module)
        assert "dependencyResolutionManagement {" in settings
        assert settings.count("https://repo.spring.io/milestone") == 2  # in both blocks

    def test_repository_deduplication_by_url(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            repositories=[
                ("spring-milestones", "https://repo.spring.io/milestone"),
                ("spring-milestones-2", "https://repo.spring.io/milestone/"),
            ],
        )
        settings = generate_settings_gradle_kts(module)
        # Trailing slash normalized — should only appear once per block
        plugin_mgmt = settings.split("pluginManagement")[1].split("}")[0]
        assert plugin_mgmt.count("repo.spring.io/milestone") == 1

    def test_central_repo_excluded_from_custom_repos(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            repositories=[
                ("central", "https://repo1.maven.org/maven2"),
                ("spring-milestones", "https://repo.spring.io/milestone"),
            ],
        )
        settings = generate_settings_gradle_kts(module)
        assert "repo1.maven.org" not in settings
        assert "repo.spring.io/milestone" in settings

    def test_plugin_management_for_multi_module_without_custom_repos(self, multi_module_root):
        settings = generate_settings_gradle_kts(multi_module_root)
        assert "pluginManagement {" in settings
        # No dependencyResolutionManagement without custom repos
        assert "dependencyResolutionManagement" not in settings

    def test_child_module_repos_aggregated(self):
        root = MavenModule(
            group_id="com.example",
            artifact_id="parent",
            modules=["svc"],
        )
        child = MavenModule(
            group_id="com.example",
            artifact_id="svc",
            source_dir="svc",
            repositories=[("jitpack", "https://jitpack.io")],
        )
        settings = generate_settings_gradle_kts(root, [child])
        assert "https://jitpack.io" in settings

    def test_nested_module_path_conversion(self):
        child = MavenModule(
            group_id="com.example",
            artifact_id="nested-child",
            source_dir="parent/child",
        )
        root = MavenModule(
            group_id="com.example",
            artifact_id="app",
            modules=["parent"],
        )
        settings = generate_settings_gradle_kts(root, [child])
        assert 'include("parent:child")' in settings


class TestGenerateGradleProperties:
    def test_standard_entries(self, simple_module):
        props = generate_gradle_properties(simple_module)
        assert "org.gradle.daemon=true" in props
        assert "org.gradle.parallel=true" in props
        assert "org.gradle.caching=true" in props

    def test_source_encoding_comment(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"project.build.sourceEncoding": "UTF-8"},
        )
        props = generate_gradle_properties(module)
        assert "Source encoding: UTF-8" in props

    def test_custom_properties_as_comments(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"my.custom.prop": "value123"},
        )
        props = generate_gradle_properties(module)
        assert "my_custom_prop=value123" in props

    def test_maven_prefixed_properties_excluded(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"maven.compiler.source": "21"},
        )
        props = generate_gradle_properties(module)
        assert "maven" not in props.split("# Generated")[1]

    def test_reporting_output_encoding_excluded(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"project.reporting.outputEncoding": "UTF-8"},
        )
        props = generate_gradle_properties(module)
        assert "reporting" not in props

    def test_java_prefixed_properties_excluded(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"java.version": "21"},
        )
        props = generate_gradle_properties(module)
        body = props.split("# Generated")[1]
        assert "java" not in body.lower()

    def test_kotlin_prefixed_properties_excluded(self):
        module = MavenModule(
            group_id="com.example",
            artifact_id="demo",
            properties={"kotlin.version": "2.0.0"},
        )
        props = generate_gradle_properties(module)
        body = props.split("# Generated")[1]
        assert "kotlin" not in body.lower()

    def test_configuration_cache_comment(self, simple_module):
        props = generate_gradle_properties(simple_module)
        assert "configuration-cache" in props


class TestGenerateGitignoreEntries:
    def test_contains_gradle_dir(self):
        content = generate_gradle_gitignore_entries()
        assert ".gradle/" in content

    def test_contains_build_dir(self):
        content = generate_gradle_gitignore_entries()
        assert "build/" in content

    def test_wrapper_exception(self):
        content = generate_gradle_gitignore_entries()
        assert "!gradle/wrapper/gradle-wrapper.jar" in content

    def test_src_main_build_exception(self):
        content = generate_gradle_gitignore_entries()
        assert "!**/src/main/**/build/" in content

    def test_src_test_build_exception(self):
        content = generate_gradle_gitignore_entries()
        assert "!**/src/test/**/build/" in content


class TestIsInterModuleDep:
    def test_child_module_is_inter_module(self):
        root = MavenModule(group_id="com.example", artifact_id="parent")
        child = MavenModule(
            group_id="com.example", artifact_id="core", source_dir="core",
        )
        dep = Dependency(group_id="com.example", artifact_id="core")
        result = _is_inter_module_dep(dep, root, [child])
        assert result == "core"

    def test_root_module_is_inter_module(self):
        root = MavenModule(group_id="com.example", artifact_id="parent")
        dep = Dependency(group_id="com.example", artifact_id="parent")
        result = _is_inter_module_dep(dep, root, [])
        assert result == "."

    def test_external_dep_returns_none(self):
        root = MavenModule(group_id="com.example", artifact_id="parent")
        dep = Dependency(group_id="org.springframework", artifact_id="spring-core")
        result = _is_inter_module_dep(dep, root, [])
        assert result is None

    def test_matching_artifact_different_group_returns_none(self):
        root = MavenModule(group_id="com.example", artifact_id="parent")
        child = MavenModule(
            group_id="com.example", artifact_id="core", source_dir="core",
        )
        dep = Dependency(group_id="com.other", artifact_id="core")
        result = _is_inter_module_dep(dep, root, [child])
        assert result is None
