# LUV - LaTeX Universal Virtualizer

`luv` is a tool for managing LaTeX projects with isolated package environments, similar to Python virtual environments. It provides reproducible, project-specific LaTeX package management using TeX Live's `tlmgr` in user mode.

## Goals

1. **Reproducibility**: Generate consistent LaTeX builds across different machines and environments
2. **Isolation**: LaTeX packages are installed per-project, preventing version conflicts between projects
3. **Simplicity**: Easy-to-use commands inspired by modern package managers

`luv` is inspired by these amazing tools:

- [uv](https://docs.astral.sh/uv/) - Fast Python package installer and resolver
- [TinyTeX](https://yihui.org/tinytex/) - Lightweight LaTeX distribution

*DISCLAIMER: In the current form, this is a fun weekend project and is very early stage.*

## Prerequisites

- TeX Live distribution (TinyTeX recommended)
- Python 3.11 or later (for running luv)

Install TinyTeX:

```bash
# macOS/Linux
curl -sL "https://yihui.org/tinytex/install-bin-unix.sh" | sh

# Windows
# See https://yihui.org/tinytex/

```

Eventually I would like the tool to install TeX Live itself, much like
`uv python install` can install different versions of Python.

## Installation

```bash
# Clone the repository
git clone https://github.com/StellarStorm/luv.git
cd luv

# Install luv (development mode)
uv tool install -e .
# OR
pip install -e .
```

## Quick Start

```bash
# Initialize a new LaTeX project
luv init

# Analyze your LaTeX files and detect required packages
luv resolve

# Install all packages listed in latex-requirements.txt
luv sync

# Compile your LaTeX document
luv compile

# Add a new package to your project
luv add hyperref

# Show project information
luv info
```

## Commands

### `luv init`

Initialize a new LaTeX environment in the current directory.

```bash
luv init                          # Use main.tex as main file
luv init --texfile manuscript.tex # Specify custom main file
luv init --engine xelatex         # Use XeLaTeX engine
```

**Options:**

- `--texfile FILE`: Main TeX file name (default: `main.tex`)
- `--engine ENGINE`: LaTeX engine (`pdflatex`, `xelatex`, `lualatex`, `latex`)

**Creates:**

- `luv.toml` - Project configuration
- `latex-requirements.txt` - Package dependencies
- `.luv/` - Local LaTeX environment directory

### `luv resolve`

Analyze LaTeX source files to detect required packages automatically.

```bash
luv resolve              # Interactive mode - ask before updating requirements
luv resolve --update     # Automatically update latex-requirements.txt
luv resolve --dry-run    # Show analysis without making changes
```

**Features:**

- Scans `\usepackage{}` declarations in all project files
- Detects package usage patterns (e.g., `\includegraphics` → `graphicx`)
- Maps LaTeX package names to TeX Live package names
- Excludes local `.sty` files and core LaTeX packages
- Recursively analyzes included files (`\input`, `\include`, etc.)

### `luv sync`

Install all packages listed in `latex-requirements.txt`.

```bash
luv sync
```

Uses `tlmgr --usermode` to install packages to the project-local environment.

### `luv add`

Add packages to requirements and install them immediately.

```bash
luv add amsmath hyperref    # Add multiple packages
luv add amsfonts            # Add single package
```

### `luv compile`

Compile the LaTeX project using the local environment.

```bash
luv compile              # Standard compilation
luv compile --clean      # Clean build directory first
```

**Features:**

- Automatically detects bibliography requirements
- Runs appropriate compilation sequence (BibTeX if needed)
- Uses project-local packages via `TEXMFHOME`
- Provides helpful error messages and warnings

### `luv remove`

Remove packages from requirements and uninstall them from the local environment.

```bash
luv remove amsmath hyperref    # Remove multiple packages
luv remove tikz               # Remove single package
```

Automatically removes packages from both the local environment and `latex-requirements.txt`.

### `luv clean`

Remove the entire local LaTeX environment (`.luv/` directory).

```bash
luv clean
```

Preserves `luv.toml` and `latex-requirements.txt`.

### `luv info`

Display project configuration and package information.

```bash
luv info
```

## Project Structure

After running `luv init`, your project will have:

```text
your-project/
├── luv.toml                    # Project configuration
├── latex-requirements.txt     # Package dependencies
├── main.tex                   # Your LaTeX document
├── .luv/                      # Local environment (auto-generated)
│   ├── texmf/                 # Local TeX tree
│   │   └── tex/latex/         # Installed packages
│   └── bin/                   # Local binaries
└── build/                     # Compilation output (auto-generated)
```

## Configuration

### `luv.toml`

Project configuration file:

```toml
[project]
texfile = "main.tex"      # Main TeX file
output_dir = "build"      # Compilation output directory
engine = "pdflatex"       # LaTeX engine
```

### `latex-requirements.txt`

Package dependencies file:

```text
# LaTeX package requirements
# Generated by 'luv resolve'

amsmath
graphicx
hyperref
booktabs
```

Supports comments and can be edited manually.

## Package Name Mapping

`luv` automatically maps LaTeX package names to TeX Live package names.
For example:

| LaTeX Package | TeX Live Package | Notes |
|---------------|------------------|-------|
| `algorithmic` | `algorithms` | |
| `amssymb` | `amsfonts` | |
| `graphicx` | `graphics` | |
| `textcomp` | (core) | Part of base LaTeX |
| `fontenc` | (core) | Part of base LaTeX |
| `subcaption` | `caption` | |

Core LaTeX packages are automatically excluded from requirements since they're part of the base system.

## Troubleshooting

### Package Installation Issues

If `tlmgr` fails to install a package:

1. **Package not found**: The package might have a different name in TeX Live or be part of a larger package collection
2. **Permission errors**: Ensure you have write permissions to the project directory
3. **Network issues**: Check your internet connection for downloading packages

### Font Map Warnings

Font map update failures are usually harmless:

```text
Package installed but font map update failed (this is usually OK)
```

This commonly occurs with TinyTeX and doesn't affect compilation.

### Missing Dependencies

If compilation fails due to missing packages:

1. Run `luv resolve` to detect missing packages
2. Run `luv sync` to install them
3. Check the compilation log in `build/` for specific errors

## Examples

### Basic Academic Paper

```bash
# Set up project
luv init --texfile paper.tex

# Edit paper.tex to include your content with packages like:
# \usepackage{amsmath,amssymb,graphicx,hyperref,natbib}

# Detect and install required packages
luv resolve
luv sync

# Compile
luv compile
```

### Complex Document with TikZ

```bash
luv init --texfile thesis.tex --engine xelatex
luv add pgf
luv compile
```

### Collaborative Project

Share your project configuration:

```bash
# Other collaborators can reproduce your environment
git clone your-project.git
cd your-project
luv sync    # Install all required packages
luv compile # Build the document
```

## Contributing

This project is in early development. Contributions welcome!

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

MIT license, copyright (C) 2025 Skylar Gay. See LICENSE.md for details.

## Acknowledgments

- TeX Live team for `tlmgr`
- TinyTeX project for inspiration
- uv project for the excellent CLI design patterns
