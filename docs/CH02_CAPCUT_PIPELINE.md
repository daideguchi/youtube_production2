# CH02 CapCut Pipeline Documentation

## Overview

This document describes the CH02 (哲学系) SRT to CapCut draft pipeline. The pipeline has been successfully restructured to eliminate PYTHONPATH dependencies and enable reliable operation from any directory.

## Prerequisites

- Python 3.9+
- Virtual environment with required dependencies
- Access tokens for LLM services (as configured in .env)
- CH02 SRT files in the expected input directory

## Installation

1. Clone or access the repository:
   ```bash
   cd /path/to/factory_commentary
   ```

2. Activate the virtual environment:
   ```bash
   source .venv/bin/activate
   ```

3. Install the package in development mode:
   ```bash
   pip install -e .
   ```

## Single File Processing

To process a single CH02 SRT file:

```bash
# Basic processing
factory-ch02 CH02 commentary_02_srt2images_timeline/input/CH02_哲学系/CH02-0XX.srt

# Check mode (dry-run)
factory-ch02 CH02 commentary_02_srt2images_timeline/input/CH02_哲学系/CH02-0XX.srt check

# Resume from existing images (skip generation)
factory-ch02 CH02 commentary_02_srt2images_timeline/input/CH02_哲学系/CH02-0XX.srt draft
```

## Batch Processing

For batch processing multiple files, see the batch processing scripts in the `scripts/` directory.

## Configuration

### Environment Variables

Ensure your `.env` file contains the necessary API keys and configuration:

```bash
# LLM Services
GEMINI_API_KEY=your_api_key
OPENAI_API_KEY=your_api_key
AZURE_OPENAI_API_KEY=your_api_key
AZURE_OPENAI_ENDPOINT=your_endpoint

# CapCut & Project Settings
# (See .env.example for complete list)
```

### CLI Options

- `--concurrency N`: Control image generation concurrency
- `--title "Custom Title"`: Override auto-generated title
- `--labels "label1,label2,label3,label4"`: Custom belt labels

## Project Structure

After the restructuring:

```
factory_commentary/                    # Root project directory
├── pyproject.toml                     # Package configuration
├── .env                              # Environment variables
├── .env.example                       # Template for .env
├── commentary_02_srt2images_timeline/ # Main CH02 pipeline
│   ├── tools/
│   │   ├── factory.py                # Main entry point
│   │   ├── run_pipeline.py           # Pipeline execution
│   │   └── auto_capcut_run.py        # CapCut draft creation
│   ├── src/                          # Source code
│   │   └── srt2images/               # Core logic
│   │       └── ...
│   └── input/CH02_哲学系/            # CH02 SRT files
└── factory_common/                    # Shared modules
```

## Troubleshooting

### Common Issues

1. **ModuleNotFoundError**: Ensure the package is installed in editable mode (`pip install -e .`)
2. **API Key Errors**: Verify your `.env` file has the required keys
3. **Permission Issues**: Check that input/output directories are writable

### Verification Steps

To verify the installation is working:

```bash
# Check CLI availability
factory-ch02 --help

# Test import of key modules
python -c "from commentary_02_srt2images_timeline.tools.factory import main; print('Success')"
```

## Development Notes

The restructuring eliminated the need for PYTHONPATH manipulation by:
- Creating a proper package structure with pyproject.toml
- Updating import statements to work with the installed package
- Ensuring CLI entry points work from any directory
- Maintaining backward compatibility with existing functionality

The pipeline now operates reliably with standard Python packaging practices rather than requiring specific directory structure manipulations.