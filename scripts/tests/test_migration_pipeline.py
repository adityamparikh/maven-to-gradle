"""Tests for migration_pipeline.py — end-to-end migration orchestration."""

import textwrap
from pathlib import Path

from migrate.migration_pipeline import migrate, _parse_modules_recursive, parse_args


class TestParseModulesRecursive:
    def test_single_child(self, tmp_path):
        # Root POM
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>parent</artifactId>
                <packaging>pom</packaging>
                <modules><module>child</module></modules>
            </project>
        """))
        # Child POM
        child_dir = tmp_path / "child"
        child_dir.mkdir()
        (child_dir / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>child</artifactId>
            </project>
        """))
        modules = _parse_modules_recursive(tmp_path, ["child"])
        assert len(modules) == 1
        assert modules[0].artifact_id == "child"
        assert modules[0].source_dir == "child"

    def test_nested_modules(self, tmp_path):
        # Root
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>root</artifactId>
                <packaging>pom</packaging>
                <modules><module>mid</module></modules>
            </project>
        """))
        # Mid-level module
        mid = tmp_path / "mid"
        mid.mkdir()
        (mid / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>mid</artifactId>
                <packaging>pom</packaging>
                <modules><module>leaf</module></modules>
            </project>
        """))
        # Leaf module
        leaf = mid / "leaf"
        leaf.mkdir()
        (leaf / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>leaf</artifactId>
            </project>
        """))
        modules = _parse_modules_recursive(tmp_path, ["mid"])
        assert len(modules) == 2
        assert modules[0].artifact_id == "mid"
        assert modules[0].source_dir == "mid"
        assert modules[1].artifact_id == "leaf"
        assert modules[1].source_dir == "mid/leaf"

    def test_missing_child_pom_skipped(self, tmp_path, capsys):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>root</artifactId>
            </project>
        """))
        modules = _parse_modules_recursive(tmp_path, ["nonexistent"])
        assert len(modules) == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err


class TestMigrateDryRun:
    def test_dry_run_prints_output(self, tmp_path, capsys):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
                <properties>
                    <java.version>21</java.version>
                </properties>
                <dependencies>
                    <dependency>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot-starter-web</artifactId>
                    </dependency>
                </dependencies>
            </project>
        """))
        migrate(tmp_path, dry_run=True)
        output = capsys.readouterr().out
        assert "libs.versions.toml" in output
        assert "settings.gradle.kts" in output
        assert "build.gradle.kts" in output
        assert "gradle.properties" in output

    def test_dry_run_does_not_create_files(self, tmp_path):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        migrate(tmp_path, dry_run=True)
        assert not (tmp_path / "build.gradle.kts").exists()
        assert not (tmp_path / "settings.gradle.kts").exists()


class TestMigrateWriteMode:
    def test_creates_all_files(self, tmp_path):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        migrate(tmp_path)
        assert (tmp_path / "build.gradle.kts").exists()
        assert (tmp_path / "settings.gradle.kts").exists()
        assert (tmp_path / "gradle.properties").exists()
        assert (tmp_path / "gradle" / "libs.versions.toml").exists()

    def test_output_to_separate_directory(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        out = tmp_path / "output"
        out.mkdir()
        migrate(src, output_path=out)
        assert (out / "build.gradle.kts").exists()
        assert not (src / "build.gradle.kts").exists()

    def test_multi_module_creates_child_builds(self, tmp_path):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>parent</artifactId>
                <version>1.0.0</version>
                <packaging>pom</packaging>
                <modules>
                    <module>core</module>
                    <module>web</module>
                </modules>
            </project>
        """))
        for mod in ["core", "web"]:
            d = tmp_path / mod
            d.mkdir()
            (d / "pom.xml").write_text(textwrap.dedent(f"""\
                <?xml version="1.0" encoding="UTF-8"?>
                <project>
                    <groupId>com.example</groupId>
                    <artifactId>{mod}</artifactId>
                    <version>1.0.0</version>
                </project>
            """))
        migrate(tmp_path)
        assert (tmp_path / "core" / "build.gradle.kts").exists()
        assert (tmp_path / "web" / "build.gradle.kts").exists()
        settings = (tmp_path / "settings.gradle.kts").read_text()
        assert 'include("core")' in settings
        assert 'include("web")' in settings


