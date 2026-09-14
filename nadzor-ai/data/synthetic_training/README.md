# Synthetic training workflow

This is a benchmark-safe curriculum for the simple competition comparator.
It does **not** fine-tune GigaChat weights. It evaluates synthetic PD/RD pairs and stores only generalized investigation lessons in local inspector memory.

## 1. Generate 30 synthetic PD/RD pairs

From repository root:

```powershell
python nadzor-ai\scripts\generate_synthetic_corpus.py
```

Output: `nadzor-ai/data/synthetic_training/generated/`.

The corpus contains 27 discrepancy cases and 3 negative controls. Each case has PD PDF, RD PDF and `ground_truth.json`.

## 2. Run curriculum and store generalized lessons

```powershell
python nadzor-ai\scripts\train_synthetic_curriculum.py --limit 30
```

The runner uses `simple_competition_compare.py` on every synthetic pair, scores the result against synthetic ground truth and sends only generic lessons from missed categories to `inspector_memory.sqlite3`.

Report: `nadzor-ai/data/synthetic_training/latest_training_report.json`.

## 3. Test the real mini benchmark in experienced/demo mode

```powershell
python nadzor-ai\scripts\simple_competition_compare.py `
  --pd "C:\OSR\mini_bench\01_PD_MINI.pdf" `
  --rd "C:\OSR\mini_bench\02_RD_OV1_MINI.pdf" `
  --rd "C:\OSR\mini_bench\03_RD_OV2_1_MINI.pdf" `
  --output "C:\OSR\mini_bench\simple_competition_result.json"
```

Do not pass `--blind` for this learned/demo transfer check. For an unbiased benchmark baseline, run the same command with `--blind`; blind mode ignores learned memory.

## Safety against benchmark leakage

- synthetic cases use fictional identifiers;
- real benchmark expected answers are never passed to the comparator;
- synthetic ground truth is used only for offline scoring;
- training feedback sent to memory contains only category-level search/verification habits;
- never hardcode case answers into runtime prompts or routing.
