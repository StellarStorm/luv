#!/usr/bin/env python3
"""
luv - LaTeX Universal Virtualizer
A tool for managing LaTeX projects with isolated package environments.

Copyright (C) 2025 Skylar Gay
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


class SimpleTOMLWriter:
    """Simple TOML writer for basic functionality."""

    @staticmethod
    def dumps(data: dict[str, Any], indent: int = 0) -> str:
        """Convert dict to TOML format."""
        lines = []
        indent_str = '  ' * indent

        # First, write non-dict values
        for key, value in data.items():
            if not isinstance(value, dict):
                if isinstance(value, str):
                    lines.append(f'{indent_str}{key} = "{value}"')
                elif isinstance(value, bool):
                    lines.append(f'{indent_str}{key} = {str(value).lower()}')
                elif isinstance(value, (int, float)):
                    lines.append(f'{indent_str}{key} = {value}')
                elif isinstance(value, list):
                    # Handle arrays
                    if all(isinstance(item, str) for item in value):
                        items = ', '.join(f'"{item}"' for item in value)
                        lines.append(f'{indent_str}{key} = [{items}]')
                    else:
                        items = ', '.join(str(item) for item in value)
                        lines.append(f'{indent_str}{key} = [{items}]')

        # Then write sections (dicts)
        for key, value in data.items():
            if isinstance(value, dict):
                if indent == 0:
                    lines.append(f'\n[{key}]')
                    lines.append(SimpleTOMLWriter.dumps(value, indent + 1))
                else:
                    # Nested sections - flatten the key path
                    lines.append(f'\n{indent_str}[{key}]')
                    lines.append(SimpleTOMLWriter.dumps(value, indent + 1))

        return '\n'.join(filter(None, lines))


class PackageResolver:
    """Resolves LaTeX package dependencies from source files using tlmgr search."""

    # Only core packages that should be skipped (already part of LaTeX base)
    CORE_PACKAGES = {
        'fontenc',
        'inputenc',
        'textcomp',
        'ifthen',
        'calc',
        'url',
    }

    # Common LaTeX packages and their typical command/environment indicators
    PACKAGE_PATTERNS = {
        # Math packages
        'amsmath': [
            r'\\begin\{align',
            r'\\begin\{equation',
            r'\\begin\{gather',
            r'\\begin\{multline',
            r'\\begin\{split}',
        ],
        'amssymb': [r'\\mathbb\{', r'\\mathfrak\{', r'\\mathcal\{'],
        'amsthm': [r'\\newtheorem', r'\\theoremstyle', r'\\begin\{proof\}'],
        # Graphics and figures
        'graphicx': [r'\\includegraphics', r'\\rotatebox', r'\\scalebox'],
        'subfig': [r'\\subfloat', r'\\subref'],
        'subcaption': [r'\\subcaptionbox', r'\\begin\{subfigure\}'],
        'tikz': [r'\\begin\{tikzpicture\}', r'\\tikz', r'\\usetikzlibrary'],
        'pgfplots': [r'\\begin\{axis\}', r'\\addplot'],
        # Tables and formatting
        'booktabs': [r'\\toprule', r'\\midrule', r'\\bottomrule'],
        'longtable': [r'\\begin\{longtable\}'],
        'array': [r'\\newcolumntype', r'\\arraybackslash'],
        'multirow': [r'\\multirow'],
        'multicol': [r'\\begin\{multicols\}'],
        'colortbl': [
            r'\\rowcolor\{',
            r'\\columncolor\{',
            r'\\cellcolor\{',
            r'\\usepackage\[.*table.*\]\{xcolor\}',
        ],
        # References and citations
        'hyperref': [r'\\href\{', r'\\url\{', r'\\hyperlink', r'\\autoref'],
        'natbib': [r'\\citep\{', r'\\citet\{', r'\\citeauthor'],
        # biblatex requires logreq and etoolbox
        'biblatex': [
            r'\\printbibliography',
            r'\\addbibresource',
            r'\\usepackage.*biblatex',
        ],
        'logreq': [r'\\usepackage.*biblatex'],  # biblatex dependency
        'etoolbox': [r'\\usepackage.*biblatex'],  # biblatex dependency
        'cleveref': [r'\\cref\{', r'\\Cref\{'],
        # Font and encoding
        'babel': [r'\\selectlanguage', r'\\foreignlanguage'],
        # Layout and spacing
        'geometry': [r'\\newgeometry', r'\\restoregeometry'],
        'setspace': [r'\\doublespacing', r'\\onehalfspacing', r'\\setstretch'],
        'fancyhdr': [r'\\fancyhead', r'\\fancyfoot', r'\\pagestyle\{fancy\}'],
        'titlesec': [r'\\titleformat', r'\\titlespacing'],
        # Colors
        'xcolor': [r'\\textcolor\{', r'\\colorbox\{', r'\\definecolor'],
        'color': [r'\\color\{'],
        # Lists and enumerations
        'enumitem': [r'\\setlist', r'\\newlist'],
        # Code listings
        'listings': [r'\\begin\{lstlisting\}', r'\\lstinputlisting'],
        'minted': [r'\\begin\{minted\}', r'\\mint\{'],
        'verbatim': [r'\\begin\{verbatim\}'],
        # Algorithms
        'algorithm': [r'\\begin\{algorithm\}'],
        'algorithmic': [r'\\begin\{algorithmic\}'],
        'algorithmicx': [r'\\algstore', r'\\algrestore'],
        # Miscellaneous
        'lipsum': [r'\\lipsum'],
        'blindtext': [r'\\blindtext', r'\\Blindtext'],
        'todonotes': [r'\\todo\{', r'\\missingfigure'],
        'authblk': [r'\\author\[', r'\\affil\{'],
        'float': [r'\\newfloat', r'\\floatstyle'],
        'lineno': [r'\\linenumbers', r'\\modulolinenumbers'],
    }

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.found_packages = set()
        self.explicitly_used = set()

    def resolve_package_name(self, latex_package: str) -> str | None:
        """Use tlmgr to find the correct TeX Live package name for a LaTeX package."""
        if latex_package in self.CORE_PACKAGES:
            return None  # Skip core packages

        try:
            # Search for the .sty file with leading slash for exact match
            cmd = [
                'tlmgr',
                'search',
                '--global',
                '--file',
                f'/{latex_package}.sty',
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False
            )

            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                candidates = []

                for line in lines:
                    line = line.strip()
                    # Package names are lines that don't start with whitespace and end with ':'
                    if line and not line.startswith(' ') and line.endswith(':'):
                        package_name = line.rstrip(':').strip()
                        if package_name and package_name not in [
                            'tlmgr',
                            'texlive-base',
                        ]:
                            # Prefer exact matches or packages with similar names
                            if (
                                latex_package.lower() in package_name.lower()
                                or package_name.lower() in latex_package.lower()
                            ):
                                return package_name
                            candidates.append(package_name)

                # Return first valid candidate if no exact match
                if candidates:
                    return candidates[0]

        except FileNotFoundError:
            # tlmgr not available, return the original name
            pass

        # If tlmgr search fails, assume package name is correct
        return latex_package

    def resolve_dependencies(self, main_file: str) -> list[str]:
        """Resolve all package dependencies from LaTeX source files."""
        self.found_packages = set()
        self.explicitly_used = set()

        # First, find all explicitly declared packages
        self._find_explicit_packages(main_file)

        # Then, scan for usage patterns that suggest additional packages
        self._scan_for_patterns(main_file)

        # Resolve all package names using tlmgr
        all_packages = self.found_packages | self.explicitly_used
        resolved_packages = set()

        print(f'Resolving {len(all_packages)} packages using tlmgr...')

        for package in all_packages:
            tex_live_name = self.resolve_package_name(package)
            if tex_live_name is not None:
                resolved_packages.add(tex_live_name)
                if tex_live_name != package:
                    print(f'  {package} → {tex_live_name}')

        # Add automatic dependencies based on detected packages
        all_packages = self.found_packages | self.explicitly_used

        # biblatex automatically requires these packages
        if 'biblatex' in all_packages:
            self.found_packages.add('logreq')
            self.found_packages.add('etoolbox')
            print('  Added biblatex dependencies: logreq, etoolbox')

        # Return sorted list of unique resolved packages
        return sorted(list(resolved_packages))

    def _find_explicit_packages(self, main_file: str) -> None:
        """Find packages explicitly declared with \\usepackage."""
        visited_files = set()
        self._scan_file_for_packages(
            self.project_root / main_file, visited_files
        )

    def _scan_file_for_packages(
        self, file_path: Path, visited_files: set[Path]
    ) -> None:
        """Recursively scan file and included files for \\usepackage declarations."""
        if file_path in visited_files or not file_path.exists():
            return

        visited_files.add(file_path)

        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            return

        # Find \\usepackage declarations
        usepackage_pattern = r'\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}'
        matches = re.findall(usepackage_pattern, content)

        for match in matches:
            # Handle multiple packages in one declaration: {pkg1,pkg2,pkg3}
            packages = [pkg.strip() for pkg in match.split(',')]
            # Filter out packages that have local .sty files in the project
            filtered_packages = []
            for pkg in packages:
                local_sty = self.project_root / f'{pkg}.sty'
                if not local_sty.exists():
                    filtered_packages.append(pkg)
            self.explicitly_used.update(filtered_packages)

        # Find included files
        include_patterns = [
            r'\\input\{([^}]+)\}',
            r'\\include\{([^}]+)\}',
            r'\\subfile\{([^}]+)\}',
            r'\\InputIfFileExists\{([^}]+)\}',
        ]

        for pattern in include_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                # Handle relative paths and add .tex extension if missing
                included_file = match.strip()
                if not included_file.endswith('.tex'):
                    included_file += '.tex'

                included_path = file_path.parent / included_file
                if not included_path.exists():
                    included_path = self.project_root / included_file

                self._scan_file_for_packages(included_path, visited_files)

    def _scan_for_patterns(self, main_file: str) -> None:
        """Scan for usage patterns that suggest specific packages are needed."""
        visited_files = set()
        self._scan_file_for_patterns(
            self.project_root / main_file, visited_files
        )

    def _scan_file_for_patterns(
        self, file_path: Path, visited_files: set[Path]
    ) -> None:
        """Recursively scan file and included files for package usage patterns."""
        if file_path in visited_files or not file_path.exists():
            return

        visited_files.add(file_path)

        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception:
            return

        # Check each package pattern
        for package, patterns in self.PACKAGE_PATTERNS.items():
            if (
                package not in self.explicitly_used
            ):  # Only suggest if not already declared
                # Skip if local .sty file exists
                local_sty = self.project_root / f'{package}.sty'
                if local_sty.exists():
                    continue

                for pattern in patterns:
                    if re.search(pattern, content):
                        self.found_packages.add(package)
                        break  # Found one pattern for this package, no need to check others

        # Find included files (same logic as above)
        include_patterns = [
            r'\\input\{([^}]+)\}',
            r'\\include\{([^}]+)\}',
            r'\\subfile\{([^}]+)\}',
            r'\\InputIfFileExists\{([^}]+)\}',
        ]

        for pattern in include_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                included_file = match.strip()
                if not included_file.endswith('.tex'):
                    included_file += '.tex'

                included_path = file_path.parent / included_file
                if not included_path.exists():
                    included_path = self.project_root / included_file

                self._scan_file_for_patterns(included_path, visited_files)


class LuvError(Exception):
    """Base exception for luv errors."""

    pass


class LaTeXEnvironment:
    """Manages a local LaTeX environment."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.luv_dir = project_root / '.luv'
        self.texmf_dir = self.luv_dir / 'texmf'
        self.packages_dir = self.texmf_dir / 'tex' / 'latex'
        self.config_file = project_root / 'luv.toml'
        self.requirements_file = project_root / 'latex-requirements.txt'
        self._tlmgr_initialized = False

    def exists(self) -> bool:
        """Check if the environment exists."""
        return self.luv_dir.exists() and self.texmf_dir.exists()

    def create(self) -> None:
        """Create a new LaTeX environment."""
        if self.exists():
            raise LuvError(f'Environment already exists at {self.luv_dir}')

        print(f'Creating LaTeX environment at {self.luv_dir}')

        # Create directory structure
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        (self.luv_dir / 'bin').mkdir(exist_ok=True)
        (self.luv_dir / 'cache').mkdir(exist_ok=True)

        # Create initial luv.toml if it doesn't exist
        if not self.config_file.exists():
            self._create_initial_config()

        # Create initial requirements file if it doesn't exist
        if not self.requirements_file.exists():
            self._create_initial_requirements()

        print('Environment created successfully!')

    def _create_initial_config(self) -> None:
        """Create initial luv.toml configuration."""
        config = {
            'project': {
                'texfile': 'main.tex',
                'output_dir': 'build',
                'engine': 'pdflatex',
            }
        }

        with open(self.config_file, 'w') as f:
            f.write(SimpleTOMLWriter.dumps(config))

        print(f'Created {self.config_file}')

    def _create_initial_requirements(self) -> None:
        """Create initial latex-requirements.txt."""
        initial_packages = [
            '# LaTeX package requirements',
            '# Add packages one per line, optionally with versions',
            '# Example:',
            '# amsmath',
            '# graphicx',
            '# hyperref',
            '',
        ]

        with open(self.requirements_file, 'w') as f:
            f.write('\n'.join(initial_packages))

        print(f'Created {self.requirements_file}')

    def remove(self) -> None:
        """Remove the LaTeX environment."""
        if not self.exists():
            raise LuvError('No environment found to remove')

        shutil.rmtree(self.luv_dir)
        print(f'Removed environment at {self.luv_dir}')

    def get_config(self) -> dict:
        """Load configuration from luv.toml."""
        if not self.config_file.exists():
            raise LuvError(f'No luv.toml found at {self.config_file}')

        try:
            with open(self.config_file, 'rb') as f:
                return tomllib.load(f)
        except Exception as e:
            raise LuvError(f'Error reading luv.toml: {e}')

    def update_config(self, config: dict) -> None:
        """Update configuration in luv.toml."""
        with open(self.config_file, 'w') as f:
            f.write(SimpleTOMLWriter.dumps(config))

    def get_requirements(self) -> list[str]:
        """Load requirements from latex-requirements.txt."""
        if not self.requirements_file.exists():
            return []

        requirements = []
        with open(self.requirements_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    requirements.append(line)

        return requirements

    def resolve_dependencies(
        self, update_requirements: bool = None, interactive: bool = True
    ) -> list[str]:
        """Resolve package dependencies from LaTeX source files."""
        config = self.get_config()
        texfile = config.get('project', {}).get('texfile', 'main.tex')

        if not (self.project_root / texfile).exists():
            raise LuvError(f'TeX file not found: {texfile}')

        print(f'Analyzing {texfile} and included files...')

        resolver = PackageResolver(self.project_root)
        packages = resolver.resolve_dependencies(texfile)

        # Get current requirements to see what's missing
        existing_requirements = self.get_requirements()
        missing_packages = set(packages) - set(existing_requirements)

        print(f'\nFound {len(packages)} installable packages:')
        if resolver.explicitly_used:
            print('Explicitly declared packages:')
            for pkg in sorted(resolver.explicitly_used):
                if pkg not in resolver.CORE_PACKAGES:
                    resolved_name = resolver.resolve_package_name(pkg)
                    if resolved_name:
                        status = (
                            '✓'
                            if resolved_name in existing_requirements
                            else '!'
                        )
                        display = (
                            f'{pkg} → {resolved_name}'
                            if resolved_name != pkg
                            else pkg
                        )
                        print(f'  {status} {display}')

        suggested_packages = resolver.found_packages - resolver.explicitly_used
        if suggested_packages:
            print('\nSuggested packages (based on usage patterns):')
            for pkg in sorted(suggested_packages):
                if pkg not in resolver.CORE_PACKAGES:
                    resolved_name = resolver.resolve_package_name(pkg)
                    if resolved_name:
                        status = (
                            '✓'
                            if resolved_name in existing_requirements
                            else '+'
                        )
                        display = (
                            f'{pkg} → {resolved_name}'
                            if resolved_name != pkg
                            else pkg
                        )
                        print(f'  {status} {display}')

        # Show summary of what's missing from requirements
        if missing_packages:
            print(
                f'\n{len(missing_packages)} packages not in latex-requirements.txt:'
            )
            for pkg in sorted(missing_packages):
                print(f'  - {pkg}')

        # Determine if we should update requirements
        should_update = False

        if update_requirements is True:
            should_update = True
        elif update_requirements is False:
            should_update = False
        elif missing_packages and interactive:
            # Ask user if they want to update when packages are missing from requirements
            try:
                response = (
                    input(
                        f'\nAdd {len(missing_packages)} missing packages to latex-requirements.txt? [Y/n]: '
                    )
                    .strip()
                    .lower()
                )
                should_update = response in ['', 'y', 'yes']
            except (EOFError, KeyboardInterrupt):
                print('\nSkipping update.')
                should_update = False
        elif missing_packages:
            # Non-interactive mode with missing packages
            print(
                f"\nUse 'luv resolve --update' to add {len(missing_packages)} missing packages to latex-requirements.txt"
            )
        if should_update:
            self._update_requirements_file(packages)

        return packages

    def _update_requirements_file(self, packages: list[str]) -> None:
        """Update latex-requirements.txt with resolved packages."""
        existing_requirements = self.get_requirements()

        # Combine existing with new packages, removing duplicates
        all_packages = set(existing_requirements + packages)

        with open(self.requirements_file, 'w') as f:
            f.write('# LaTeX package requirements\n')
            f.write("# Generated by 'luv resolve'\n\n")
            for package in sorted(all_packages):
                f.write(f'{package}\n')

        print(
            f'\nUpdated {self.requirements_file} with {len(all_packages)} packages'
        )

    def _setup_tlmgr_user_mode(self) -> None:
        """Set up tlmgr to use user mode with our local directory."""
        if not self.exists():
            raise LuvError("No environment found. Run 'luv init' first.")

        # Only run init-usertree once per session
        if self._tlmgr_initialized:
            return

        # Set TEXMFHOME to our local texmf directory
        env = os.environ.copy()
        env['TEXMFHOME'] = str(self.texmf_dir)

        try:
            # Initialize tlmgr user mode if not already done
            result = subprocess.run(
                ['tlmgr', 'init-usertree'],
                env=env,
                capture_output=True,
                text=True,
                check=False,  # Don't raise exception on non-zero exit
            )

            # init-usertree returns non-zero if already initialized, which is fine
            if result.returncode != 0 and 'already exists' not in result.stderr:
                print(f'Warning: tlmgr init-usertree returned: {result.stderr}')

            self._tlmgr_initialized = True

        except FileNotFoundError:
            raise LuvError('tlmgr not found. Please install TeX Live first.')

    def install_package_smart(self, package_name: str) -> bool:
        """Install a package, with fallback to dynamic resolution if not found."""
        if not self.exists():
            raise LuvError("No environment found. Run 'luv init' first.")

        print(f'Installing package: {package_name}')

        # First try direct installation
        if self._try_install_package(package_name):
            return True

        # If direct installation failed, try to resolve the package dynamically
        print(f'Package {package_name} not found, searching for it...')
        resolved_package = self.resolve_package_name(package_name)

        if resolved_package and resolved_package != package_name:
            print(f'Found {package_name} in package: {resolved_package}')
            return self._try_install_package(resolved_package)

        print(f'Could not resolve package for: {package_name}')
        return False

    def _try_install_package(self, package_name: str) -> bool:
        """Try to install a package using tlmgr."""
        # Ensure tlmgr user mode is set up
        self._setup_tlmgr_user_mode()

        # Set environment to use our local texmf directory
        env = os.environ.copy()
        env['TEXMFHOME'] = str(self.texmf_dir)

        try:
            # First try with --no-depends-at-all to avoid dependency and updmap issues
            cmd = [
                'tlmgr',
                '--usermode',
                'install',
                '--no-depends-at-all',
                package_name,
            ]
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )

            if result.returncode == 0:
                print(f'Successfully installed {package_name}')
                return True
            elif (
                'already installed' in result.stdout
                or 'already installed' in result.stderr
            ):
                print(f'Package {package_name} is already installed')
                return True

            # If that failed but package was installed (updmap error), check if files exist
            if 'updmap' in result.stderr and 'install:' in result.stdout:
                print(
                    f'Package {package_name} installed but font map update failed (this is usually OK)'
                )
                return True

            # If --no-depends-at-all failed for other reasons, try without special flags
            print(
                f'Trying alternative installation method for {package_name}...'
            )
            cmd = ['tlmgr', '--usermode', 'install', package_name]
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )

            if result.returncode == 0:
                print(f'Successfully installed {package_name}')
                return True
            elif (
                'already installed' in result.stdout
                or 'already installed' in result.stderr
            ):
                print(f'Package {package_name} is already installed')
                return True
            elif 'updmap' in result.stderr and 'install:' in result.stdout:
                print(
                    f'Package {package_name} installed but font map update failed (this is usually OK)'
                )
                return True

            # Installation failed
            if 'not present in repository' in result.stderr:
                return False  # Package not found, try dynamic resolution

            # Other errors
            print(f'Warning: Could not install {package_name}')
            print(f'tlmgr output: {result.stdout}')
            if result.stderr:
                print(f'tlmgr error: {result.stderr}')
            return False

        except FileNotFoundError:
            raise LuvError('tlmgr not found. Please install TeX Live first.')

    def install_package(self, package_name: str) -> None:
        """Install a LaTeX package to the local environment using tlmgr with smart resolution."""
        if not self.exists():
            raise LuvError("No environment found. Run 'luv init' first.")

        # Use smart installation with dynamic package resolution
        success = self.install_package_smart(package_name)

        if not success:
            print(
                f'Note: Package {package_name} may already be installed or not available in this TeX Live version.'
            )

    def remove_package(self, package_name: str) -> None:
        """Remove a LaTeX package from the local environment."""
        if not self.exists():
            raise LuvError("No environment found. Run 'luv init' first.")

        print(f'Removing package: {package_name}')

        # Set environment to use our local texmf directory
        env = os.environ.copy()
        env['TEXMFHOME'] = str(self.texmf_dir)

        try:
            # Remove package using tlmgr in user mode
            cmd = ['tlmgr', '--usermode', 'remove', package_name]
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )

            if result.returncode == 0:
                print(f'Successfully removed {package_name}')

                # Remove from requirements file
                requirements = self.get_requirements()
                if package_name in requirements:
                    updated_requirements = [
                        pkg for pkg in requirements if pkg != package_name
                    ]

                    with open(self.requirements_file, 'w') as f:
                        f.write('# LaTeX package requirements\n')
                        f.write("# Generated by 'luv resolve'\n\n")
                        for package in sorted(updated_requirements):
                            f.write(f'{package}\n')

                    print(f'Removed {package_name} from latex-requirements.txt')
                return
            elif (
                'not installed' in result.stdout
                or 'not installed' in result.stderr
            ):
                print(f'Package {package_name} is not installed')
                return
            else:
                print(f'Warning: Could not remove {package_name}')
                print(f'tlmgr output: {result.stdout}')
                if result.stderr:
                    print(f'tlmgr error: {result.stderr}')

        except FileNotFoundError:
            raise LuvError('tlmgr not found. Please install TeX Live first.')

    def clean(self) -> None:
        """Remove the entire LaTeX environment."""
        if not self.exists():
            raise LuvError('No environment found to clean')

        shutil.rmtree(self.luv_dir)
        print(f'Cleaned environment at {self.luv_dir}')

    def sync(self) -> None:
        """Install all packages from latex-requirements.txt."""
        requirements = self.get_requirements()

        if not requirements:
            print('No requirements found.')
            return

        print(f'Installing {len(requirements)} packages...')

        # Ensure tlmgr user mode is set up first
        self._setup_tlmgr_user_mode()

        failed_packages = []
        for package in requirements:
            # Handle version specifications (package==version or package>=version)
            package_name = (
                package.split('==')[0].split('>=')[0].split('<=')[0].strip()
            )
            try:
                self.install_package(package_name)
            except Exception as e:
                print(f'Failed to install {package_name}: {e}')
                failed_packages.append(package_name)

        if failed_packages:
            print(
                f'\nWarning: Failed to install {len(failed_packages)} packages:'
            )
            for pkg in failed_packages:
                print(f'  - {pkg}')
            print(
                '\nSome packages might have different names in TeX Live or be part of larger schemes.'
            )
        else:
            print('All packages installed successfully!')

    def compile(self, clean: bool = False) -> None:
        """Compile the LaTeX project."""
        if not self.exists():
            raise LuvError("No environment found. Run 'luv init' first.")

        config = self.get_config()
        project_config = config.get('project', {})

        texfile = project_config.get('texfile', 'main.tex')
        output_dir = project_config.get('output_dir', 'build')
        engine = project_config.get('engine', 'pdflatex')

        texfile_path = self.project_root / texfile
        if not texfile_path.exists():
            raise LuvError(f'TeX file not found: {texfile_path}')

        # Create output directory
        output_path = self.project_root / output_dir
        output_path.mkdir(exist_ok=True)

        if clean:
            print('Cleaning build directory...')
            for item in output_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        # Set TEXMFHOME to use our local packages
        env = os.environ.copy()
        env['TEXMFHOME'] = str(self.texmf_dir)

        print(f'Compiling {texfile} with {engine}...')

        # Detect if bibliography is needed
        has_bibliography = self._has_bibliography(texfile_path)
        has_citations = self._has_citations(texfile_path)

        if has_bibliography and has_citations:
            print('Bibliography detected, running full compilation sequence...')
            success = self._compile_with_bibliography(
                texfile, output_dir, engine, env
            )
        else:
            print('No bibliography detected, running single pass...')
            success = self._compile_single_pass(
                texfile, output_dir, engine, env
            )

        if not success:
            sys.exit(1)

    def _has_bibliography(self, texfile_path: Path) -> bool:
        """Check if the document uses bibliography."""
        try:
            with open(texfile_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Check for bibliography commands
            bib_patterns = [
                r'\\bibliography\{',
                r'\\bibliographystyle\{',
                r'\\printbibliography',
                r'\\addbibresource\{',
            ]

            for pattern in bib_patterns:
                if re.search(pattern, content):
                    return True

            # Check if .bib files exist
            bib_files = list(self.project_root.glob('*.bib'))
            return len(bib_files) > 0

        except Exception:
            return False

    def _has_citations(self, texfile_path: Path) -> bool:
        """Check if the document has citations."""
        try:
            with open(texfile_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Check for citation commands
            cite_patterns = [
                r'\\cite\{',
                r'\\citep\{',
                r'\\citet\{',
                r'\\citeauthor\{',
                r'\\autocite\{',
                r'\\textcite\{',
            ]

            for pattern in cite_patterns:
                if re.search(pattern, content):
                    return True

            return False

        except Exception:
            return False

    def _compile_single_pass(
        self, texfile: str, output_dir: str, engine: str, env: dict
    ) -> bool:
        """Run a single LaTeX compilation pass."""
        cmd = [
            engine,
            '-interaction=nonstopmode',
            f'-output-directory={output_dir}',
            str(texfile),
        ]

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                env=env,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                print('Compilation failed!')
                print('STDOUT:', result.stdout)
                print('STDERR:', result.stderr)
                return False
            else:
                # Check for warnings
                self._check_warnings(result.stdout)
                print(f'Compilation successful! Output in {output_dir}/')
                return True

        except FileNotFoundError:
            raise LuvError(
                f"LaTeX engine '{engine}' not found. Please install it first."
            )

    def _compile_with_bibliography(
        self, texfile: str, output_dir: str, engine: str, env: dict
    ) -> bool:
        """Run full compilation sequence with bibliography."""
        basename = texfile.replace('.tex', '')

        # First LaTeX pass
        print('  Pass 1: Initial compilation...')
        if not self._run_latex_pass(texfile, output_dir, engine, env):
            print('  Pass 1 failed, stopping compilation')
            return False
        print('  Pass 1 completed successfully')

        # Run BibTeX if .aux file exists and has citations
        aux_file = self.project_root / output_dir / f'{basename}.aux'
        if aux_file.exists():
            print('  Pass 2: Processing bibliography...')
            if not self._run_bibtex(basename, output_dir, env):
                print('  Warning: BibTeX failed, continuing...')
            else:
                print('  Pass 2 completed successfully')
        else:
            print('  No .aux file found, skipping BibTeX')

        # Second LaTeX pass (resolve bibliography)
        print('  Pass 3: Resolving references...')
        if not self._run_latex_pass(texfile, output_dir, engine, env):
            print('  Pass 3 failed, stopping compilation')
            return False
        print('  Pass 3 completed successfully')

        # Third LaTeX pass (resolve cross-references)
        print('  Pass 4: Final compilation...')
        result = self._run_latex_pass(
            texfile, output_dir, engine, env, final=True
        )

        if result:
            print('  Pass 4 completed successfully')
            print(f'Compilation successful! Output in {output_dir}/')
        else:
            print('  Pass 4 failed')

        return result

    def _run_latex_pass(
        self,
        texfile: str,
        output_dir: str,
        engine: str,
        env: dict,
        final: bool = False,
    ) -> bool:
        """Run a single LaTeX pass."""
        cmd = [
            engine,
            '-interaction=nonstopmode',
            f'-output-directory={output_dir}',
            str(texfile),
        ]

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                env=env,
                capture_output=True,
                text=True,
            )

            # Check if PDF was actually generated (more reliable than return code)
            basename = texfile.replace('.tex', '')
            pdf_file = self.project_root / output_dir / f'{basename}.pdf'

            if not pdf_file.exists():
                print(
                    f'LaTeX pass failed - no PDF generated (return code {result.returncode})'
                )
                print('STDOUT:', result.stdout[-1000:])  # Show last 1000 chars
                print('STDERR:', result.stderr[-1000:])  # Show last 1000 chars
                return False

            # PDF exists, but check for serious errors in output
            if (
                'Fatal error' in result.stdout
                or 'Emergency stop' in result.stdout
            ):
                print(
                    f'LaTeX pass failed with fatal error (return code {result.returncode})'
                )
                print('STDOUT:', result.stdout[-1000:])
                print('STDERR:', result.stderr[-1000:])
                return False

            if final:
                self._check_warnings(result.stdout)

            return True

        except FileNotFoundError:
            raise LuvError(
                f"LaTeX engine '{engine}' not found. Please install it first."
            )

    def _run_bibtex(self, basename: str, output_dir: str, env: dict) -> bool:
        """Run BibTeX on the auxiliary file."""
        cmd = ['bibtex', f'{output_dir}/{basename}.aux']

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,  # Run from project root, not build directory
                env=env,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                print(f'BibTeX failed with return code {result.returncode}')
                print('BibTeX STDOUT:', result.stdout[-500:])
                print('BibTeX STDERR:', result.stderr[-500:])
                return False

            return True

        except FileNotFoundError:
            print('BibTeX not found, skipping bibliography processing')
            return False

    def _check_warnings(self, stdout: str) -> None:
        """Check LaTeX output for common warnings and provide helpful advice."""
        warnings = []

        if 'undefined references' in stdout.lower():
            warnings.append(
                'Undefined references detected - check your \\cite{} commands'
            )

        if 'citation' in stdout.lower() and 'undefined' in stdout.lower():
            warnings.append(
                'Undefined citations - check your bibliography file and citation keys'
            )

        if 'rerun' in stdout.lower():
            warnings.append(
                'LaTeX suggests rerunning - this is normal for complex documents'
            )

        if 'multiply defined' in stdout.lower():
            warnings.append(
                'Multiply defined labels - check for duplicate \\label{} commands'
            )

        if warnings:
            print('\nWarnings detected:')
            for warning in warnings:
                print(f'  • {warning}')
            print(
                '\nTo debug: check the .log file in the build directory for details.'
            )


def find_project_root() -> Path | None:
    """Find the project root by looking for luv.toml."""
    current = Path.cwd()

    while current != current.parent:
        if (current / 'luv.toml').exists():
            return current
        current = current.parent

    return None


def main():
    parser = argparse.ArgumentParser(
        description='luv - LaTeX Universal Virtualizer\n\nA tool for managing LaTeX projects with isolated package environments.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  luv init                          # Initialize new LaTeX project
  luv init --texfile manuscript.tex # Initialize with custom tex file
  luv resolve                       # Analyze and show required packages (interactive)
  luv resolve --update              # Update latex-requirements.txt with found packages
  luv resolve --dry-run             # Show packages without updating or prompting
  luv sync                          # Install packages from latex-requirements.txt
  luv add amsmath hyperref          # Add packages to requirements and install
  luv compile                       # Compile the LaTeX project
  luv compile --clean               # Clean build and compile
  luv remove                        # Remove the LaTeX environment
  luv info                          # Show project information

Project Structure:
  luv.toml                          # Project configuration file
  latex-requirements.txt            # Package dependencies
  .luv/                             # Local LaTeX environment
  .luv/texmf/                       # Local TeX tree
        """,
    )

    subparsers = parser.add_subparsers(
        dest='command',
        # help='Available commands',
        # metavar='<command>'
    )

    # init command
    init_parser = subparsers.add_parser(
        'init',
        help='Initialize a new LaTeX environment in current directory',
        description='Initialize a new LaTeX project with isolated package environment. Creates luv.toml, latex-requirements.txt, and .luv/ directory structure.',
    )
    init_parser.add_argument(
        '--texfile',
        default='main.tex',
        metavar='FILE',
        help='Specify the main TeX file name (default: main.tex)',
    )
    init_parser.add_argument(
        '--engine',
        default='pdflatex',
        choices=['pdflatex', 'xelatex', 'lualatex', 'latex'],
        metavar='ENGINE',
        help='LaTeX compilation engine to use (default: pdflatex)',
    )

    # resolve command
    resolve_parser = subparsers.add_parser(
        'resolve',
        help='Analyze LaTeX files and resolve package dependencies',
        description='Scan LaTeX source files to detect required packages. Finds both explicitly declared packages (\\usepackage) and suggests packages based on usage patterns.',
    )
    resolve_parser.add_argument(
        '--update',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Automatically add all found packages to latex-requirements.txt without prompting',
    )
    resolve_parser.add_argument(
        '--dry-run',
        action=argparse.BooleanOptionalAction,
        default=False,
        help='Show analysis results without updating files or prompting for changes',
    )

    # sync command
    subparsers.add_parser(
        'sync',
        help='Install all packages listed in latex-requirements.txt',
        description='Download and install all packages specified in latex-requirements.txt to the local environment.',
    )

    # add command
    add_parser = subparsers.add_parser(
        'add',
        help='Add packages to requirements and install them',
        description='Add one or more packages to latex-requirements.txt and install them to the local environment.',
    )
    add_parser.add_argument(
        'packages',
        nargs='+',
        metavar='PACKAGE',
        help='One or more LaTeX package names to add and install',
    )

    # compile command
    compile_parser = subparsers.add_parser(
        'compile',
        help='Compile the LaTeX project using the local environment',
        description='Compile the main TeX file using the configured engine and local package environment.',
    )
    compile_parser.add_argument(
        '--clean',
        action=argparse.BooleanOptionalAction,
        default=False,
        help='Remove all files from build directory before compilation',
    )

    # remove command
    remove_parser = subparsers.add_parser(
        'remove',
        help='Remove packages from the local environment',
        description='Remove one or more packages from latex-requirements.txt and uninstall them from the local environment.',
    )
    remove_parser.add_argument(
        'packages',
        nargs='+',
        metavar='PACKAGE',
        help='One or more LaTeX package names to remove',
    )

    # clean command
    subparsers.add_parser(
        'clean',
        help='Remove the entire local LaTeX environment',
        description='Delete the .luv/ directory and all locally installed packages. This does not affect luv.toml or latex-requirements.txt.',
    )

    # info command
    subparsers.add_parser(
        'info',
        help='Display project configuration and package information',
        description='Show current project settings, installed packages, and environment status.',
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == 'init':
            # Initialize in current directory
            project_root = Path.cwd()
            env = LaTeXEnvironment(project_root)
            env.create()

            # Update config with specified texfile and engine
            if args.texfile != 'main.tex' or args.engine != 'pdflatex':
                config = env.get_config()
                config['project']['texfile'] = args.texfile
                config['project']['engine'] = args.engine
                env.update_config(config)
                print(f'Set texfile to {args.texfile}')
                if args.engine != 'pdflatex':
                    print(f'Set engine to {args.engine}')

        else:
            # Find project root for other commands
            project_root = find_project_root()
            if not project_root:
                raise LuvError(
                    "No luv.toml found. Run 'luv init' to create a new project."
                )

            env = LaTeXEnvironment(project_root)

            if args.command == 'resolve':
                if args.dry_run:
                    env.resolve_dependencies(
                        update_requirements=False, interactive=False
                    )
                elif args.update:
                    env.resolve_dependencies(
                        update_requirements=True, interactive=False
                    )
                else:
                    # Default: interactive mode with no explicit update setting
                    env.resolve_dependencies(
                        update_requirements=None, interactive=True
                    )

            elif args.command == 'sync':
                env.sync()

            elif args.command == 'add':
                resolver = PackageResolver(project_root)
                for package in args.packages:
                    # Resolve package name using tlmgr
                    resolved_name = resolver.resolve_package_name(package)

                    # Skip core packages
                    if resolved_name is None:
                        print(
                            f'Skipping {package} (core LaTeX package, no installation needed)'
                        )
                        continue

                    env.install_package(resolved_name)

                    # Add resolved package name to requirements file
                    requirements = env.get_requirements()
                    if resolved_name not in requirements:
                        with open(env.requirements_file, 'a') as f:
                            f.write(f'{resolved_name}\n')
                        print(
                            f'Added {resolved_name} to latex-requirements.txt'
                        )

            elif args.command == 'remove':
                resolver = PackageResolver(project_root)
                for package in args.packages:
                    # Resolve package name before removal
                    resolved_name = resolver.resolve_package_name(package)

                    # Skip core packages
                    if resolved_name is None:
                        print(
                            f'Skipping {package} (core LaTeX package, cannot be removed)'
                        )
                        continue

                    env.remove_package(resolved_name)

            elif args.command == 'clean':
                env.clean()

            elif args.command == 'compile':
                env.compile(clean=args.clean)

            elif args.command == 'remove':
                env.remove()

            elif args.command == 'info':
                config = env.get_config()
                requirements = env.get_requirements()

                print(f'Project root: {project_root}')
                print(f'Environment: {env.luv_dir}')
                print(
                    f'TeX file: {config.get("project", {}).get("texfile", "main.tex")}'
                )
                print(
                    f'Engine: {config.get("project", {}).get("engine", "pdflatex")}'
                )
                print(
                    f'Output dir: {config.get("project", {}).get("output_dir", "build")}'
                )
                print(f'Packages: {len(requirements)} installed')
                if requirements:
                    for pkg in requirements:
                        print(f'  - {pkg}')

    except LuvError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nOperation cancelled.')
        sys.exit(1)
    except Exception as e:
        print(f'Unexpected error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
