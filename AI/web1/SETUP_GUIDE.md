# Hugo Site Setup - Complete Guide

## 🚨 Problem: Missing .toml/.yaml File

If you're getting this error:
```
Error: Unable to locate config file or config directory. 
Perhaps you need to create a new site.
```

This means the Hugo site needs proper initialization. Follow the steps below.

## ✅ Solution: Complete Setup (2 Methods)

### Method 1: Automated Setup (Recommended)

```bash
# 1. Navigate to hugo_site directory
cd hugo_site

# 2. Run initialization script
python initialize_hugo.py

# 3. Verify setup
ls -la

# You should see:
#   config.toml          ✓
#   themes/              ✓
#   content/             ✓
#   static/              ✓

# 4. Test Hugo
hugo version
hugo server

# 5. Open browser to http://localhost:1313
```

### Method 2: Manual Setup

If the automated script doesn't work, follow these steps:

#### Step 1: Verify Directory Structure

Your `hugo_site/` directory should contain:

```
hugo_site/
├── config.toml                    ← Must exist!
├── themes/
│   └── ocean-validation/
│       ├── theme.toml
│       └── layouts/
│           ├── _default/
│           │   ├── baseof.html
│           │   ├── single.html
│           │   └── list.html
│           └── index.html
├── content/
│   ├── _index.md
│   ├── analyses/
│   ├── rankings/
│   └── about/
│       └── index.md
├── static/
└── archetypes/
```

#### Step 2: Create Missing config.toml

If `config.toml` is missing, create it:

```bash
cd hugo_site
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
```

#### Step 3: Create Missing Directories

```bash
cd hugo_site

# Create all required directories
mkdir -p content/analyses
mkdir -p content/rankings
mkdir -p content/about
mkdir -p static/analyses
mkdir -p themes/ocean-validation/layouts/_default
mkdir -p archetypes
```

#### Step 4: Create theme.toml

```bash
cat > themes/ocean-validation/theme.toml << 'EOF'
name = "Ocean Validation"
license = "MIT"
description = "Theme for ocean model validation"
min_version = "0.80.0"

[author]
  name = "Ocean Modeling Team"
EOF
```

#### Step 5: Create Default Content

```bash
# Home page
cat > content/_index.md << 'EOF'
---
title: "Ocean Model Validation Dashboard"
date: 2024-01-01
---

Welcome to the Ocean Model Validation Dashboard.
EOF

# About page
mkdir -p content/about
cat > content/about/index.md << 'EOF'
---
title: "About"
date: 2024-01-01
---

# About This Validation Dashboard

Model validation results and rankings.
EOF
```

#### Step 6: Test Hugo

```bash
# Test configuration
hugo config

# Should show your configuration without errors

# Test server
hugo server

# Open http://localhost:1313
```

## 🔧 Troubleshooting

### Error: "theme not found"

**Problem:** Hugo can't find the theme

**Solution:**
```bash
# Check theme directory exists
ls themes/ocean-validation/

# If missing, the theme files weren't extracted properly
# Make sure you have:
ls themes/ocean-validation/layouts/_default/baseof.html
```

**Fix:**
```bash
# If theme files are missing, they should be in your download
# Check that you extracted ALL files from the package
# The theme files should be included
```

### Error: "Error building site"

**Problem:** Template files have errors

**Solution:**
```bash
# Check for errors
hugo --debug

# Look for specific file mentioned in error
# Common issues:
#   - Missing closing tags in HTML
#   - Incorrect Hugo syntax
```

### Error: "Port already in use"

**Problem:** Hugo server already running

**Solution:**
```bash
# Stop existing server (Ctrl+C)
# Or use different port:
hugo server --port 1314
```

### Site loads but shows blank page

**Problem:** No content generated yet

**Solution:**
```bash
# This is normal before running analysis_to_hugo.py
# Generate some content:
cd examples
python batch_process.py --input ../../model_outputs

# Or create test content:
cd ../content/analyses
cat > test.md << 'EOF'
---
title: "Test Analysis"
date: 2024-01-01
draft: false
---

# Test

This is a test page.
EOF

# Then rebuild
cd ../..
hugo server
```

## 📋 Verification Checklist

Use this checklist to verify everything is set up correctly:

