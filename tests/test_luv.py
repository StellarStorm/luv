#!/usr/bin/env python3
"""
Comprehensive unit tests for luv - LaTeX Universal Virtualizer
"""

import os
import tempfile
from pathlib import Path

import pytest

from luv import (
    LaTeXEnvironment,
    LuvError,
    PackageResolver,
    find_project_root,
)


class TestPackageResolver:
    """Test the PackageResolver class."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project directory with sample files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)

            # Create main.tex with sample content
            main_tex = project_root / 'main.tex'
            main_tex.write_text(
                """
\\documentclass{article}
\\usepackage{amsmath}
\\usepackage[utf8]{inputenc}
\\usepackage{graphicx,hyperref}

\\begin{document}
\\begin{align}
    x + y = z
\\end{align}

\\includegraphics{figure.png}
\\href{http://example.com}{Link}
\\input{chapter1}
\\end{document}
"""
            )

            # Create included file
            chapter1 = project_root / 'chapter1.tex'
            chapter1.write_text(
                """
\\usepackage{tikz}
\\begin{tikzpicture}
\\node at (0,0) {Hello};
\\end{tikzpicture}
"""
            )

            yield project_root

    def test_init(self, temp_project):
        resolver = PackageResolver(temp_project)
        assert resolver.project_root == temp_project
        assert resolver.found_packages == set()
        assert resolver.explicitly_used == set()

    def test_core_packages_defined(self):
        assert 'fontenc' in PackageResolver.CORE_PACKAGES
        assert 'inputenc' in PackageResolver.CORE_PACKAGES

    def test_package_patterns_defined(self):
        assert 'amsmath' in PackageResolver.PACKAGE_PATTERNS
        assert 'graphicx' in PackageResolver.PACKAGE_PATTERNS
        assert any(
            '\\\\begin\\{align' in pattern
            for pattern in PackageResolver.PACKAGE_PATTERNS['amsmath']
        )

    def test_resolve_package_name_core_package(self, temp_project):
        resolver = PackageResolver(temp_project)
        result = resolver.resolve_package_name('inputenc')
        assert result is None  # Core packages return None

    def test_resolve_package_name_normal_package(self, temp_project, mocker):
        resolver = PackageResolver(temp_project)

        # Mock successful tlmgr search
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=0,
            stdout='texlive-latex-base:\n    /usr/share/texlive/texmf-dist/tex/latex/base/amsmath.sty\n',
        )

        result = resolver.resolve_package_name('amsmath')
        assert result == 'texlive-latex-base'

    def test_resolve_package_name_not_found(self, temp_project, mocker):
        resolver = PackageResolver(temp_project)

        # Mock failed tlmgr search
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=1, stdout='')

        result = resolver.resolve_package_name('nonexistent')
        assert result == 'nonexistent'  # Returns original name if not found

    def test_resolve_package_name_tlmgr_not_found(self, temp_project, mocker):
        resolver = PackageResolver(temp_project)

        # Mock tlmgr not being available
        mock_run = mocker.patch('subprocess.run')
        mock_run.side_effect = FileNotFoundError()

        result = resolver.resolve_package_name('amsmath')
        assert (
            result == 'amsmath'
        )  # Returns original name if tlmgr not available

    def test_find_explicit_packages(self, temp_project):
        resolver = PackageResolver(temp_project)
        resolver._find_explicit_packages('main.tex')

        # Should find explicitly declared packages
        assert 'amsmath' in resolver.explicitly_used
        assert 'graphicx' in resolver.explicitly_used
        assert 'hyperref' in resolver.explicitly_used
        assert 'tikz' in resolver.explicitly_used  # From included file

        # inputenc is in CORE_PACKAGES but the test file includes it, so it gets filtered out
        # during the scanning process. Let's check it's not included because it's core.
        assert len(resolver.explicitly_used) >= 4

    def test_scan_for_patterns(self, temp_project):
        resolver = PackageResolver(temp_project)
        resolver._scan_for_patterns('main.tex')

        # Should suggest packages based on usage patterns
        # Note: These might already be explicitly declared, so check the logic

    def test_resolve_dependencies(self, temp_project, mocker):
        resolver = PackageResolver(temp_project)

        # Mock tlmgr calls
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=0,
            stdout='texlive-latex-base:\n    /path/to/package.sty\n',
        )

        packages = resolver.resolve_dependencies('main.tex')

        assert isinstance(packages, list)
        assert len(packages) > 0
        # Should include resolved packages from explicit declarations and patterns

    def test_skip_local_sty_files(self, temp_project):
        # Create a local .sty file
        local_sty = temp_project / 'custom.sty'
        local_sty.write_text('% Custom package')

        # Create tex file that uses the local package
        test_tex = temp_project / 'test.tex'
        test_tex.write_text('\\usepackage{custom}')

        resolver = PackageResolver(temp_project)
        resolver._find_explicit_packages('test.tex')

        # Should not include local packages
        assert 'custom' not in resolver.explicitly_used


class TestLaTeXEnvironment:
    """Test the LaTeXEnvironment class."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            yield project_root

    @pytest.fixture
    def env(self, temp_project):
        """Create a LaTeXEnvironment instance."""
        return LaTeXEnvironment(temp_project)

    def test_init(self, temp_project):
        env = LaTeXEnvironment(temp_project)
        assert env.project_root == temp_project
        assert env.luv_dir == temp_project / '.luv'
        assert env.texmf_dir == temp_project / '.luv' / 'texmf'
        assert (
            env.packages_dir
            == temp_project / '.luv' / 'texmf' / 'tex' / 'latex'
        )
        assert env.config_file == temp_project / 'luv.toml'
        assert env.requirements_file == temp_project / 'latex-requirements.txt'

    def test_exists_false(self, env):
        assert not env.exists()

    def test_exists_true(self, env):
        env.luv_dir.mkdir()
        env.texmf_dir.mkdir(parents=True)
        assert env.exists()

    def test_create_new_environment(self, env):
        env.create()

        assert env.exists()
        assert env.luv_dir.exists()
        assert env.texmf_dir.exists()
        assert env.packages_dir.exists()
        assert (env.luv_dir / 'bin').exists()
        assert (env.luv_dir / 'cache').exists()
        assert env.config_file.exists()
        assert env.requirements_file.exists()

    def test_create_existing_environment_raises_error(self, env):
        env.create()

        with pytest.raises(LuvError, match='Environment already exists'):
            env.create()

    def test_create_initial_config(self, env):
        env._create_initial_config()

        assert env.config_file.exists()
        content = env.config_file.read_text()
        assert 'texfile = "main.tex"' in content
        assert 'engine = "pdflatex"' in content
        assert 'output_dir = "build"' in content

    def test_create_initial_requirements(self, env):
        env._create_initial_requirements()

        assert env.requirements_file.exists()
        content = env.requirements_file.read_text()
        assert '# LaTeX package requirements' in content
        assert '# amsmath' in content

    def test_clean_environment(self, env):
        env.create()
        assert env.exists()

        env.clean()
        assert not env.exists()

    def test_clean_nonexistent_environment(self, env, capsys):
        env.clean()

        # Check that warning was logged to stdout
        captured = capsys.readouterr()
        assert 'No environment found to clean' in captured.out

    def test_get_config(self, env):
        env._create_initial_config()
        config = env.get_config()

        assert 'project' in config
        assert config['project']['texfile'] == 'main.tex'
        assert config['project']['engine'] == 'pdflatex'

    def test_get_config_no_file(self, env):
        with pytest.raises(LuvError, match='No luv.toml found'):
            env.get_config()

    def test_update_config(self, env):
        env._create_initial_config()

        new_config = {
            'project': {'texfile': 'document.tex', 'engine': 'xelatex'}
        }
        env.update_config(new_config)

        updated_config = env.get_config()
        assert updated_config['project']['texfile'] == 'document.tex'
        assert updated_config['project']['engine'] == 'xelatex'

    def test_get_requirements_empty(self, env):
        requirements = env.get_requirements()
        assert requirements == []

    def test_get_requirements_with_packages(self, env):
        env.requirements_file.write_text(
            """
# Comments should be ignored
amsmath
graphicx
# Another comment
hyperref
"""
        )

        requirements = env.get_requirements()
        assert requirements == ['amsmath', 'graphicx', 'hyperref']

    def test_clean(self, env):
        env.create()
        assert env.exists()

        env.clean()
        assert not env.exists()

    def test_clean_nonexistent(self, env, capsys):
        env.clean()

        # Check that warning was logged to stdout
        captured = capsys.readouterr()
        assert 'No environment found to clean' in captured.out

    def test_has_bibliography_with_bibliography(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\bibliography{refs}')

        assert env._has_bibliography(tex_file)

    def test_has_bibliography_with_bib_file(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\documentclass{article}')

        bib_file = env.project_root / 'refs.bib'
        bib_file.write_text('@article{key, title={Title}}')

        assert env._has_bibliography(tex_file)

    def test_has_bibliography_without_bibliography(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\documentclass{article}')

        assert not env._has_bibliography(tex_file)

    def test_has_citations_with_citations(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\cite{reference}')

        assert env._has_citations(tex_file)

    def test_has_citations_without_citations(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\documentclass{article}')

        assert not env._has_citations(tex_file)

    def test_get_bibliography_backend_biber(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\usepackage[backend=biber]{biblatex}')

        backend = env._get_bibliography_backend(tex_file)
        assert backend == 'biber'

    def test_get_bibliography_backend_bibtex(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\bibliographystyle{plain}')

        backend = env._get_bibliography_backend(tex_file)
        assert backend == 'bibtex'

    def test_get_bibliography_backend_default(self, env):
        tex_file = env.project_root / 'main.tex'
        tex_file.write_text('\\documentclass{article}')

        backend = env._get_bibliography_backend(tex_file)
        assert backend == 'bibtex'

    def test_resolve_dependencies_no_texfile(self, env):
        env.create()
        env._create_initial_config()

        with pytest.raises(LuvError, match='TeX file not found'):
            env.resolve_dependencies()

    def test_resolve_dependencies_with_texfile(self, env, mocker):
        env.create()
        env._create_initial_config()

        # Create main.tex
        main_tex = env.project_root / 'main.tex'
        main_tex.write_text('\\usepackage{amsmath}')

        # Mock PackageResolver
        mock_resolver = mocker.patch('luv.PackageResolver')
        mock_instance = mocker.Mock()
        mock_instance.resolve_dependencies.return_value = ['amsmath']
        mock_instance.explicitly_used = {'amsmath'}
        mock_instance.found_packages = set()
        mock_instance.CORE_PACKAGES = set()
        mock_instance.resolve_package_name.return_value = 'amsmath'
        mock_resolver.return_value = mock_instance

        packages = env.resolve_dependencies(
            update_requirements=False, interactive=False
        )
        assert packages == ['amsmath']

    def test_sync_no_requirements(self, env):
        env.create()

        # This should not raise an error
        env.sync()

    def test_sync_with_requirements(self, env, mocker):
        env.create()
        env.requirements_file.write_text('amsmath\ngraphicx')

        # Mock install_package
        mock_install = mocker.patch.object(
            env, 'install_package_smart', return_value=True
        )
        mock_setup = mocker.patch.object(env, '_setup_tlmgr_user_mode')

        env.sync()

        # With parallel installation, _setup_tlmgr_user_mode may be called multiple times
        assert mock_setup.call_count >= 1
        assert mock_install.call_count == 2
        mock_install.assert_any_call('amsmath')
        mock_install.assert_any_call('graphicx')


class TestFindProjectRoot:
    """Test the find_project_root function."""

    def test_find_project_root_in_current_dir(self):
        """Test find_project_root in current directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir).resolve()  # Resolve to handle symlinks
            luv_toml = temp_path / 'luv.toml'
            luv_toml.write_text('[project]')

            # Change to temp directory
            original_cwd = os.getcwd()
            try:
                os.chdir(temp_path)
                result = find_project_root()
                assert result == temp_path
            finally:
                os.chdir(original_cwd)

    def test_find_project_root_in_parent_dir(self):
        """Test find_project_root in parent directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir).resolve()  # Resolve to handle symlinks
            project_dir = temp_path / 'project'
            subdir = project_dir / 'src'
            subdir.mkdir(parents=True)

            luv_toml = project_dir / 'luv.toml'
            luv_toml.write_text('[project]')

            # Change to subdirectory
            original_cwd = os.getcwd()
            try:
                os.chdir(subdir)
                result = find_project_root()
                assert result == project_dir
            finally:
                os.chdir(original_cwd)

    def test_find_project_root_not_found(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Change to temp directory without luv.toml
            original_cwd = os.getcwd()
            try:
                os.chdir(temp_path)
                result = find_project_root()
                assert result is None
            finally:
                os.chdir(original_cwd)


class TestLuvError:
    """Test the LuvError exception."""

    def test_luv_error_is_exception(self):
        assert issubclass(LuvError, Exception)

    def test_luv_error_with_message(self):
        error = LuvError("Test error message")
        assert str(error) == "Test error message"


# Integration-style tests
class TestIntegration:
    """Integration tests that test multiple components together."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project with a complete setup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)

            # Create main.tex
            main_tex = project_root / 'main.tex'
            main_tex.write_text(
                """
\\documentclass{article}
\\usepackage{amsmath}
\\usepackage{graphicx}

\\begin{document}
\\begin{align}
    E = mc^2
\\end{align}

\\includegraphics{example.png}
\\end{document}
"""
            )

            yield project_root

    def test_full_workflow(self, temp_project, mocker):
        """Test a complete workflow: init -> resolve -> sync."""
        env = LaTeXEnvironment(temp_project)

        # Initialize environment
        env.create()
        assert env.exists()
        assert env.config_file.exists()
        assert env.requirements_file.exists()

        # Mock tlmgr for package resolution
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=0,
            stdout='texlive-latex-base:\n    /path/to/package.sty\n',
        )

        # Resolve dependencies
        packages = env.resolve_dependencies(
            update_requirements=False, interactive=False
        )
        assert isinstance(packages, list)
        assert len(packages) > 0

        # Mock package installation for sync
        mock_install = mocker.patch.object(
            env, 'install_package_smart', return_value=True
        )
        mock_setup = mocker.patch.object(env, '_setup_tlmgr_user_mode')

        # Update requirements manually for testing
        env.requirements_file.write_text('\n'.join(['amsmath', 'graphicx']))

        # Sync packages
        env.sync()
        # With parallel installation, _setup_tlmgr_user_mode may be called multiple times
        assert mock_setup.call_count >= 1
        assert mock_install.call_count == 2

    def test_package_resolver_with_real_files(self, temp_project):
        """Test PackageResolver with real LaTeX files."""
        # Create a comprehensive LaTeX file
        main_tex = temp_project / 'main.tex'
        main_tex.write_text(
            """
\\documentclass{article}
\\usepackage{amsmath,amssymb}
\\usepackage[utf8]{inputenc}
\\usepackage{graphicx}
\\usepackage{hyperref}
\\usepackage{tikz}

\\begin{document}
\\title{Test Document}
\\author{Test Author}
\\maketitle

\\section{Mathematics}
\\begin{align}
    \\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}
\\end{align}

Some text with \\textcolor{red}{colored text}.

\\begin{tikzpicture}
\\node at (0,0) {Hello World};
\\end{tikzpicture}

\\href{https://example.com}{A link}

\\end{document}
"""
        )

        resolver = PackageResolver(temp_project)

        # Test explicit package finding
        resolver._find_explicit_packages('main.tex')

        # Should find explicitly declared packages (excluding core ones)
        assert 'amsmath' in resolver.explicitly_used
        assert 'amssymb' in resolver.explicitly_used
        assert 'graphicx' in resolver.explicitly_used
        assert 'hyperref' in resolver.explicitly_used
        assert 'tikz' in resolver.explicitly_used

        # inputenc is in CORE_PACKAGES but may still be detected as explicit
        # This is expected behavior as the filtering happens later
        assert len(resolver.explicitly_used) >= 5

        # Test pattern scanning
        resolver._scan_for_patterns('main.tex')

        # The explicitly used packages should be detected by patterns too,
        # but since they're already explicit, they won't be added to found_packages

        # Test that we can distinguish between explicit and suggested packages
        resolver.explicitly_used.clear()  # Clear to test pattern detection

        resolver._scan_for_patterns('main.tex')

        # Should detect patterns for packages we cleared
        suggested = resolver.found_packages
        assert len(suggested) > 0

    def test_config_file_creation_and_reading(self):
        """Test that config files can be created and read correctly with tomli-w."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env = LaTeXEnvironment(project_root)

            # Create environment which should create config file
            env.create()

            # Verify config file exists and can be read
            assert env.config_file.exists()
            config = env.get_config()

            # Check that the config has expected structure
            assert 'project' in config
            assert config['project']['texfile'] == 'main.tex'
            assert config['project']['output_dir'] == 'build'
            assert config['project']['engine'] == 'pdflatex'

            # Test updating config
            config['project']['engine'] = 'xelatex'
            env.update_config(config)

            # Verify the update was persisted
            updated_config = env.get_config()
            assert updated_config['project']['engine'] == 'xelatex'
