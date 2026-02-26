# 🌊 Ocean Model Validation Website - Complete System

## ✅ What You Have - Complete Hugo Website System

A professional, automated website generator for ocean model validation results with intelligent rankings.

### 📦 Package Contents

```
hugo_site/                              Complete Hugo website system
├── OVERVIEW.md                         ⭐ Start here - complete system overview
├── QUICKSTART.md                       🚀 5-minute quick start guide  
├── README.md                           📖 Full API documentation
│
├── analysis_to_hugo.py                 🐍 Main conversion script
├── integrated_workflow.py              🐍 All-in-one workflow script
├── config.toml                         ⚙️ Hugo configuration
│
├── content/                            📄 Website content (auto-generated)
│   ├── analyses/                       Individual model pages
│   └── rankings/                       Rankings page
│
├── themes/ocean-validation/            🎨 Custom responsive theme
│   └── layouts/
│       ├── _default/
│       │   ├── baseof.html            Base template with CSS
│       │   ├── single.html            Analysis page template
│       │   └── list.html              Index template
│       └── index.html                 Home page template
│
├── static/                             🖼️ Assets (auto-populated)
│   ├── analyses/                      Analysis images
│   └── rankings.json                  Rankings data (JSON export)
│
└── examples/
    ├── batch_process.py                📝 Batch processing script
    └── example_metadata.md             📝 Metadata file example
```

## 🎯 Three Usage Modes

### Mode 1: Batch Process Existing Analyses (Simplest)

```bash
# 1. Put your analysis outputs here
model_outputs/
├── model1/statistics_report.txt
├── model2/statistics_report.txt
└── model3/statistics_report.txt

# 2. Run batch processor
cd hugo_site/examples
python batch_process.py --input ../../model_outputs

# 3. Preview
cd .. && hugo server
# Open http://localhost:1313

# 4. Build
hugo
# Deploy: public/
```

### Mode 2: Integrated Workflow (Analysis + Website)

```bash
# Run analysis then generate website
cd hugo_site
python integrated_workflow.py \
    --analyze \
    --model "ROMS" \
    --obs-folder ./data/obs \
    --model-folder ./data/model \
    --variable sst \
    --build \
    --preview
```

### Mode 3: Python API (Custom Integration)

```python
from analysis_to_hugo import AnalysisToHugo, ModelRanking

converter = AnalysisToHugo()
ranker = ModelRanking()

# Convert analyses
slug, frontmatter = converter.create_analysis_page(...)

# Generate rankings
rankings = ranker.rank_models(metrics)
```

## 📊 Ranking System (10-20 Models)

### How It Works

**Composite Score Formula:**
```
Score = 0.4×RMSE + 0.2×|Bias| + 0.2×MAE + 0.2×(1-Correlation)
```

**Customizable Weights:**
```bash
python batch_process.py \
    --weight-rmse 0.5 \
    --weight-correlation 0.3 \
    --weight-bias 0.1 \
    --weight-mae 0.1
```

### Rankings Generated

1. **Per Variable** - SST, Temperature, Salinity rankings
2. **Overall** - Average across all variables
3. **Multiple Metrics** - RMSE, Bias, MAE, Correlation shown
4. **Color Coded** - Performance levels highlighted

### Score Interpretation

- **< 0.5** = Excellent ⭐⭐⭐
- **0.5-1.0** = Good ⭐⭐
- **1.0-2.0** = Moderate ⭐
- **> 2.0** = Needs improvement

## 📄 Input Requirements

### Required
- `statistics_report.txt` (from your analysis scripts)

### Optional but Recommended
- PNG plots in variable folders (e.g., `sst/*.png`)
- `metadata.md` file with model information

### Example Directory
```
my_model_output/
├── statistics_report.txt    ✅ Required
├── sst/
│   ├── spatial_maps.png     ✅ Auto-detected
│   └── time_series.png
└── metadata.md              ✅ Recommended
```

### Example Metadata File
```markdown
---
institution: "Your Lab"
model_version: "v4.2"
resolution: "5km"
period: "2020-2023"
---

# Model Description
Additional details here...
```

## 🌐 Website Features

### Home Page
- Analysis count
- Model count
- Recent analyses
- Quick navigation

### All Analyses
- Grouped by model
- Filterable list
- Quick metrics
- Links to details

