#!/usr/bin/env python3
"""
luv - LaTeX Universal Virtualizer
A tool for managing LaTeX projects with isolated package environments.

Copyright (C) 2025 Skylar Gay
"""

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import tomli_w
from rich.logging import RichHandler
from rich_argparse import RawDescriptionRichHelpFormatter


class RichConditionalLevelFormatter(logging.Formatter):
    def format(self, record):
        level = record.levelno

        if level >= logging.ERROR:
            # Red, bold
            record.msg = (
                f'[bold red][{record.levelname}][/bold red] {record.msg}'
            )
        elif level >= logging.WARNING:
            # Yellow, bold
            record.msg = (
                f'[bold yellow][{record.levelname}][/bold yellow] {record.msg}'
            )
        # INFO and DEBUG are left unchanged (no level shown)
        return super().format(record)


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.propagate = False
log_format = RichConditionalLevelFormatter('%(message)s')
console_handler = RichHandler(
    show_time=False, show_path=False, show_level=False, markup=True
)
console_handler.setFormatter(log_format)
logger.addHandler(console_handler)


class PackageResolver:
    """Resolves LaTeX package dependencies from source files using tlmgr search."""

    # Only core packages that should be skipped (already part of LaTeX base)
    CORE_PACKAGES: frozenset[str] = frozenset(
        {
            'fontenc',
            'inputenc',
            'textcomp',
            'ifthen',
            'calc',
            'url',
        }
    )

    # Common LaTeX packages and their typical command/environment indicators
    PACKAGE_PATTERNS: dict[str, list[str]] = {
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
        self.found_packages: set[str] = set()
        self.explicitly_used: set[str] = set()

        # Compile regex patterns once for better performance
        self._usepackage_pattern = re.compile(
            r'\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}'
        )
        self._include_patterns = [
            re.compile(pattern)
            for pattern in [
                r'\\input\{([^}]+)\}',
                r'\\include\{([^}]+)\}',
                r'\\subfile\{([^}]+)\}',
                r'\\InputIfFileExists\{([^}]+)\}',
            ]
        ]
        self._compiled_package_patterns: dict[str, list[re.Pattern]] = {
            package: [re.compile(pattern) for pattern in patterns]
            for package, patterns in self.PACKAGE_PATTERNS.items()
        }

    @lru_cache(maxsize=128)
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

        logger.info(f'Resolving {len(all_packages)} packages using tlmgr...')

        mw = min(8, max(1, len(all_packages)))
        with ThreadPoolExecutor(max_workers=mw) as executor:
            package_futures = {
                executor.submit(self.resolve_package_name, package): package
                for package in all_packages
            }

            for future in package_futures:
                package = package_futures[future]
                try:
                    tex_live_name = future.result()
                    if tex_live_name is not None:
                        resolved_packages.add(tex_live_name)
                        if tex_live_name != package:
                            logger.info(f'  {package} → {tex_live_name}')
                except Exception as e:
                    logger.warning(
                        f'  Warning: Failed to resolve {package}: {e}'
                    )

        # Add automatic dependencies based on detected packages
        all_packages = self.found_packages | self.explicitly_used

        # biblatex automatically requires these packages
        if 'biblatex' in all_packages:
            self.found_packages.add('logreq')
            self.found_packages.add('etoolbox')
            logger.info('  Added biblatex dependencies: logreq, etoolbox')

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
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return

        # Find \\usepackage declarations
        matches = self._usepackage_pattern.findall(content)

        for match in matches:
            # Handle multiple packages in one declaration: {pkg1,pkg2,pkg3}
            packages = [pkg.strip() for pkg in match.split(',')]
            # Filter out packages that have local .sty files in the project
            filtered_packages = [
                pkg
                for pkg in packages
                if not (self.project_root / f'{pkg}.sty').exists()
            ]
            self.explicitly_used.update(filtered_packages)

        # Find included files
        for pattern in self._include_patterns:
            matches = pattern.findall(content)
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
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return

        # Check each package pattern
        for (
            package,
            compiled_patterns,
        ) in self._compiled_package_patterns.items():
            if (
                package not in self.explicitly_used
            ):  # Only suggest if not already declared
                # Skip if local .sty file exists
                if (self.project_root / f'{package}.sty').exists():
                    continue
                if any(
                    pattern.search(content) for pattern in compiled_patterns
                ):
                    self.found_packages.add(package)

        # Find included files
        for pattern in self._include_patterns:
            matches = pattern.findall(content)
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

        logger.info(f'Creating LaTeX environment at {self.luv_dir}')

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

        logger.info('Environment created successfully!')

    def _create_initial_config(self) -> None:
        """Create initial luv.toml configuration."""
        config = {
            'project': {
                'texfile': 'main.tex',
                'output_dir': 'build',
                'engine': 'pdflatex',
            }
        }

        with open(self.config_file, 'wb') as f:
            tomli_w.dump(config, f)

        logger.info(f'Created {self.config_file}')

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

        self.requirements_file.write_text('\n'.join(initial_packages))
        logger.info(f'Created {self.requirements_file}')

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
        with open(self.config_file, 'wb') as f:
            tomli_w.dump(config, f)

    def get_requirements(self) -> list[str]:
        """Load requirements from latex-requirements.txt."""
        if not self.requirements_file.exists():
            return []

        requirements = []
        for line in self.requirements_file.read_text(
            encoding='utf-8'
        ).splitlines():
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

        logger.info(f'Analyzing {texfile} and included files...')

        resolver = PackageResolver(self.project_root)
        packages = resolver.resolve_dependencies(texfile)

        # Get current requirements to see what's missing
        existing_requirements = self.get_requirements()
        missing_packages = set(packages) - set(existing_requirements)

        logger.info(f'\nFound {len(packages)} installable packages:')
        if resolver.explicitly_used:
            logger.info('Explicitly declared packages:')
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
                        logger.info(f'  {status} {display}')

        suggested_packages = resolver.found_packages - resolver.explicitly_used
        if suggested_packages:
            logger.info('\nSuggested packages (based on usage patterns):')
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
                        logger.info(f'  {status} {display}')

        # Show summary of what's missing from requirements
        if missing_packages:
            logger.info(
                f'\n{len(missing_packages)} packages not in latex-requirements.txt:'
            )
            for pkg in sorted(missing_packages):
                logger.info(f'  - {pkg}')

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
                logger.info('\nSkipping update.')
                should_update = False
        elif missing_packages:
            # Non-interactive mode with missing packages
            logger.info(
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

        self.requirements_file.write_text(
            '# LaTeX package requirements\n'
            "# Generated by 'luv resolve'\n\n"
            + '\n'.join(sorted(all_packages))
            + '\n'
        )

        logger.info(
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
                logger.warning(
                    f'Warning: tlmgr init-usertree returned: {result.stderr}'
                )

            self._tlmgr_initialized = True

        except FileNotFoundError:
            raise LuvError('tlmgr not found. Please install TeX Live first.')

    def install_package_smart(self, package_name: str) -> bool:
        """Install a package, with fallback to dynamic resolution if not found."""
        if not self.exists():
            raise LuvError("No environment found. Run 'luv init' first.")

        logger.info(f'Installing package: {package_name}')

        # First try direct installation
        if self._try_install_package(package_name):
            return True

        # If direct installation failed, try to resolve the package dynamically
        logger.info(f'Package {package_name} not found, searching for it...')
        resolver = PackageResolver(self.project_root)
        resolved_package = resolver.resolve_package_name(package_name)

        if resolved_package and resolved_package != package_name:
            logger.info(f'Found {package_name} in package: {resolved_package}')
            return self._try_install_package(resolved_package)

        logger.warning(f'Could not resolve package for: {package_name}')
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
                return True
            elif (
                'already installed' in result.stdout
                or 'already installed' in result.stderr
            ):
                logger.info(f'Package {package_name} is already installed')
                return True

            # If that failed but package was installed (updmap error), check if files exist
            if 'updmap' in result.stderr and 'install:' in result.stdout:
                logger.info(
                    f'Package {package_name} installed but font map update failed (this is usually OK)'
                )
                return True

            # If --no-depends-at-all failed for other reasons, try without special flags
            logger.info(
                f'Trying alternative installation method for {package_name}...'
            )
            cmd = ['tlmgr', '--usermode', 'install', package_name]
            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )

            if result.returncode == 0:
                logger.info(f'Successfully installed {package_name}')
                return True
            elif (
                'already installed' in result.stdout
                or 'already installed' in result.stderr
            ):
                logger.info(f'Package {package_name} is already installed')
                return True
            elif 'updmap' in result.stderr and 'install:' in result.stdout:
                logger.info(
                    f'Package {package_name} installed but font map update failed (this is usually OK)'
                )
                return True

            # Installation failed
            if 'not present in repository' in result.stderr:
                return False  # Package not found, try dynamic resolution

            # Other errors
            logger.warning(f'Warning: Could not install {package_name}')
            logger.warning(f'tlmgr output: {result.stdout}')
            if result.stderr:
                logger.warning(f'tlmgr error: {result.stderr}')
            return False

        except FileNotFoundError:
            raise LuvError('tlmgr not found. Please install TeX Live first.')

    def install_package(self, package_name: str) -> None:
        """Install a LaTeX package to the local environment using tlmgr with smart resolution."""
        if not self.exists():
            logger.error("No environment found. Run 'luv init' first.")
            return

        # Use smart installation with dynamic package resolution
        success = self.install_package_smart(package_name)

        if not success:
            logger.warning(
                f'Note: Package {package_name} may already be installed or not available in this TeX Live version.'
            )

    def remove_package(self, package_name: str) -> None:
        """Remove a LaTeX package from the local environment."""
        if not self.exists():
            logger.error("No environment found. Run 'luv init' first.")
            return

        logger.info(f'Removing package: {package_name}')

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
                logger.info(f'Successfully removed {package_name}')

                # Remove from requirements file
                requirements = self.get_requirements()
                if package_name in requirements:
                    updated_requirements = [
                        pkg for pkg in requirements if pkg != package_name
                    ]

                    self.requirements_file.write_text(
                        '# LaTeX package requirements\n'
                        "# Generated by 'luv resolve'\n\n"
                        + '\n'.join(sorted(updated_requirements))
                        + '\n'
                    )

                    logger.info(
                        f'Removed {package_name} from latex-requirements.txt'
                    )
                return
            elif (
                'not installed' in result.stdout
                or 'not installed' in result.stderr
            ):
                logger.info(f'Package {package_name} is not installed')
                return
            else:
                logger.warning(f'Warning: Could not remove {package_name}')
                logger.warning(f'tlmgr output: {result.stdout}')
                if result.stderr:
                    logger.warning(f'tlmgr error: {result.stderr}')

        except FileNotFoundError:
            raise LuvError('tlmgr not found. Please install TeX Live first.')

    def clean(self) -> None:
        """Remove the entire LaTeX environment."""
        if not self.exists():
            logger.warning('No environment found to clean')
            return

        shutil.rmtree(self.luv_dir)
        logger.info(f'Cleaned environment at {self.luv_dir}')

    def sync(self) -> None:
        """Install all packages from latex-requirements.txt."""
        requirements = self.get_requirements()

        if not requirements:
            logger.info('No requirements found.')
            return

        logger.info(f'Installing {len(requirements)} packages...')

        # Ensure tlmgr user mode is set up first
        self._setup_tlmgr_user_mode()

        # Extract package names (handle version specifications)
        package_names = [
            package.split('==')[0].split('>=')[0].split('<=')[0].strip()
            for package in requirements
        ]

        failed_packages = []
        mw = min(4, max(1, len(package_names)))
        with ThreadPoolExecutor(max_workers=mw) as executor:
            package_futures = {
                executor.submit(
                    self.install_package_smart, package_name
                ): package_name
                for package_name in package_names
            }

            for future in package_futures:
                package_name = package_futures[future]
                try:
                    success = future.result()
                    if not success:
                        failed_packages.append(package_name)
                except Exception as e:
                    logger.warning(f'Failed to install {package_name}: {e}')
                    failed_packages.append(package_name)

        if failed_packages:
            logger.warning(
                f'\nWarning: Failed to install {len(failed_packages)} packages:'
            )
            for pkg in failed_packages:
                logger.warning(f'  - {pkg}')
            logger.warning(
                '\nSome packages might have different names in TeX Live or be part of larger schemes.'
            )
        else:
            logger.info('All packages installed successfully!')

    def compile(self, clean: bool = False) -> None:
        """Compile the LaTeX project."""
        if not self.exists():
            logger.error("No environment found. Run 'luv init' first.")
            return

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
            logger.info('Cleaning build directory...')
            shutil.rmtree(output_path)
            os.makedirs(output_path)

        # Set TEXMFHOME to use our local packages
        env = os.environ.copy()
        env['TEXMFHOME'] = str(self.texmf_dir)

        logger.info(f'Compiling {texfile} with {engine}...')

        # Detect if bibliography is needed
        has_bibliography = self._has_bibliography(texfile_path)
        has_citations = self._has_citations(texfile_path)
        bibliography_backend = self._get_bibliography_backend(texfile_path)

        if has_bibliography and has_citations:
            logger.info(
                f'Bibliography detected ({bibliography_backend}), running full compilation sequence...'
            )
            success = self._compile_with_bibliography(
                texfile, output_dir, engine, env, bibliography_backend
            )
        else:
            logger.info('No bibliography detected, running single pass...')
            success = self._compile_single_pass(
                texfile, output_dir, engine, env
            )

        if not success:
            sys.exit(1)

    def _has_bibliography(self, texfile_path: Path) -> bool:
        """Check if the document uses bibliography."""
        try:
            content = texfile_path.read_text(encoding='utf-8', errors='ignore')

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

    def _get_bibliography_backend(self, texfile_path: Path) -> str:
        """Determine which bibliography backend to use (biber or bibtex)."""
        try:
            content = texfile_path.read_text(encoding='utf-8', errors='ignore')

            # Check for biblatex with backend specification
            biblatex_pattern = r'\\usepackage\[([^\]]*)\]\{biblatex\}'
            matches = re.findall(biblatex_pattern, content)

            for match in matches:
                if 'backend=biber' in match:
                    return 'biber'
                elif 'backend=bibtex' in match:
                    return 'bibtex'

            # Check if biblatex is used without explicit backend (defaults to biber)
            if re.search(r'\\usepackage.*\{biblatex\}', content):
                return 'biber'

            # Check for traditional bibtex commands
            if re.search(r'\\bibliographystyle\{', content):
                return 'bibtex'

            # Default fallback
            return 'bibtex'

        except Exception:
            return 'bibtex'

    def _has_citations(self, texfile_path: Path) -> bool:
        """Check if the document has citations."""
        try:
            content = texfile_path.read_text(encoding='utf-8', errors='ignore')

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
                logger.error('Compilation failed!')
                logger.error(f'STDOUT: {result.stdout}')
                logger.error(f'STDERR: {result.stderr}')
                return False
            else:
                self._check_warnings(result.stdout)
                logger.info(f'Compilation successful! Output in {output_dir}/')
                return True

        except FileNotFoundError:
            raise LuvError(
                f"LaTeX engine '{engine}' not found. Please install it first."
            )

    def _compile_with_bibliography(
        self,
        texfile: str,
        output_dir: str,
        engine: str,
        env: dict,
        backend: str = 'bibtex',
    ) -> bool:
        """Run full compilation sequence with bibliography."""
        basename = texfile.replace('.tex', '')

        # First LaTeX pass
        logger.info('  Pass 1: Initial compilation...')
        if not self._run_latex_pass(texfile, output_dir, engine, env):
            logger.warning('  Pass 1 failed, stopping compilation')
            return False
        logger.info('  Pass 1 completed successfully')

        # Run bibliography processor if .aux file exists and has citations
        aux_file = self.project_root / output_dir / f'{basename}.aux'
        if aux_file.exists():
            logger.info(f'  Pass 2: Processing bibliography ({backend})...')
            if backend == 'biber':
                success = self._run_biber(basename, output_dir, env)
            else:
                success = self._run_bibtex(basename, output_dir, env)

            if not success:
                logger.warning(f'  Warning: {backend} failed, continuing...')
            else:
                logger.info('  Pass 2 completed successfully')
        else:
            logger.info(
                '  No .aux file found, skipping bibliography processing'
            )

        # Second LaTeX pass (resolve bibliography)
        logger.info('  Pass 3: Resolving references...')
        if not self._run_latex_pass(texfile, output_dir, engine, env):
            logger.warning('  Pass 3 failed, stopping compilation')
            return False
        logger.info('  Pass 3 completed successfully')

        # Third LaTeX pass (resolve cross-references)
        logger.info('  Pass 4: Final compilation...')
        result = self._run_latex_pass(
            texfile, output_dir, engine, env, final=True
        )

        if result:
            logger.info('  Pass 4 completed successfully')
            logger.info(f'Compilation successful! Output in {output_dir}/')
        else:
            logger.warning('  Pass 4 failed')

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
                logger.error(
                    f'LaTeX compilation failed - no PDF generated (exit code: {result.returncode})'
                )
                if result.stdout:
                    logger.error('Last 1000 characters of output:')
                    logger.error(result.stdout[-1000:])
                if result.stderr:
                    logger.error('Error output:')
                    logger.error(result.stderr[-1000:])
                return False

            # PDF exists, but check for serious errors in output
            if (
                'Fatal error' in result.stdout
                or 'Emergency stop' in result.stdout
            ):
                logger.error(
                    f'LaTeX compilation failed with fatal error (exit code: {result.returncode})'
                )
                if result.stdout:
                    logger.error('Last 1000 characters of output:')
                    logger.error(result.stdout[-1000:])
                if result.stderr:
                    logger.error('Error output:')
                    logger.error(result.stderr[-1000:])
                return False

            if final:
                self._check_warnings(result.stdout)

            return True

        except FileNotFoundError:
            raise LuvError(
                f"LaTeX engine '{engine}' not found. Please install it first."
            )

    def _run_biber(self, basename: str, output_dir: str, env: dict) -> bool:
        """Run Biber on the auxiliary file."""
        # Try different biber commands in order of preference
        biber_commands = [
            'biber',  # System biber
            f'{self.texmf_dir}/scripts/biber/biber.pl',  # Local biber script
            f'{self.texmf_dir}/bin/biber',  # Local biber binary
        ]

        for biber_cmd in biber_commands:
            try:
                cmd = [biber_cmd, f'{output_dir}/{basename}']
                result = subprocess.run(
                    cmd,
                    cwd=self.project_root,
                    env=env,
                    capture_output=True,
                    text=True,
                )

                if result.returncode == 0:
                    return True
                else:
                    logger.warning(
                        f'Biber failed with return code {result.returncode}'
                    )
                    if result.stdout:
                        logger.error('Last 1000 characters of Biber output:')
                        logger.error(result.stdout[-1000:])
                    if result.stderr:
                        logger.error('Error output:')
                        logger.error(result.stderr[-1000:])

                    return False

            except FileNotFoundError:
                continue  # Try next biber command

        # If no biber found, fall back to BibTeX

        logger.warning(
            """Biber not found in any location, falling back to BibTeX
WARNING: Your document uses biblatex with biber backend, but biber is not installed.

Please install biber on your system:
  • macOS: `brew install biber`
  • Ubuntu/Debian: `sudo apt install biber`
  • Windows: Install from CTAN or use TeX Live Manager
  • Or ensure biber is included in your TeX Live installation
Falling back to BibTeX may not work correctly with biblatex."""
        )

        return self._run_bibtex(basename, output_dir, env)

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
                logger.warning(
                    f'BibTeX failed with return code {result.returncode}'
                )
                if result.stdout:
                    logger.error('Last 1000 characters of BibTeX output:')
                    logger.error(result.stdout[-1000:])
                if result.stderr:
                    logger.error('Error output:')
                    logger.error(result.stderr[-1000:])
                return False

            return True

        except FileNotFoundError:
            logger.warning('BibTeX not found, skipping bibliography processing')
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
            logger.warning('\nWarnings detected:')
            for warning in warnings:
                logger.warning(f'  • {warning}')
            logger.warning(
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
        formatter_class=RawDescriptionRichHelpFormatter,
        epilog="""
Examples:
  luv init                          # Initialize new LaTeX project
  luv init --texfile manuscript.tex # Initialize with custom tex file
  luv resolve                       # Analyze and update latex-requirements.txt with found packages
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
                logger.info(f'Set texfile to {args.texfile}')
                if args.engine != 'pdflatex':
                    logger.info(f'Set engine to {args.engine}')

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
                        logger.info(
                            f'Skipping {package} (core LaTeX package, no installation needed)'
                        )
                        continue

                    env.install_package(resolved_name)

                    # Add resolved package name to requirements file
                    requirements = env.get_requirements()
                    if resolved_name not in requirements:
                        env.requirements_file.write_text(
                            env.requirements_file.read_text()
                            + f'{resolved_name}\n'
                        )
                        logger.info(
                            f'Added {resolved_name} to latex-requirements.txt'
                        )

            elif args.command == 'remove':
                resolver = PackageResolver(project_root)
                for package in args.packages:
                    # Resolve package name before removal
                    resolved_name = resolver.resolve_package_name(package)

                    # Skip core packages
                    if resolved_name is None:
                        logger.info(
                            f'Skipping {package} (core LaTeX package, cannot be removed)'
                        )
                        continue

                    env.remove_package(resolved_name)

            elif args.command == 'clean':
                env.clean()

            elif args.command == 'compile':
                env.compile(clean=args.clean)

            elif args.command == 'info':
                config = env.get_config()
                requirements = env.get_requirements()

                logger.info(
                    f"""Project root: {project_root}
Environment: {env.luv_dir}
TeX file: {config.get('project', {}).get('texfile', 'main.tex')}
Engine: {config.get('project', {}).get('engine', 'pdflatex')}
Output dir: {config.get('project', {}).get('output_dir', 'build')}
Packages: {len(requirements)} installed"""
                )
                if requirements:
                    for pkg in requirements:
                        logger.info(f'  - {pkg}')
    except LuvError as e:
        logger.error(f'Error: {e}', exc_info=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info('\nOperation cancelled.')
        sys.exit(1)
    except Exception as e:
        logger.error(f'Unexpected error: {e}', exc_info=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