class TestMigrateOverlayMode:
    def test_overlay_creates_gitignore(self, tmp_path):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        migrate(tmp_path, mode="overlay")
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert ".gradle/" in gitignore.read_text()

    def test_overlay_appends_to_existing_gitignore(self, tmp_path):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("target/\n")
        migrate(tmp_path, mode="overlay")
        content = gitignore.read_text()
        assert "target/" in content
        assert ".gradle/" in content

    def test_overlay_does_not_duplicate_gitignore_entries(self, tmp_path):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("target/\n.gradle/\nbuild/\n")
        migrate(tmp_path, mode="overlay")
        content = gitignore.read_text()
        # Should not append since .gradle/ already exists
        assert content.count(".gradle/") == 1


class TestMigrateDryRunMultiModule:
    def test_dry_run_prints_child_builds(self, tmp_path, capsys):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>parent</artifactId>
                <version>1.0.0</version>
                <packaging>pom</packaging>
                <modules>
                    <module>core</module>
                </modules>
            </project>
        """))
        core = tmp_path / "core"
        core.mkdir()
        (core / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>core</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        migrate(tmp_path, dry_run=True)
        output = capsys.readouterr().out
        assert "core/build.gradle.kts" in output

    def test_dry_run_overlay_prints_gitignore(self, tmp_path, capsys):
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        migrate(tmp_path, dry_run=True, mode="overlay")
        output = capsys.readouterr().out
        assert ".gitignore (append)" in output
        assert ".gradle/" in output


class TestParseModulesCircularReference:
    def test_circular_module_reference_handled(self, tmp_path):
        # Module 'a' references 'b' and 'b' references 'a' — a true cycle
        a_dir = tmp_path / "a"
        b_dir = tmp_path / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        (a_dir / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>a</artifactId>
                <packaging>pom</packaging>
                <modules><module>../b</module></modules>
            </project>
        """))
        (b_dir / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>b</artifactId>
                <packaging>pom</packaging>
                <modules><module>../a</module></modules>
            </project>
        """))
        # Start from 'a' — should parse a then b, but stop when b tries to revisit a
        modules = _parse_modules_recursive(tmp_path, ["a"])
        artifact_ids = [m.artifact_id for m in modules]
        assert "a" in artifact_ids
        assert "b" in artifact_ids
        # Circular guard prevents infinite recursion — exactly 2 modules
        assert len(modules) == 2


class TestParseArgs:
    def test_defaults(self, tmp_path):
        args = parse_args([str(tmp_path)])
        assert args.project == tmp_path
        assert args.output is None
        assert args.dry_run is False
        assert args.mode == "migrate"

    def test_dry_run_flag(self, tmp_path):
        args = parse_args([str(tmp_path), "--dry-run"])
        assert args.dry_run is True

    def test_short_flags(self, tmp_path):
        args = parse_args([str(tmp_path), "-n", "-m", "overlay", "-o", str(tmp_path / "out")])
        assert args.dry_run is True
        assert args.mode == "overlay"
        assert args.output == tmp_path / "out"

    def test_overlay_mode(self, tmp_path):
        args = parse_args([str(tmp_path), "--mode", "overlay"])
        assert args.mode == "overlay"

    def test_invalid_mode_exits(self):
        import pytest
        with pytest.raises(SystemExit):
            parse_args(["/some/path", "--mode", "invalid"])


class TestMainEntryPoint:
    def test_main_delegates_to_migrate(self, tmp_path, monkeypatch):
        from migrate import migration_pipeline
        (tmp_path / "pom.xml").write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project>
                <groupId>com.example</groupId>
                <artifactId>demo</artifactId>
                <version>1.0.0</version>
            </project>
        """))
        monkeypatch.setattr(
            "sys.argv", ["migrate", str(tmp_path), "--dry-run"]
        )
        migration_pipeline.main()
        # If it didn't error out, main() correctly parsed args and ran


class TestMigrateErrorHandling:
    def test_missing_pom_exits(self, tmp_path):
        import pytest
        with pytest.raises(SystemExit):
            migrate(tmp_path)
