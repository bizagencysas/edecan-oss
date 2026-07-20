## Summary

Describe the user or maintainer problem and the solution implemented.

## Change type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor or maintenance
- [ ] Documentation
- [ ] Build, packaging, or CI
- [ ] Security hardening

## Validation

List the exact commands or manual checks you ran and their results.

```text
command -> result
```

## Risk and compatibility

Describe backward-compatibility considerations, migrations, security or privacy impact, performance implications, and rollback steps. Write `None` only after reviewing each area.

## Checklist

- [ ] The change is focused and does not remove existing behavior unintentionally.
- [ ] Tests cover new behavior or the reason tests are not applicable is documented.
- [ ] User-facing behavior and configuration changes are documented.
- [ ] No credentials, personal data, private document contents, or generated artifacts are committed.
- [ ] Dependency changes are minimal and lockfiles are updated.
- [ ] Platform-specific behavior was considered for web, desktop, iOS, and Android where relevant.
- [ ] Container or migration changes pass `make selfhost-smoke`, or are marked not applicable.
- [ ] I have read and agree to follow the Code of Conduct and contribution guidelines.

## Related work

Link related issues, design discussions, or pull requests. Use `Closes #123` when merging this pull request should close an issue.
