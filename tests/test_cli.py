#!/usr/bin/env python3
"""
Tests for the main CLI functionality and integration scenarios.
"""

import sys
import tempfile
from pathlib import Path

import pytest

from luv import LaTeXEnvironment, LuvError, main


class TestMainCLI:
    """Test the main CLI function."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            yield project_root

    def test_main_no_args(self, mocker, capsys):
        """Test main() with no arguments shows help."""
        mocker.patch.object(sys, 'argv', ['luv'])

        # main() with no args just prints help and returns, doesn't exit
        main()

        captured = capsys.readouterr()
        assert 'usage:' in captured.out.lower()

    def test_main_init_command(self, mocker, temp_project):
        """Test the init command."""
        mocker.patch.object(sys, 'argv', ['luv', 'init'])
        mocker.patch('pathlib.Path.cwd', return_value=temp_project)

        # Mock print to avoid output during tests
        mocker.patch('builtins.print')

        main()

        # Verify environment was created
        env = LaTeXEnvironment(temp_project)
        assert env.exists()
        assert env.config_file.exists()
        assert env.requirements_file.exists()

    def test_main_init_with_custom_texfile(self, mocker, temp_project):
        """Test init command with custom texfile."""
        mock_argv = mocker.patch.object(
            sys, 'argv', ['luv', 'init', '--texfile', 'document.tex']
        )
        mock_cwd = mocker.patch('pathlib.Path.cwd', return_value=temp_project)
        mock_print = mocker.patch('builtins.print')

        main()

        # Verify custom texfile was set
        env = LaTeXEnvironment(temp_project)
        config = env.get_config()
        assert config['project']['texfile'] == 'document.tex'

    def test_main_init_with_custom_engine(self, mocker, temp_project):
        """Test init command with custom engine."""
        mock_argv = mocker.patch.object(
            sys, 'argv', ['luv', 'init', '--engine', 'xelatex']
        )
        mock_cwd = mocker.patch('pathlib.Path.cwd', return_value=temp_project)
        mock_print = mocker.patch('builtins.print')

        main()

        # Verify custom engine was set
        env = LaTeXEnvironment(temp_project)
        config = env.get_config()
        assert config['project']['engine'] == 'xelatex'

    def test_main_command_without_project_root(self, mocker):
        """Test commands that require project root but none exists."""
        mock_argv = mocker.patch.object(sys, 'argv', ['luv', 'sync'])
        mock_find_root = mocker.patch(
            'luv.find_project_root', return_value=None
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_resolve_command(self, mocker, temp_project):
        """Test the resolve command."""
        # Set up project
        env = LaTeXEnvironment(temp_project)
        env.create()

        # Create main.tex
        main_tex = temp_project / 'main.tex'
        main_tex.write_text('\\usepackage{amsmath}')

        mocker.patch.object(sys, 'argv', ['luv', 'resolve', '--dry-run'])
        mocker.patch('luv.find_project_root', return_value=temp_project)
        mock_resolve = mocker.patch.object(
            env.__class__, 'resolve_dependencies'
        )

        # Mock the LaTeXEnvironment class to return our prepared instance
        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env

        main()

        # Verify resolve_dependencies was called with correct parameters
        mock_resolve.assert_called_once_with(
            update_requirements=False, interactive=False
        )

    def test_main_sync_command(self, mocker, temp_project):
        """Test the sync command."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mocker.patch.object(sys, 'argv', ['luv', 'sync'])
        mocker.patch('luv.find_project_root', return_value=temp_project)
        mock_sync = mocker.patch.object(env.__class__, 'sync')

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        mock_sync.assert_called_once()

    def test_main_add_command(self, mocker, temp_project):
        """Test the add command."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mocker.patch.object(sys, 'argv', ['luv', 'add', 'amsmath', 'graphicx'])
        mocker.patch('luv.find_project_root', return_value=temp_project)
        mock_install = mocker.patch.object(env.__class__, 'install_package')
        mocker.patch.object(env.__class__, 'get_requirements', return_value=[])
        mock_resolver_init = mocker.patch('luv.PackageResolver')
        mock_resolver = mocker.Mock()
        mock_resolver.resolve_package_name.side_effect = (
            lambda x: x
        )  # Return same name
        mock_resolver_init.return_value = mock_resolver

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        # Should install both packages
        assert mock_install.call_count == 2
        mock_install.assert_any_call('amsmath')
        mock_install.assert_any_call('graphicx')

    def test_main_remove_command(self, mocker, temp_project):
        """Test the remove command."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mock_argv = mocker.patch.object(
            sys, 'argv', ['luv', 'remove', 'amsmath']
        )
        mock_find_root = mocker.patch(
            'luv.find_project_root', return_value=temp_project
        )
        mock_remove = mocker.patch.object(env.__class__, 'remove_package')
        mock_resolver_init = mocker.patch('luv.PackageResolver')
        mock_resolver = mocker.Mock()
        mock_resolver.resolve_package_name.return_value = 'amsmath'
        mock_resolver_init.return_value = mock_resolver

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        mock_remove.assert_called_once_with('amsmath')

    def test_main_clean_command(self, mocker, temp_project):
        """Test the clean command."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mock_argv = mocker.patch.object(sys, 'argv', ['luv', 'clean'])
        mock_find_root = mocker.patch(
            'luv.find_project_root', return_value=temp_project
        )
        mock_clean = mocker.patch.object(env.__class__, 'clean')

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        mock_clean.assert_called_once()

    def test_main_compile_command(self, mocker, temp_project):
        """Test the compile command."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mock_argv = mocker.patch.object(sys, 'argv', ['luv', 'compile'])
        mock_find_root = mocker.patch(
            'luv.find_project_root', return_value=temp_project
        )
        mock_compile = mocker.patch.object(env.__class__, 'compile')

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        mock_compile.assert_called_once_with(clean=False)

    def test_main_compile_with_clean(self, mocker, temp_project):
        """Test the compile command with --clean flag."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mock_argv = mocker.patch.object(
            sys, 'argv', ['luv', 'compile', '--clean']
        )
        mock_find_root = mocker.patch(
            'luv.find_project_root', return_value=temp_project
        )
        mock_compile = mocker.patch.object(env.__class__, 'compile')

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        mock_compile.assert_called_once_with(clean=True)

    def test_main_info_command(self, mocker, temp_project):
        """Test the info command."""
        env = LaTeXEnvironment(temp_project)
        env.create()

        mock_argv = mocker.patch.object(sys, 'argv', ['luv', 'info'])
        mock_find_root = mocker.patch(
            'luv.find_project_root', return_value=temp_project
        )
        mock_get_config = mocker.patch.object(
            env.__class__,
            'get_config',
            return_value={
                'project': {
                    'texfile': 'main.tex',
                    'engine': 'pdflatex',
                    'output_dir': 'build',
                }
            },
        )
        mock_get_req = mocker.patch.object(
            env.__class__, 'get_requirements', return_value=['amsmath']
        )
        mock_print = mocker.patch('builtins.print')

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env_class.return_value = env
        main()

        # Verify info was printed
        mock_print.assert_called()
        # Check that project info was displayed
        print_calls = [
            call[0][0] for call in mock_print.call_args_list if call[0]
        ]
        info_text = ' '.join(print_calls)
        assert 'main.tex' in info_text
        assert 'amsmath' in info_text

    def test_main_luv_error_handling(self, mocker, temp_project):
        """Test that LuvError exceptions are handled properly."""
        mocker.patch.object(sys, 'argv', ['luv', 'sync'])
        mocker.patch('luv.find_project_root', return_value=temp_project)

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env = mocker.Mock()
        mock_env.sync.side_effect = LuvError("Test error")
        mock_env_class.return_value = mock_env

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_keyboard_interrupt_handling(self, mocker, temp_project):
        """Test that KeyboardInterrupt is handled properly."""
        mocker.patch.object(sys, 'argv', ['luv', 'sync'])
        mocker.patch('luv.find_project_root', return_value=temp_project)
        mock_print = mocker.patch('builtins.print')

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env = mocker.Mock()
        mock_env.sync.side_effect = KeyboardInterrupt()
        mock_env_class.return_value = mock_env

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        mock_print.assert_called_with('\nOperation cancelled.')

    def test_main_unexpected_error_handling(self, mocker, temp_project):
        """Test that unexpected exceptions are handled properly."""
        mocker.patch.object(sys, 'argv', ['luv', 'sync'])
        mocker.patch('luv.find_project_root', return_value=temp_project)

        mock_env_class = mocker.patch('luv.LaTeXEnvironment')
        mock_env = mocker.Mock()
        mock_env.sync.side_effect = RuntimeError("Unexpected error")
        mock_env_class.return_value = mock_env

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1


class TestArgumentParsing:
    """Test argument parsing and command-line interface."""

    def test_help_message(self, mocker, capsys):
        """Test that help message is displayed correctly."""
        mock_argv = mocker.patch.object(sys, 'argv', ['luv', '--help'])

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert 'LaTeX Universal Virtualizer' in captured.out
        assert 'init' in captured.out
        assert 'resolve' in captured.out
        assert 'sync' in captured.out

    def test_init_help(self, mocker, capsys):
        """Test init command help."""
        mock_argv = mocker.patch.object(sys, 'argv', ['luv', 'init', '--help'])

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert '--texfile' in captured.out
        assert '--engine' in captured.out

    def test_resolve_help(self, mocker, capsys):
        """Test resolve command help."""
        mock_argv = mocker.patch.object(
            sys, 'argv', ['luv', 'resolve', '--help']
        )

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert '--update' in captured.out
        assert '--dry-run' in captured.out

    def test_compile_help(self, mocker, capsys):
        """Test compile command help."""
        mock_argv = mocker.patch.object(
            sys, 'argv', ['luv', 'compile', '--help']
        )

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert '--clean' in captured.out