### Rankings
- Overall table
- Per-variable rankings
- Color-coded performance
- Methodology

### Individual Pages
- Model metadata
- Statistics summary
- All visualizations
- Full report

## 🚀 Quick Start (10 Minutes)

```bash
# 1. Install Hugo
brew install hugo  # macOS
# or from https://gohugo.io/installation/

# 2. Organize analyses
mkdir model_outputs
# Copy your analysis folders here

# 3. Generate website
cd hugo_site/examples
python batch_process.py --input ../../model_outputs

# 4. Preview
cd ..
hugo server

# 5. Build
hugo
# Upload public/ to web server
```

## 📤 Deployment

### GitHub Pages (Free)
```bash
cd hugo_site && hugo
cd public
git init && git add . && git commit -m "Deploy"
git push origin gh-pages
```

### Netlify (Free, Auto)
1. Push to GitHub
2. Connect to Netlify
3. Build: `hugo`
4. Publish: `public`

### Any Server
Upload `public/` contents

## 🎨 Customization

### Colors
Edit `themes/ocean-validation/layouts/_default/baseof.html`

### Ranking Weights  
Command-line flags or edit `analysis_to_hugo.py`

### Templates
Modify files in `themes/ocean-validation/layouts/`

### Add Pages
```bash
hugo new about/info.md
```

## 📚 Documentation Order

1. **OVERVIEW.md** (this file) - Complete system overview
2. **QUICKSTART.md** - 5-minute start guide
3. **README.md** - Full API reference
4. **examples/** - Working scripts

## ✅ Complete Workflow

```
Your Analyses
     ↓
batch_process.py
     ↓
Hugo Content Generated
     ↓
hugo server (preview)
     ↓
hugo (build)
     ↓
Deploy public/
     ↓
Live Website! 🎉
```

## 🎯 Key Features

✅ **Automated** - Point at analyses, get website  
✅ **Rankings** - Multi-metric composite scores  
✅ **10-20 Models** - Handles comparison perfectly  
✅ **Metadata** - Optional markdown integration  
✅ **Responsive** - Works on mobile/tablet/desktop  
✅ **Fast** - Static site, no database  
✅ **Flexible** - Custom weights, colors, templates  
✅ **Portable** - Deploy anywhere  

## 📦 Export Formats

- **Web Page** - Beautiful HTML (auto)
- **Markdown** - `content/rankings/index.md` (auto)
- **JSON** - `static/rankings.json` (auto)
- **CSV** - Use export function (optional)

## 🔄 Update Workflow

```bash
# 1. Add new model outputs
cp -r new_model model_outputs/

# 2. Regenerate
cd hugo_site/examples
python batch_process.py --input ../../model_outputs

# 3. Rebuild
cd .. && hugo

# 4. Redeploy public/
```

## 🆘 Support

- **Hugo docs:** https://gohugo.io/documentation/
- **Theme files:** `themes/ocean-validation/layouts/`
- **Examples:** `examples/` directory
- **Issues:** Check QUICKSTART.md troubleshooting

## 🎓 Learning Path

1. Read OVERVIEW.md (10 min) ← You are here
2. Follow QUICKSTART.md (5 min)
3. Run batch_process.py (5 min)
4. Customize (optional, 30 min)
5. Deploy (10 min)

**Total: 30-60 minutes to deployed website**

---

## 🌟 What Makes This System Special

### For 10-20 Models

- ✅ Automatic ranking with composite scores
- ✅ Multi-metric comparison tables
- ✅ Variable-specific and overall rankings
- ✅ Customizable weighting schemes
- ✅ Professional presentation

### For Ocean Models

- ✅ Understands ocean model metrics
- ✅ Handles multiple variables (SST, T, S, etc.)
- ✅ Shows depth profiles for 3D data
- ✅ Displays all relevant statistics

### For Researchers

- ✅ Publication-ready visualizations
- ✅ Shareable website
- ✅ Exportable data (JSON/CSV)
- ✅ Professional presentation
- ✅ Easy updates

---

**Ready to create your model validation website?**

→ Start with **QUICKSTART.md**  
→ Run **batch_process.py**  
→ Preview with **hugo server**  
→ Deploy **public/** folder

🌊 **Happy analyzing!** 🌊
