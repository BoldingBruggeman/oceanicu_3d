#!/bin/bash
#
# Hugo Site Setup Script
# Initializes Hugo site with all required files and directories
#

set -e  # Exit on error

echo "=========================================="
echo "Hugo Ocean Model Validation Site Setup"
echo "=========================================="
echo ""

# Check if Hugo is installed
if ! command -v hugo &> /dev/null; then
    echo "❌ Hugo not found!"
    echo ""
    echo "Please install Hugo first:"
    echo "  macOS:   brew install hugo"
    echo "  Linux:   snap install hugo"
    echo "  Windows: choco install hugo-extended"
    echo "  Or download from: https://gohugo.io/installation/"
    echo ""
    exit 1
fi

echo "✓ Hugo found: $(hugo version | head -1)"
echo ""

# Check if config.toml exists
if [ -f "config.toml" ]; then
    echo "✓ config.toml already exists"
else
    echo "Creating config.toml..."
    cat > config.toml << 'EOF'
baseURL = 'https://example.org/'
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
EOF
    echo "✓ Created config.toml"
fi

# Create directory structure
echo ""
echo "Creating directory structure..."

mkdir -p content/analyses
mkdir -p content/rankings  
mkdir -p content/about
mkdir -p static/analyses
mkdir -p static/css
mkdir -p static/js
mkdir -p archetypes
mkdir -p data
mkdir -p layouts
mkdir -p themes/ocean-validation/layouts/_default
mkdir -p themes/ocean-validation/layouts/partials
mkdir -p themes/ocean-validation/static

echo "✓ Directories created"

# Create theme.toml if missing
if [ ! -f "themes/ocean-validation/theme.toml" ]; then
    echo ""
    echo "Creating theme.toml..."
    cat > themes/ocean-validation/theme.toml << 'EOF'
name = "Ocean Validation"
license = "MIT"
description = "Clean theme for ocean model validation results"
homepage = "https://example.com/"
tags = ["scientific", "validation", "ocean"]
min_version = "0.80.0"

[author]
  name = "Ocean Modeling Team"
  homepage = "https://example.com/"
EOF
    echo "✓ Created theme.toml"
fi

# Create default content
echo ""
echo "Creating default content..."

# Home page
if [ ! -f "content/_index.md" ]; then
    cat > content/_index.md << 'EOF'
---
title: "Ocean Model Validation Dashboard"
date: 2024-01-01
---

Welcome to the Ocean Model Validation Dashboard.
EOF
    echo "✓ Created content/_index.md"
fi

# About page
if [ ! -f "content/about/index.md" ]; then
    cat > content/about/index.md << 'EOF'
---
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

Models are ranked using a composite score that combines all metrics.

## Contact

For questions, please contact the modeling team.
EOF
    echo "✓ Created content/about/index.md"
fi

# Test configuration
echo ""
echo "Testing Hugo configuration..."

if hugo config > /dev/null 2>&1; then
    echo "✓ Hugo configuration valid"
else
    echo "❌ Hugo configuration has errors"
    echo "Run: hugo config"
    exit 1
fi

# Check theme files
echo ""
echo "Checking theme files..."

THEME_FILES=(
    "themes/ocean-validation/layouts/_default/baseof.html"
    "themes/ocean-validation/layouts/_default/single.html"
    "themes/ocean-validation/layouts/_default/list.html"
    "themes/ocean-validation/layouts/index.html"
)

MISSING_COUNT=0
for file in "${THEME_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "✓ $file"
    else
        echo "⚠️  Missing: $file"
        MISSING_COUNT=$((MISSING_COUNT + 1))
    fi
done

if [ $MISSING_COUNT -gt 0 ]; then
    echo ""
    echo "⚠️  WARNING: $MISSING_COUNT theme file(s) missing"
    echo "   Make sure you extracted all files from the package"
    echo "   The theme layout files should be included in your download"
fi

# Final summary
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Directory structure created:"
echo "  ✓ config.toml"
echo "  ✓ content/"
echo "  ✓ static/"
echo "  ✓ themes/ocean-validation/"
echo ""

if [ $MISSING_COUNT -eq 0 ]; then
    echo "✅ All files present - ready to use!"
    echo ""
    echo "Next steps:"
    echo "  1. Test: hugo server"
    echo "  2. Open: http://localhost:1313"
    echo "  3. Generate content: cd examples && python batch_process.py"
    echo ""
else
    echo "⚠️  Theme files missing - see warnings above"
    echo ""
    echo "To fix:"
    echo "  1. Make sure all files were extracted from the package"
    echo "  2. Check themes/ocean-validation/layouts/ directory"
    echo ""
fi

# Offer to test server
echo "Would you like to test the Hugo server now? [y/N]"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    echo ""
    echo "Starting Hugo server..."
    echo "Press Ctrl+C to stop"
    echo ""
    hugo server
fi
