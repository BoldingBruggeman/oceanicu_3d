# Hugo Website System - Quick Start Guide

Complete system for creating a professional website to display ocean model validation results with automated rankings.

## 📁 What You Have

```
hugo_site/
├── README.md                      # Full documentation
├── config.toml                    # Hugo configuration
├── analysis_to_hugo.py            # Main conversion script
├── content/                       # Website content (auto-generated)
├── themes/ocean-validation/       # Custom theme
├── static/                        # Images and assets
└── examples/
    ├── batch_process.py           # Batch processing script
    └── example_metadata.md        # Example metadata file
```

## 🚀 Quick Start (5 Steps)

### Step 1: Install Hugo

```bash
# macOS
brew install hugo

# Linux (Ubuntu/Debian)
sudo snap install hugo

# Windows
choco install hugo-extended

# Or download from: https://gohugo.io/installation/
```

Verify installation:
```bash
hugo version
# Should show: Hugo Static Site Generator v0.1xx
```

### Step 2: Organize Your Model Outputs

Create this structure:
```
model_outputs/
├── roms_model/
│   ├── statistics_report.txt    # Required
│   ├── sst/
│   │   └── *.png
│   ├── temperature/
│   │   └── *.png
│   └── metadata.md              # Optional but recommended
├── nemo_model/
│   ├── statistics_report.txt
│   ├── metadata.md
│   └── ...
└── hycom_model/
    └── ...
```

### Step 3: Process Your Analyses

```bash
cd hugo_site/examples

# Process all models at once
python batch_process.py --input ../../model_outputs

# Or with custom weights (emphasize RMSE)
python batch_process.py \
    --input ../../model_outputs \
    --weight-rmse 0.5 \
    --weight-correlation 0.3 \
    --weight-bias 0.1 \
    --weight-mae 0.1
```

### Step 4: Preview Website

```bash
cd ../  # Back to hugo_site/
hugo server

# Open browser to: http://localhost:1313
```

### Step 5: Build for Production

```bash
# Still in hugo_site/
hugo

# Your website is now in: public/
# Deploy public/ folder to any web server
```

## 📊 Features

### Automated Content Generation

- **Individual analysis pages** for each model
- **Master index** listing all analyses
- **Rankings page** with composite scores
- **Beautiful visualizations** from your PNG files

### Intelligent Rankings

Models are ranked using a composite score:

| Metric | Default Weight | What It Measures |
|--------|---------------|------------------|
| RMSE | 40% | Overall accuracy |
| Bias | 20% | Systematic error |
| MAE | 20% | Average error |
| Correlation | 20% | Pattern agreement |

**Lower scores = Better performance**

### Metadata Support

Add `metadata.md` to any model output directory:

```markdown
---
institution: "Your Lab"
contact: "you@lab.edu"
model_version: "v4.2"
resolution: "5km"
period: "2020-2023"
---

# Additional Information

Your model description here...
```

This appears on the model's page!

## 💡 Common Usage Patterns

### Pattern 1: Single Analysis

```python
from analysis_to_hugo import AnalysisToHugo

converter = AnalysisToHugo(hugo_site_dir="./hugo_site")

slug, frontmatter = converter.create_analysis_page(
    analysis_dir="./roms_output",
    model_name="ROMS Regional",
    metadata_file="./roms_metadata.md"
)

print(f"Created: {slug}")
```

### Pattern 2: Batch Process All Models

```bash
# Use the provided script
python examples/batch_process.py --input /path/to/models
```

### Pattern 3: Custom Rankings

```python
from analysis_to_hugo import ModelRanking

ranker = ModelRanking()

# Emphasize correlation
weights = {
    'correlation': 0.5,
    'rmse': 0.3,
    'bias': 0.1,
    'mae': 0.1
}

rankings = ranker.rank_models(metrics, weights=weights)
ranker.create_ranking_page(rankings, analyses)
```

## 🎨 Customization

### Change Theme Colors

Edit `themes/ocean-validation/layouts/_default/baseof.html`:

```css
/* Current gradient */
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);

/* Ocean blue theme */
background: linear-gradient(135deg, #0083B0 0%, #00B4DB 100%);

/* Sunset theme */
background: linear-gradient(135deg, #f12711 0%, #f5af19 100%);
```

### Modify Ranking Weights

Default weights are in `analysis_to_hugo.py`:

```python
weights = {
    'bias': 0.2,
    'rmse': 0.4,      # ← Modify these
    'mae': 0.2,
    'correlation': 0.2
}
```

## 📤 Deployment Options

### Option 1: GitHub Pages

```bash
cd hugo_site
hugo
cd public

git init
git add .
git commit -m "Deploy website"
git branch -M gh-pages
git remote add origin https://github.com/user/repo.git
git push -f origin gh-pages
```

Access at: `https://user.github.io/repo`

### Option 2: Netlify

1. Push `hugo_site/` to GitHub
2. Go to netlify.com
3. "New site from Git"
4. Build command: `hugo`
5. Publish directory: `public`

### Option 3: Simple Server

```bash
cd hugo_site/public
python -m http.server 8000

# Access at: http://localhost:8000
```

## 🔄 Updating the Website

When you have new analyses:

```bash
# 1. Add new model outputs to model_outputs/

# 2. Re-run batch process
cd hugo_site/examples
python batch_process.py --input ../../model_outputs

# 3. Preview changes
cd ../
hugo server

# 4. Rebuild
hugo
```

## 📋 Requirements

- Python 3.7+
- Hugo (any recent version)
- Python packages: `pyyaml` (optional, for enhanced metadata)

```bash
pip install pyyaml
```

## ✅ Checklist

- [ ] Hugo installed and verified
- [ ] Model outputs organized with `statistics_report.txt`
- [ ] (Optional) Created `metadata.md` files for models
- [ ] Ran batch_process.py successfully
- [ ] Previewed with `hugo server`
- [ ] Built with `hugo`
- [ ] Deployed `public/` folder

## 🎯 What You Get

A professional website with:

1. **Home page** - Overview and recent analyses
2. **All Analyses** - Filterable list of all models
3. **Rankings** - Sortable by variable and metric
4. **Individual pages** - Detailed results for each model
5. **Responsive design** - Works on mobile/tablet/desktop
6. **Static site** - Fast, secure, easy to host

## 📖 Full Documentation

See `README.md` for:
- Detailed API documentation
- Advanced customization
- Export options (CSV, JSON)
- Interactive features
- Troubleshooting guide

## 🆘 Troubleshooting

**Hugo not found**
```bash
# Check PATH
echo $PATH

# Reinstall
brew install hugo  # or your package manager
```

**No content appearing**
```bash
# Check that statistics_report.txt exists
ls model_outputs/*/statistics_report.txt

# Verify processing completed
python examples/batch_process.py --input model_outputs
```

**Images not showing**
```bash
# Check static directory
ls -la static/analyses/

# Verify image paths start with /analyses/
grep -r "!\[" content/analyses/
```

## 💡 Pro Tips

1. **Use metadata files** - They make your results look professional
2. **Organize by date** - Name folders like `roms_2024-01` for sorting
3. **Custom weights** - Adjust for your specific validation priorities
4. **Regular updates** - Re-run batch process when you add models
5. **Version control** - Keep hugo_site/ in git to track changes

---

**You're ready to create a professional model validation website!** 🌊

Run the batch processor, preview with Hugo, and deploy!
