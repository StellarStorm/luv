#!/usr/bin/env python3
"""
Tests for LaTeX compilation and package installation functionality.
"""

import tempfile
from pathlib import Path

import pytest

from luv import LaTeXEnvironment, LuvError


class TestCompilation:
    """Test LaTeX compilation functionality."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project with LaTeX files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)

            # Create main.tex
            main_tex = project_root / 'main.tex'
            main_tex.write_text(
                """
\\documentclass{article}
\\usepackage{amsmath}

\\begin{document}
\\title{Test Document}
\\maketitle

\\begin{align}
    E = mc^2
\\end{align}

\\end{document}
"""
            )

            # Create a bib file for bibliography tests
            bib_file = project_root / 'refs.bib'
            bib_file.write_text(
                """
@article{einstein1905,
    title={Zur Elektrodynamik bewegter K{\"o}rper},
    author={Einstein, Albert},
    journal={Annalen der physik},
    volume={17},
    number={10},
    pages={891--921},
    year={1905}
}
"""
            )

            yield project_root

    @pytest.fixture
    def env(self, temp_project):
        """Create and initialize a LaTeXEnvironment."""
        env = LaTeXEnvironment(temp_project)
        env.create()
        return env

    def test_compile_no_environment(self, temp_project, capsys):
        """Test compilation logs error when no environment exists."""
        env = LaTeXEnvironment(temp_project)

        env.compile()

        # Check that error was logged to stdout
        captured = capsys.readouterr()
        assert 'No environment found' in captured.out

    def test_compile_no_texfile(self, env):
        """Test compilation fails when TeX file doesn't exist."""
        # Remove the main.tex file
        (env.project_root / 'main.tex').unlink()

        with pytest.raises(LuvError, match="TeX file not found"):
            env.compile()

    def test_has_bibliography_detection(self, env):
        """Test bibliography detection."""
        # Test with \\bibliography command
        tex_with_bib = env.project_root / 'with_bib.tex'
        tex_with_bib.write_text('\\bibliography{refs}')
        assert env._has_bibliography(tex_with_bib)

        # Test with \\bibliographystyle
        tex_with_style = env.project_root / 'with_style.tex'
        tex_with_style.write_text('\\bibliographystyle{plain}')
        assert env._has_bibliography(tex_with_style)

        # Test with biblatex
        tex_with_biblatex = env.project_root / 'with_biblatex.tex'
        tex_with_biblatex.write_text('\\printbibliography')
        assert env._has_bibliography(tex_with_biblatex)

        # Test without bibliography and no .bib files
        tex_without = env.project_root / 'without.tex'
        tex_without.write_text('\\documentclass{article}')
        # Remove any .bib files that might exist from the fixture
        for bib_file in env.project_root.glob('*.bib'):
            bib_file.unlink()
        assert not env._has_bibliography(tex_without)

    def test_has_citations_detection(self, env):
        """Test citation detection."""
        # Test with \\cite
        tex_with_cite = env.project_root / 'with_cite.tex'
        tex_with_cite.write_text('\\cite{einstein1905}')
        assert env._has_citations(tex_with_cite)

        # Test with \\citep
        tex_with_citep = env.project_root / 'with_citep.tex'
        tex_with_citep.write_text('\\citep{einstein1905}')
        assert env._has_citations(tex_with_citep)

        # Test without citations
        tex_without = env.project_root / 'without.tex'
        tex_without.write_text('\\documentclass{article}')
        assert not env._has_citations(tex_without)

    def test_bibliography_backend_detection(self, env):
        """Test bibliography backend detection."""
        # Test biber backend
        tex_biber = env.project_root / 'biber.tex'
        tex_biber.write_text('\\usepackage[backend=biber]{biblatex}')
        assert env._get_bibliography_backend(tex_biber) == 'biber'

        # Test bibtex backend
        tex_bibtex = env.project_root / 'bibtex.tex'
        tex_bibtex.write_text('\\usepackage[backend=bibtex]{biblatex}')
        assert env._get_bibliography_backend(tex_bibtex) == 'bibtex'

        # Test traditional bibtex
        tex_traditional = env.project_root / 'traditional.tex'
        tex_traditional.write_text('\\bibliographystyle{plain}')
        assert env._get_bibliography_backend(tex_traditional) == 'bibtex'

        # Test biblatex without explicit backend (defaults to biber)
        tex_default = env.project_root / 'default.tex'
        tex_default.write_text('\\usepackage{biblatex}')
        assert env._get_bibliography_backend(tex_default) == 'biber'

    def test_check_warnings(self, env, capsys):
        """Test warning detection in LaTeX output."""
        # Test undefined references warning
        stdout_with_undef_refs = (
            "LaTeX Warning: There were undefined references."
        )
        env._check_warnings(stdout_with_undef_refs)
        captured = capsys.readouterr()
        assert "Undefined references detected" in captured.out

        # Test citation warnings
        stdout_with_cite_undef = "LaTeX Warning: Citation `key' undefined."
        env._check_warnings(stdout_with_cite_undef)
        captured = capsys.readouterr()
        assert "Undefined citations" in captured.out

        # Test rerun suggestion
        stdout_with_rerun = "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right."
        env._check_warnings(stdout_with_rerun)
        captured = capsys.readouterr()
        assert "LaTeX suggests rerunning" in captured.out

    def test_compile_single_pass_mock(self, env, mocker):
        """Test single pass compilation with mocked subprocess."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")

        # Create build directory and mock PDF creation
        build_dir = env.project_root / 'build'
        build_dir.mkdir(exist_ok=True)
        pdf_file = build_dir / 'main.pdf'

        def create_pdf(*args, **kwargs):
            pdf_file.write_text("fake pdf content")
            return mocker.Mock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = create_pdf

        result = env._compile_single_pass('main.tex', 'build', 'pdflatex', {})
        assert result is True
        mock_run.assert_called_once()

    def test_compile_single_pass_failure(self, env, mocker):
        """Test single pass compilation failure."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=1, stdout="Error!", stderr="Fatal error"
        )

        result = env._compile_single_pass('main.tex', 'build', 'pdflatex', {})
        assert result is False

    def test_run_latex_pass_success(self, env, mocker):
        """Test successful LaTeX pass."""
        mock_run = mocker.patch('subprocess.run')

        # Create build directory and PDF file
        build_dir = env.project_root / 'build'
        build_dir.mkdir(exist_ok=True)
        pdf_file = build_dir / 'main.pdf'
        pdf_file.write_text("fake pdf")

        mock_run.return_value = mocker.Mock(returncode=0, stdout='', stderr='')

        result = env._run_latex_pass('main.tex', 'build', 'pdflatex', {})
        assert result is True

    def test_run_latex_pass_no_pdf(self, env, mocker):
        """Test LaTeX pass that doesn't generate PDF."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout='', stderr='')

        # Don't create PDF file
        result = env._run_latex_pass('main.tex', 'build', 'pdflatex', {})
        assert result is False

    def test_run_latex_pass_fatal_error(self, env, mocker):
        """Test LaTeX pass with fatal error."""
        mock_run = mocker.patch('subprocess.run')

        # Create PDF but have fatal error in output
        build_dir = env.project_root / 'build'
        build_dir.mkdir(exist_ok=True)
        pdf_file = build_dir / 'main.pdf'
        pdf_file.write_text('fake pdf')

        mock_run.return_value = mocker.Mock(
            returncode=0, stdout='Fatal error occurred', stderr=''
        )

        result = env._run_latex_pass('main.tex', 'build', 'pdflatex', {})
        assert result is False

    def test_run_bibtex_success(self, env, mocker):
        """Test successful BibTeX run."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout="", stderr="")

        result = env._run_bibtex('main', 'build', {})
        assert result is True

    def test_run_bibtex_failure(self, env, mocker):
        """Test BibTeX failure."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=1, stdout='Error', stderr=''
        )

        result = env._run_bibtex('main', 'build', {})
        assert result is False

    def test_run_bibtex_not_found(self, env, mocker):
        """Test BibTeX not found."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.side_effect = FileNotFoundError()

        result = env._run_bibtex('main', 'build', {})
        assert result is False

    def test_run_biber_success(self, env, mocker):
        """Test successful Biber run."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout='', stderr='')

        result = env._run_biber('main', 'build', {})
        assert result is True

    def test_run_biber_fallback_to_bibtex(self, env, mocker):
        """Test Biber fallback to BibTeX."""
        mock_run = mocker.patch('subprocess.run')

        # First call (biber) fails, second call (bibtex) succeeds
        mock_run.side_effect = [
            FileNotFoundError(),  # biber not found
            mocker.Mock(returncode=0, stdout='', stderr=''),  # bibtex succeeds
        ]

        result = env._run_biber('main', 'build', {})
        assert result is True

        # Should have tried multiple biber commands and eventually fallen back
        # The current implementation tries all biber commands first, then prints warnings
        assert (
            mock_run.call_count >= 2
        )  # At least one biber attempt + bibtex fallback


class TestPackageInstallation:
    """Test package installation functionality."""

    @pytest.fixture
    def env(self):
        """Create a LaTeXEnvironment in a temporary directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            env = LaTeXEnvironment(project_root)
            env.create()
            yield env

    def test_install_package_no_environment(self, capsys):
        """Test package installation logs error without environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            env = LaTeXEnvironment(Path(temp_dir))

            env.install_package('amsmath')

            # Check that error was logged to stdout
            captured = capsys.readouterr()
            assert 'No environment found' in captured.out

    def test_setup_tlmgr_user_mode(self, env, mocker):
        """Test tlmgr user mode setup."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout='', stderr='')

        env._setup_tlmgr_user_mode()

        # Should call tlmgr init-usertree
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert 'tlmgr' in args
        assert 'init-usertree' in args

    def test_setup_tlmgr_user_mode_already_exists(self, env, mocker):
        """Test tlmgr setup when already initialized."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=1, stdout='', stderr='already exists'
        )

        # Should not raise error when already exists
        env._setup_tlmgr_user_mode()

    def test_setup_tlmgr_user_mode_not_found(self, env, mocker):
        """Test tlmgr setup when tlmgr not found."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.side_effect = FileNotFoundError()

        with pytest.raises(LuvError, match='tlmgr not found'):
            env._setup_tlmgr_user_mode()

    def test_try_install_package_success(self, env, mocker):
        """Test successful package installation."""
        mocker.patch.object(env, '_setup_tlmgr_user_mode')
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout='', stderr='')

        result = env._try_install_package('amsmath')
        assert result is True

    def test_try_install_package_already_installed(self, env, mocker):
        """Test installing already installed package."""
        mocker.patch.object(env, '_setup_tlmgr_user_mode')
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=0, stdout='already installed', stderr=''
        )

        result = env._try_install_package('amsmath')
        assert result is True

    def test_try_install_package_not_present(self, env, mocker):
        """Test installing package not in repository."""
        mocker.patch.object(env, '_setup_tlmgr_user_mode')
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=1, stdout="", stderr="not present in repository"
        )

        result = env._try_install_package('nonexistent')
        assert result is False

    def test_try_install_package_updmap_error(self, env, mocker):
        """Test package installation with updmap error (should still succeed)."""
        mocker.patch.object(env, '_setup_tlmgr_user_mode')
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=1, stdout='install: package', stderr='updmap failed'
        )

        result = env._try_install_package('amsmath')
        assert result is True

    def test_install_package_smart_direct_success(self, env, mocker):
        """Test smart installation with direct success."""
        mock_try_install = mocker.patch.object(
            env, '_try_install_package', return_value=True
        )

        result = env.install_package_smart('amsmath')
        assert result is True
        mock_try_install.assert_called_once_with('amsmath')

    def test_install_package_smart_with_resolution(self, env, mocker):
        """Test smart installation with package resolution."""
        mock_try_install = mocker.patch.object(env, '_try_install_package')
        mock_try_install.side_effect = [
            False,
            True,
        ]  # First fails, second succeeds

        # Mock the PackageResolver that gets created inside install_package_smart
        mock_resolver_class = mocker.patch('luv.PackageResolver')
        mock_resolver = mocker.Mock()
        mock_resolver.resolve_package_name.return_value = 'texlive-amsmath'
        mock_resolver_class.return_value = mock_resolver

        result = env.install_package_smart('amsmath')
        assert result is True
        assert mock_try_install.call_count == 2
        mock_try_install.assert_any_call('amsmath')
        mock_try_install.assert_any_call('texlive-amsmath')

    def test_remove_package_success(self, env, mocker):
        """Test successful package removal."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(returncode=0, stdout='', stderr='')

        # Create requirements file with the package
        env.requirements_file.write_text('amsmath\ngraphicx')

        env.remove_package('amsmath')

        # Verify package was removed from requirements
        requirements = env.get_requirements()
        assert 'amsmath' not in requirements
        assert 'graphicx' in requirements

    def test_remove_package_not_installed(self, env, mocker):
        """Test removing package that's not installed."""
        mock_run = mocker.patch('subprocess.run')
        mock_run.return_value = mocker.Mock(
            returncode=1, stdout='not installed', stderr=''
        )

        # Should not raise error
        env.remove_package('nonexistent')

    def test_remove_package_no_environment(self, capsys):
        """Test package removal logs error without environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            env = LaTeXEnvironment(Path(temp_dir))

            env.remove_package('amsmath')

            # Check that error was logged to stdout
            captured = capsys.readouterr()
            assert 'No environment found' in captured.out
