#!/usr/bin/env python3
"""
Batch Processing Script for Ocean Model Analyses
Automatically processes all model outputs and generates Hugo website.
"""

import sys
from pathlib import Path
import argparse

# Add parent directory to path to import analysis_to_hugo
sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis_to_hugo import AnalysisToHugo, ModelRanking


def process_all_models(
    base_dir: str = "./model_outputs",
    hugo_dir: str = "./hugo",
    custom_weights: dict = None
):
    """
    Process all model outputs in a directory and generate website.
    
    Parameters:
    -----------
    base_dir : str
        Directory containing model output folders
    hugo_dir : str
        Hugo site directory
    custom_weights : dict, optional
        Custom weights for ranking (default: equal weighting)
    """
    
    print("=" * 80)
    print("BATCH PROCESSING OCEAN MODEL ANALYSES")
    print("=" * 80)
    
    converter = AnalysisToHugo(hugo_site_dir=hugo_dir)
    ranker = ModelRanking(hugo_site_dir=hugo_dir)
    analyses = []
    
    base_path = Path(base_dir)
    
    if not base_path.exists():
        print(f"Error: Directory not found: {base_dir}")
        return
    
    # Find all analysis directories
    print(f"\nScanning {base_dir} for analysis results...")
    
    model_dirs = []
    for item in base_path.iterdir():
        if not item.is_dir():
            continue
        
        stats_report = item / "statistics_report.txt"
        if stats_report.exists():
            model_dirs.append(item)
    
    print(f"Found {len(model_dirs)} model outputs with statistics reports")
    
    if len(model_dirs) == 0:
        print("\nNo valid model outputs found!")
        print("Each model output directory must contain 'statistics_report.txt'")
        return
    
    # Process each model
    print("\n" + "=" * 80)
    print("PROCESSING MODELS")
    print("=" * 80)
    
    for idx, model_dir in enumerate(model_dirs, 1):
        print(f"\n[{idx}/{len(model_dirs)}] Processing: {model_dir.name}")
        
        # Extract model name from directory name or metadata
        model_name = model_dir.name.replace('_', ' ').replace('-', ' ').title()
        
        # Look for metadata file
        metadata_candidates = [
            model_dir / "metadata.md",
            model_dir / "README.md",
            model_dir / f"{model_dir.name}_metadata.md"
        ]
        
        metadata_file = None
        for candidate in metadata_candidates:
            if candidate.exists():
                metadata_file = candidate
                print(f"  Found metadata: {candidate.name}")
                break
        
        try:
            slug, frontmatter = converter.create_analysis_page(
                analysis_dir=str(model_dir),
                model_name=model_name,
                metadata_file=str(metadata_file) if metadata_file else None
            )
            
            frontmatter['slug'] = slug
            analyses.append(frontmatter)
            
            print(f"  ✓ Created page: {slug}")
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            continue
    
    if len(analyses) == 0:
        print("\nNo analyses were successfully processed!")
        return
    
    # Generate index page
    print("\n" + "=" * 80)
    print("GENERATING INDEX PAGE")
    print("=" * 80)
    converter.create_index_page(analyses)
    
    # Generate rankings
    print("\n" + "=" * 80)
    print("GENERATING RANKINGS")
    print("=" * 80)
    
    metrics = ranker.collect_all_metrics(analyses)
    
    print(f"\nCollected metrics for {len(metrics)} variables:")
    for var_name, models in metrics.items():
        print(f"  - {var_name}: {len(models)} models")
    
    rankings = ranker.rank_models(metrics, weights=custom_weights)
    ranker.create_ranking_page(rankings, analyses)
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\nSuccessfully processed: {len(analyses)}/{len(model_dirs)} models")
    print(f"Variables analyzed: {len(metrics)}")
    print(f"Hugo site directory: {hugo_dir}")
    
    # Print top 3 overall
    if 'overall' in rankings and len(rankings['overall']) > 0:
        print("\nTop 3 Models (Overall):")
        for model, score, rank in rankings['overall'][:3]:
            print(f"  {rank}. {model} (score: {score:.4f})")
    
    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print(f"\n1. Preview website:")
    print(f"   cd {hugo_dir}")
    print(f"   hugo server")
    print(f"   # Open http://localhost:1313")
    print(f"\n2. Build for production:")
    print(f"   cd {hugo_dir}")
    print(f"   hugo")
    print(f"   # Output in: {hugo_dir}/public/")
    print()


def main():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Batch process ocean model analyses for Hugo website",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all models in current directory
  python batch_process.py
  
  # Process models from specific directory
  python batch_process.py --input /path/to/models
  
  # Use custom Hugo site location
  python batch_process.py --output /path/to/hugo_site
  
  # Custom ranking weights (emphasize RMSE)
  python batch_process.py --weight-rmse 0.5 --weight-correlation 0.3
        """
    )
    
    parser.add_argument(
        '--input', '-i',
        default='./model_outputs',
        help='Directory containing model output folders (default: ./model_outputs)'
    )
    
    parser.add_argument(
        '--output', '-o',
        default='./hugo_site',
        help='Hugo site directory (default: ./hugo_site)'
    )
    
    parser.add_argument(
        '--weight-rmse',
        type=float,
        default=0.4,
        help='Weight for RMSE in ranking (default: 0.4)'
    )
    
    parser.add_argument(
        '--weight-bias',
        type=float,
        default=0.2,
        help='Weight for bias in ranking (default: 0.2)'
    )
    
    parser.add_argument(
        '--weight-mae',
        type=float,
        default=0.2,
        help='Weight for MAE in ranking (default: 0.2)'
    )
    
    parser.add_argument(
        '--weight-correlation',
        type=float,
        default=0.2,
        help='Weight for correlation in ranking (default: 0.2)'
    )
    
    args = parser.parse_args()
    
    # Validate weights sum to 1.0
    total_weight = (args.weight_rmse + args.weight_bias + 
                   args.weight_mae + args.weight_correlation)
    
    if abs(total_weight - 1.0) > 0.01:
        print(f"Warning: Weights sum to {total_weight:.2f}, normalizing...")
        # Normalize
        factor = 1.0 / total_weight
        args.weight_rmse *= factor
        args.weight_bias *= factor
        args.weight_mae *= factor
        args.weight_correlation *= factor
    
    custom_weights = {
        'rmse': args.weight_rmse,
        'bias': args.weight_bias,
        'mae': args.weight_mae,
        'correlation': args.weight_correlation
    }
    
    print(f"\nRanking weights:")
    for metric, weight in custom_weights.items():
        print(f"  {metric}: {weight:.2f} ({weight*100:.0f}%)")
    
    # Run batch processing
    process_all_models(
        base_dir=args.input,
        hugo_dir=args.output,
        custom_weights=custom_weights
    )


if __name__ == "__main__":
    main()
