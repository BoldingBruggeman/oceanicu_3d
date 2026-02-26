#!/usr/bin/env python3
"""
Complete Workflow: Ocean Model Analysis → Hugo Website
Integrates analysis pipeline with website generation.
"""

import sys
import argparse
import subprocess
from pathlib import Path
from datetime import datetime


class IntegratedWorkflow:
    """Complete workflow from raw data to published website."""
    
    def __init__(
        self,
        data_dir: str = "./data",
        output_dir: str = "./model_outputs",
        hugo_dir: str = "./hugo_site"
    ):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.hugo_dir = Path(hugo_dir)
        
    def run_analysis(
        self,
        model_name: str,
        obs_folder: str,
        obs_pattern: str,
        model_folder: str,
        model_pattern: str,
        variable: str,
        analysis_type: str = "surface",
        **kwargs
    ):
        """
        Run ocean model analysis using the analysis toolkit.
        
        Parameters:
        -----------
        model_name : str
            Name of the model
        obs_folder, model_folder : str
            Data folders
        obs_pattern, model_pattern : str
            File patterns
        variable : str
            Variable to analyze
        analysis_type : str
            'surface', 'depth_slice', or '3d'
        **kwargs : additional arguments for the analyzer
        """
        print(f"\n{'='*80}")
        print(f"RUNNING ANALYSIS: {model_name} - {variable}")
        print(f"{'='*80}")
        
        # Determine output directory
        output_subdir = self.output_dir / f"{model_name.lower().replace(' ', '_')}"
        output_subdir.mkdir(parents=True, exist_ok=True)
        
        # Import appropriate analyzer
        if analysis_type == "3d":
            from depth_analysis.depth_resolved_analysis import DepthResolvedAnalyzer
            
            analyzer = DepthResolvedAnalyzer(output_dir=str(output_subdir))
            
            analyzer.add_dataset_pair(
                obs_folder=obs_folder,
                model_folder=model_folder,
                obs_pattern=obs_pattern,
                model_pattern=model_pattern,
                variable=variable,
                standard_depths=kwargs.get('standard_depths'),
                **{k: v for k, v in kwargs.items() if k != 'standard_depths'}
            )
            
            analyzer.load_all_data()
            analyzer.interpolate_to_standard_depths(
                standard_depths=kwargs.get('standard_depths'),
                method=kwargs.get('depth_interp_method', 'linear')
            )
            analyzer.regrid_horizontal(
                method=kwargs.get('regrid_method', 'nearest'),
                direction=kwargs.get('regrid_direction', 'obs_to_model')
            )
            analyzer.compute_statistics()
            analyzer.create_visualizations()
            analyzer.save_statistics_report()
            analyzer.save_netcdf_outputs()
            
        else:
            from multi_variable_analysis import MultiVariableAnalyzer
            
            analyzer = MultiVariableAnalyzer(output_dir=str(output_subdir))
            
            if analysis_type == "surface":
                analyzer.add_dataset_pair(
                    obs_folder=obs_folder,
                    model_folder=model_folder,
                    obs_pattern=obs_pattern,
                    model_pattern=model_pattern,
                    variable=variable,
                    **kwargs
                )
            # For depth_slice, would need to extract first (see example_02)
            
            analyzer.load_all_data()
            analyzer.regrid_data(
                method=kwargs.get('regrid_method', 'nearest'),
                direction=kwargs.get('regrid_direction', 'obs_to_model')
            )
            analyzer.compute_statistics()
            analyzer.create_visualizations()
            analyzer.save_statistics_report()
            analyzer.save_netcdf_outputs()
        
        print(f"\n✓ Analysis complete: {output_subdir}")
        return output_subdir
    
    def generate_website(self, custom_weights: dict = None):
        """
        Generate Hugo website from all analysis results.
        
        Parameters:
        -----------
        custom_weights : dict, optional
            Custom weights for ranking calculation
        """
        print(f"\n{'='*80}")
        print("GENERATING HUGO WEBSITE")
        print(f"{'='*80}")
        
        # Import Hugo tools
        sys.path.insert(0, str(self.hugo_dir))
        from analysis_to_hugo import AnalysisToHugo, ModelRanking
        
        converter = AnalysisToHugo(hugo_site_dir=str(self.hugo_dir))
        ranker = ModelRanking(hugo_site_dir=str(self.hugo_dir))
        
        analyses = []
        
        # Process each model output directory
        for model_dir in self.output_dir.iterdir():
            if not model_dir.is_dir():
                continue
            
            stats_report = model_dir / "statistics_report.txt"
            if not stats_report.exists():
                continue
            
            model_name = model_dir.name.replace('_', ' ').title()
            
            # Look for metadata
            metadata_file = model_dir / "metadata.md"
            
            try:
                slug, frontmatter = converter.create_analysis_page(
                    analysis_dir=str(model_dir),
                    model_name=model_name,
                    metadata_file=str(metadata_file) if metadata_file.exists() else None
                )
                
                frontmatter['slug'] = slug
                analyses.append(frontmatter)
                print(f"✓ Processed: {model_name}")
                
            except Exception as e:
                print(f"✗ Error processing {model_name}: {e}")
        
        # Generate index and rankings
        print(f"\n✓ Generating index page...")
        converter.create_index_page(analyses)
        
        print(f"✓ Generating rankings...")
        metrics = ranker.collect_all_metrics(analyses)
        rankings = ranker.rank_models(metrics, weights=custom_weights)
        ranker.create_ranking_page(rankings, analyses)
        
        print(f"\n✓ Website content generated: {self.hugo_dir}")
        return analyses, rankings
    
    def build_site(self):
        """Build Hugo site for production."""
        print(f"\n{'='*80}")
        print("BUILDING HUGO SITE")
        print(f"{'='*80}")
        
        try:
            result = subprocess.run(
                ['hugo'],
                cwd=self.hugo_dir,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print("✓ Hugo build successful!")
                print(f"  Output: {self.hugo_dir}/public/")
                return True
            else:
                print("✗ Hugo build failed:")
                print(result.stderr)
                return False
                
        except FileNotFoundError:
            print("✗ Hugo not found. Install from: https://gohugo.io/installation/")
            print("  Website content is ready, just run 'hugo' in hugo_site/")
            return False
    
    def preview_site(self):
        """Launch Hugo development server."""
        print(f"\n{'='*80}")
        print("LAUNCHING PREVIEW SERVER")
        print(f"{'='*80}")
        print("\nPress Ctrl+C to stop server\n")
        
        try:
            subprocess.run(
                ['hugo', 'server'],
                cwd=self.hugo_dir
            )
        except FileNotFoundError:
            print("✗ Hugo not found. Install from: https://gohugo.io/installation/")
        except KeyboardInterrupt:
            print("\n\n✓ Server stopped")


def main():
    """Command-line interface for integrated workflow."""
    
    parser = argparse.ArgumentParser(
        description="Integrated workflow: Analysis → Website",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate website from existing analyses
  python integrated_workflow.py --generate-only
  
  # Run new analysis and generate website
  python integrated_workflow.py --analyze \\
      --model "ROMS Regional" \\
      --obs-folder ./data/obs \\
      --model-folder ./data/model \\
      --variable sst
  
  # Full workflow: analyze multiple models and build site
  python integrated_workflow.py --batch ./model_configs.txt --build
  
  # Preview website
  python integrated_workflow.py --preview
        """
    )
    
    # Workflow options
    parser.add_argument(
        '--generate-only',
        action='store_true',
        help='Only generate website from existing analyses (skip analysis)'
    )
    
    parser.add_argument(
        '--build',
        action='store_true',
        help='Build Hugo site for production (run hugo)'
    )
    
    parser.add_argument(
        '--preview',
        action='store_true',
        help='Launch Hugo preview server'
    )
    
    # Analysis options
    parser.add_argument(
        '--analyze',
        action='store_true',
        help='Run new analysis before generating website'
    )
    
    parser.add_argument(
        '--model',
        help='Model name (required with --analyze)'
    )
    
    parser.add_argument(
        '--obs-folder',
        help='Observation data folder'
    )
    
    parser.add_argument(
        '--obs-pattern',
        help='Observation file pattern'
    )
    
    parser.add_argument(
        '--model-folder',
        help='Model output folder'
    )
    
    parser.add_argument(
        '--model-pattern',
        help='Model file pattern'
    )
    
    parser.add_argument(
        '--variable',
        help='Variable to analyze'
    )
    
    parser.add_argument(
        '--type',
        choices=['surface', '3d'],
        default='surface',
        help='Analysis type (default: surface)'
    )
    
    # Batch processing
    parser.add_argument(
        '--batch',
        help='Batch process models from config file'
    )
    
    # Paths
    parser.add_argument(
        '--output-dir',
        default='./model_outputs',
        help='Output directory for analyses'
    )
    
    parser.add_argument(
        '--hugo-dir',
        default='./hugo_site',
        help='Hugo site directory'
    )
    
    args = parser.parse_args()
    
    # Initialize workflow
    workflow = IntegratedWorkflow(
        output_dir=args.output_dir,
        hugo_dir=args.hugo_dir
    )
    
    # Preview mode
    if args.preview:
        workflow.preview_site()
        return
    
    # Run analysis if requested
    if args.analyze:
        if not all([args.model, args.obs_folder, args.model_folder, args.variable]):
            parser.error("--analyze requires: --model, --obs-folder, --model-folder, --variable")
        
        workflow.run_analysis(
            model_name=args.model,
            obs_folder=args.obs_folder,
            obs_pattern=args.obs_pattern or "*.nc",
            model_folder=args.model_folder,
            model_pattern=args.model_pattern or "*.nc",
            variable=args.variable,
            analysis_type=args.type
        )
    
    # Generate website
    if not args.preview:
        analyses, rankings = workflow.generate_website()
        
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")
        print(f"\nProcessed: {len(analyses)} models")
        print(f"Variables: {len(rankings) - 1}")  # -1 for 'overall'
        
        if 'overall' in rankings and rankings['overall']:
            print("\nTop 3 Models:")
            for model, score, rank in rankings['overall'][:3]:
                print(f"  {rank}. {model} (score: {score:.4f})")
    
    # Build site if requested
    if args.build:
        success = workflow.build_site()
        if success:
            print("\n" + "="*80)
            print("DEPLOYMENT READY")
            print("="*80)
            print(f"\nDeploy this directory: {args.hugo_dir}/public/")
    
    # Final instructions
    if not args.preview and not args.build:
        print("\n" + "="*80)
        print("NEXT STEPS")
        print("="*80)
        print(f"\n1. Preview website:")
        print(f"   python {sys.argv[0]} --preview")
        print(f"\n2. Build for production:")
        print(f"   python {sys.argv[0]} --build")
        print(f"\n3. Or manually:")
        print(f"   cd {args.hugo_dir} && hugo server")


if __name__ == "__main__":
    main()
