# Working instructions

- Keep the thesis source and research repository unchanged unless separately authorized.
- Record proposed behavior separately from verified research behavior.
- Preserve all 16 activity labels. Processing failures are not the `other` class.
- Keep Python modules under about 500 lines. Split a module along a real boundary (for example, `monitoring.py` lifecycle vs. `scheduling.py`) before it grows past that, but don't create tiny single-purpose modules either: put small helpers in the module that owns their concern instead of adding a new file. Put command-line wrappers in `scripts/`, not in the `fall_detection` package.
- Model predictions must retain their input timestamps and configuration identity.
- Never commit video data, credentials, model weights, or adapter weights.
- Commit completed, verified changes in small, coherent pieces instead of accumulating a large uncommitted worktree.
- Never commit directly to `main`; use feature branches and pull requests for all changes.
- Open pull requests as drafts (`gh pr create --draft`) and push freely while work is in progress. Mark the PR ready (`gh pr ready`) only once the change is complete and verified; that triggers the single automatic Claude code review. Later pushes are not reviewed automatically; to request another review before merging, add the `claude-review` label (remove and re-add it to trigger again). `@claude` in a PR comment also asks Claude directly.
- In web UI text, don't chain values with ` · `. Give separate fields their own rows, put a status reason in parentheses, and write meta text as plain words or comma lists.
