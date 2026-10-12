# Working instructions

- Keep the thesis source and research repository unchanged unless separately authorized.
- Record proposed behavior separately from verified research behavior.
- Preserve all 16 activity labels. Processing failures are not the `other` class.
- Model predictions must retain their input timestamps and configuration identity.
- Never commit video data, credentials, model weights, or adapter weights.
- Commit completed, verified changes in small, coherent pieces instead of accumulating a large uncommitted worktree.
- Never commit directly to `main`; use feature branches and pull requests for all changes.
- Open pull requests as drafts (`gh pr create --draft`) and push freely while work is in progress. Mark the PR ready (`gh pr ready`) only once the change is complete and verified; that triggers the single automatic Claude code review. Later pushes are not reviewed automatically; to request another review before merging, add the `claude-review` label (remove and re-add it to trigger again). `@claude` in a PR comment also asks Claude directly.
