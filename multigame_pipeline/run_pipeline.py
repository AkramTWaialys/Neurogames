"""
Multi-Game ADHD AI Pipeline - Master Runner
=============================================
Runs all 4 modules across all 5 games, then performs cross-game analysis.

Usage:
    python multigame_pipeline/run_pipeline.py --module all
    python multigame_pipeline/run_pipeline.py --module classify
    python multigame_pipeline/run_pipeline.py --module classify --game gonogo
    python multigame_pipeline/run_pipeline.py --module cross
"""

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from multigame_pipeline.preprocessor import GAME_NAMES


def _banner():
    print("\n" + "=" * 60)
    print("  Multi-Game ADHD AI Pipeline")
    print("=" * 60)
    print(f"  Games : {', '.join(GAME_NAMES)}")
    print(f"  Root  : {_ROOT}\n")


def run_classify(game_name):
    from multigame_pipeline.classification.classifier import run_classification
    t0 = time.time()
    result = run_classification(game_name, verbose=True)
    elapsed = time.time() - t0
    acc = result.get("ensemble_test_acc", 0)
    return {"module": "Classification", "game": game_name, "test_acc": acc, "elapsed": elapsed}


def run_sequence(game_name):
    from multigame_pipeline.sequence.sequence_model import run_sequence_model
    t0 = time.time()
    result = run_sequence_model(game_name, verbose=True)
    elapsed = time.time() - t0
    acc = result.get("test_acc", 0)
    return {"module": "Sequence Model", "game": game_name, "test_acc": acc, "elapsed": elapsed}


def run_assess(game_name):
    from multigame_pipeline.assessment.invisible_assessment import run_assessment
    t0 = time.time()
    result = run_assessment(game_name, verbose=True)
    elapsed = time.time() - t0
    acc = result.get("session_test_acc", 0)
    return {"module": "Assessment", "game": game_name, "test_acc": acc, "elapsed": elapsed}


def run_anomaly(game_name):
    from multigame_pipeline.anomaly.anomaly_detector import run_anomaly_detection
    t0 = time.time()
    result = run_anomaly_detection(game_name, verbose=True)
    elapsed = time.time() - t0
    n_flags = result.get("n_flagged", 0)
    return {"module": "Anomaly Detection", "game": game_name, "test_acc": None, "elapsed": elapsed, "n_flagged": n_flags}


def run_cross():
    from multigame_pipeline.cross_game.cross_game_analysis import run_cross_game_analysis
    t0 = time.time()
    result = run_cross_game_analysis(verbose=True)
    elapsed = time.time() - t0
    maj_acc = result.get("majority_vote_acc", 0)
    return {"module": "Cross-Game", "game": "all", "test_acc": maj_acc, "elapsed": elapsed}


MODULE_MAP = {
    "classify":  run_classify,
    "sequence":  run_sequence,
    "assess":    run_assess,
    "anomaly":   run_anomaly,
}


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Game ADHD AI Pipeline",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--module",
        choices=["all", "classify", "sequence", "assess", "anomaly", "cross"],
        default="all",
        help=(
            "Which module to run:\n"
            "  all      - run all 4 modules on all games + cross-game\n"
            "  classify - Module 1: Classification\n"
            "  sequence - Module 2: Sequence Model\n"
            "  assess   - Module 3: Invisible Assessment\n"
            "  anomaly  - Module 4: Anomaly Detection\n"
            "  cross    - Cross-game analysis only\n"
        ),
    )
    parser.add_argument(
        "--game",
        choices=GAME_NAMES + ["all"],
        default="all",
        help="Which game to process (default: all)",
    )
    args = parser.parse_args()
    _banner()

    games = GAME_NAMES if args.game == "all" else [args.game]
    summary = []

    if args.module == "cross":
        try:
            summary.append(run_cross())
        except Exception as e:
            print(f"\n  [FAIL] Cross-game analysis failed: {e}")
            import traceback; traceback.print_exc()
    elif args.module == "all":
        # Run all modules on all games
        for game in games:
            print(f"\n{'='*60}")
            print(f"  GAME: {game.upper()}")
            print(f"{'='*60}")
            for mod_name, mod_fn in MODULE_MAP.items():
                try:
                    summary.append(mod_fn(game))
                except Exception as e:
                    print(f"\n  [FAIL] {mod_name} on {game}: {e}")
                    import traceback; traceback.print_exc()

        # Cross-game analysis
        try:
            summary.append(run_cross())
        except Exception as e:
            print(f"\n  [FAIL] Cross-game analysis: {e}")
            import traceback; traceback.print_exc()
    else:
        # Single module on selected game(s)
        mod_fn = MODULE_MAP[args.module]
        for game in games:
            try:
                summary.append(mod_fn(game))
            except Exception as e:
                print(f"\n  [FAIL] {args.module} on {game}: {e}")
                import traceback; traceback.print_exc()

    # Print summary table
    print("\n" + "=" * 70)
    print("  PIPELINE SUMMARY")
    print("=" * 70)
    print(f"  {'Module':<22} {'Game':<12} {'Accuracy':>10}  {'Time':>8}")
    print("  " + "-" * 60)
    for r in summary:
        acc_str = f"{r['test_acc']:.4f}" if r.get("test_acc") is not None else "  --"
        print(f"  {r['module']:<22} {r['game']:<12} {acc_str:>10}  {r['elapsed']:>6.1f}s")
    print("=" * 70)
    print(f"\n  Outputs saved to: multigame_pipeline/outputs/\n")


if __name__ == "__main__":
    main()
