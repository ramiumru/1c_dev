# Public-history privacy maintenance

The public history has been sanitized to remove private deployment contexts,
company-specific standards and identifiers, and corporate commit-email metadata.
This document intentionally contains no former private values or old commit IDs.

## Prevent reintroduction

- Keep actual deployment profiles, project catalogs, company standards, internal
  addresses, credentials and company model settings in the private overlay.
- Use fictional neutral examples in the public repository.
- Use a public GitHub noreply identity for this repository's commits.
- A mailmap only changes presentation; it does not remove original commit metadata.
- After a history rewrite, clone again. Never merge or push branches from an old
  clone into the sanitized history. Preserve uncommitted work separately and review
  patches before applying them to a new clone.
- Update external pins and submodule gitlinks to an audited sanitized revision.
- Keep any pre-cleanup backup private. Never publish it as a backup branch or tag.
- Review CI logs/artifacts, release assets, PRs, issues and other public surfaces
  separately from Git object history.

## Limits

Rewriting controlled branches cannot remove external clones or forks. GitHub may
retain old objects, cached pages and read-only references. Residual sensitive data
may require GitHub Support; removal is not guaranteed for non-secret information.

Official procedure:
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