```bash
cd hugo_site

# 1. Check config exists
[ -f config.toml ] && echo "✓ config.toml exists" || echo "✗ config.toml missing"

# 2. Check theme files exist
[ -f themes/ocean-validation/theme.toml ] && echo "✓ theme.toml exists" || echo "✗ theme.toml missing"
[ -f themes/ocean-validation/layouts/_default/baseof.html ] && echo "✓ baseof.html exists" || echo "✗ baseof.html missing"
[ -f themes/ocean-validation/layouts/index.html ] && echo "✓ index.html exists" || echo "✗ index.html missing"

# 3. Check Hugo works
hugo version && echo "✓ Hugo installed" || echo "✗ Hugo not found"

# 4. Check config is valid
hugo config > /dev/null 2>&1 && echo "✓ Config valid" || echo "✗ Config has errors"

# 5. Test build
hugo > /dev/null 2>&1 && echo "✓ Build successful" || echo "✗ Build failed"
```

All items should show ✓

## 🚀 Quick Setup Command (All-in-One)

If you want to set everything up in one command:

```bash
cd hugo_site

# Run initialization
python initialize_hugo.py

# Verify
hugo config

# Test
hugo server

# Open http://localhost:1313
```

## 📁 What Each File Does

| File/Directory | Purpose |
|----------------|---------|
| `config.toml` | Main Hugo configuration (REQUIRED) |
| `themes/ocean-validation/` | Custom theme templates and layouts |
| `themes/ocean-validation/theme.toml` | Theme metadata |
| `content/` | Website content (markdown files) |
| `static/` | Static assets (images, CSS, JS) |
| `layouts/` | Custom layouts (overrides theme) |
| `archetypes/` | Content templates |
| `public/` | Generated website (created by hugo build) |

## 🎯 After Setup

Once everything is working:

```bash
# 1. Generate content from analyses
cd examples
python batch_process.py --input ../../model_outputs

# 2. Preview
cd ..
hugo server

# 3. Build
hugo

# 4. Deploy
# Upload public/ directory to web server
```

## 💡 Pro Tips

### Tip 1: Use Hugo's Built-in Checks

```bash
# Check configuration
hugo config

# Check what content Hugo sees
hugo list all

# Build with verbose output
hugo --verbose

# Check for broken links
hugo --gc --minify
```

### Tip 2: Start Fresh if Needed

If you have a messed up installation:

```bash
# Backup your analysis scripts
cp -r hugo_site/examples ~/backup_examples
cp -r hugo_site/*.py ~/backup_scripts

# Start fresh
rm -rf hugo_site
mkdir hugo_site
cd hugo_site

# Re-extract from package or recreate structure
# Restore your scripts
cp ~/backup_examples/* examples/
cp ~/backup_scripts/*.py .
```

### Tip 3: Minimal Working Configuration

The absolute minimum needed:

```
hugo_site/
├── config.toml          ← This file must exist
└── themes/
    └── ocean-validation/
        ├── theme.toml
        └── layouts/
            └── index.html
```

Everything else can be added later.

## 🆘 Still Having Issues?

### Check Hugo Version

```bash
hugo version

# Should be v0.80.0 or higher
# If older, update Hugo
```

### Check File Permissions

```bash
cd hugo_site
ls -la

# All files should be readable
# If you see permission errors:
chmod -R 755 .
```

### Verify Python Scripts Work

```bash
# Test initialize script
python initialize_hugo.py

# Test analysis script  
cd examples
python batch_process.py --help
```

### Get Detailed Error Info

```bash
# Run Hugo with maximum verbosity
hugo server --verbose --debug --logLevel debug

# Check the output for specific errors
```

## ✅ Success Indicators

You know everything is working when:

1. ✓ `hugo config` runs without errors
2. ✓ `hugo server` starts successfully
3. ✓ http://localhost:1313 loads (even if empty)
4. ✓ No "theme not found" errors
5. ✓ No "config file not found" errors

Once you see these, you're ready to generate content!

## 📞 Next Steps After Setup

1. ✅ Setup complete (this guide)
2. ➡️ Generate content (`batch_process.py`)
3. ➡️ Customize theme (optional)
4. ➡️ Deploy website

---

**Most common issue:** Missing `config.toml` or theme files  
**Quick fix:** Run `python initialize_hugo.py` 🚀
