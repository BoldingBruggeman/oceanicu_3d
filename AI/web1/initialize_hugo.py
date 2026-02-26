#!/usr/bin/env python3
"""
Initialize Hugo Site for Ocean Model Validation
Creates proper Hugo structure with theme and configuration.
"""

from pathlib import Path
import shutil


def initialize_hugo_site(target_dir: str = "./hugo_site"):
    """
    Initialize a complete Hugo site structure.
    
    This creates all necessary directories and files for Hugo to work.
    """
    
    print("=" * 80)
    print("INITIALIZING HUGO SITE")
    print("=" * 80)
    
    base = Path(target_dir)
    
    # Create main directories
    directories = [
        "archetypes",
        "content/analyses",
        "content/rankings",
        "content/about",
        "data",
        "layouts",
        "static/analyses",
        "static/css",
        "static/js",
        "themes/ocean-validation/archetypes",
        "themes/ocean-validation/layouts/_default",
        "themes/ocean-validation/layouts/partials",
        "themes/ocean-validation/layouts/shortcodes",
        "themes/ocean-validation/static",
    ]
    
    print("\nCreating directory structure...")
    for dir_path in directories:
        full_path = base / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        print(f"  ✓ {dir_path}")
    
    # Create theme.toml for the custom theme
    print("\nCreating theme configuration...")
    theme_toml = base / "themes/ocean-validation/theme.toml"
    with open(theme_toml, 'w') as f:
        f.write("""name = "Ocean Validation"
license = "MIT"
licenselink = "https://github.com/yourname/yourtheme/blob/master/LICENSE"
description = "Clean theme for ocean model validation results"
homepage = "https://example.com/"
tags = ["scientific", "validation", "ocean", "models"]
features = ["responsive", "rankings", "visualizations"]
min_version = "0.80.0"

[author]
  name = "Ocean Modeling Team"
  homepage = "https://example.com/"
""")
    print(f"  ✓ themes/ocean-validation/theme.toml")
    
    # Create about page
    print("\nCreating default content pages...")
    about_page = base / "content/about/index.md"
    with open(about_page, 'w') as f:
        f.write("""---
title: "About"
date: 2024-01-01
draft: false
---

# About This Validation Dashboard

This website presents comprehensive validation results for ocean circulation models.

## Methodology

Model outputs are compared against observational data using multiple statistical metrics:

- **RMSE**: Root Mean Square Error
- **Bias**: Mean bias (Model - Observations)
- **MAE**: Mean Absolute Error
- **Correlation**: Pearson correlation coefficient

## Rankings

Models are ranked using a composite score that combines all metrics with customizable weights.

## Data Sources

Observational data sources vary by analysis. See individual model pages for details.

## Contact

For questions or more information, please contact the modeling team.
""")
    print(f"  ✓ content/about/index.md")
    
    # Create home page content
    home_page = base / "content/_index.md"
    with open(home_page, 'w') as f:
        f.write("""---
title: "Ocean Model Validation Dashboard"
date: 2024-01-01
---

Welcome to the Ocean Model Validation Dashboard.
""")
    print(f"  ✓ content/_index.md")
    
    # Create .gitkeep files in empty directories
    print("\nCreating placeholder files...")
    for empty_dir in ["static/css", "static/js", "data"]:
        gitkeep = base / empty_dir / ".gitkeep"
        gitkeep.touch()
        print(f"  ✓ {empty_dir}/.gitkeep")
    
    # Verify config.toml exists
    config_file = base / "config.toml"
    if not config_file.exists():
        print(f"\n⚠️  WARNING: config.toml not found at {config_file}")
        print("   Creating default config.toml...")
        create_default_config(base)
    else:
        print(f"\n✓ config.toml exists")
    
    # Verify theme files exist
    theme_files = [
        "themes/ocean-validation/layouts/_default/baseof.html",
        "themes/ocean-validation/layouts/_default/single.html",
        "themes/ocean-validation/layouts/_default/list.html",
        "themes/ocean-validation/layouts/index.html"
    ]
    
    missing_files = []
    for theme_file in theme_files:
        if not (base / theme_file).exists():
            missing_files.append(theme_file)
    
    if missing_files:
        print(f"\n⚠️  WARNING: Missing theme files:")
        for f in missing_files:
            print(f"   - {f}")
        print("\n   These should be in your hugo_site folder.")
        print("   Make sure you extracted all files from the package.")
    else:
        print(f"\n✓ All theme files present")
    
    print("\n" + "=" * 80)
    print("INITIALIZATION COMPLETE")
    print("=" * 80)
    print(f"\nHugo site initialized at: {base.absolute()}")
    print("\nNext steps:")
    print("1. Verify theme files are present (see above)")
    print("2. Run: cd hugo_site && hugo server")
    print("3. Open: http://localhost:1313")
    print()


def create_default_config(base: Path):
    """Create a default config.toml file."""
    config_file = base / "config.toml"
    
    with open(config_file, 'w') as f:
        f.write("""baseURL = 'https://example.org/'
languageCode = 'en-us'
title = 'Ocean Model Validation Dashboard'
theme = 'ocean-validation'

[params]
  description = 'Comprehensive ocean model validation and comparison'
  author = 'Ocean Modeling Team'

[menu]
  [[menu.main]]
    name = "Home"
    url = "/"
    weight = 1
  [[menu.main]]
    name = "Model Rankings"
    url = "/rankings/"
    weight = 2
  [[menu.main]]
    name = "All Analyses"
    url = "/analyses/"
    weight = 3
  [[menu.main]]
    name = "About"
    url = "/about/"
    weight = 4

[taxonomies]
  variable = "variables"
  model = "models"
  metric = "metrics"
""")
    print(f"  ✓ Created config.toml")


if __name__ == "__main__":
    import sys
    
    target = sys.argv[1] if len(sys.argv) > 1 else "./hugo_site"
    
    print(f"\nTarget directory: {target}")
    response = input("Initialize Hugo site here? [y/N]: ")
    
    if response.lower() == 'y':
        initialize_hugo_site(target)
    else:
        print("Cancelled.")
