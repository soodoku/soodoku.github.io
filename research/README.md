# Research catalog

Edit `catalog.json`, then regenerate the page:

```sh
python -m pip install -r .github/scripts/requirements.txt
python .github/scripts/research_catalog.py
```

Run commands from the repository root. Commit both the catalog and generated
`research/index.html`. GitHub Pages serves that HTML directly; it needs no build
service or JavaScript. CI rejects edits that leave the generated page out of date.
The page shell lives in `page.template.html`; shared styling stays in `/style.css`.

## Entries, resources, and sections

The catalog uses JSON Schema, plus validation for references and sync destinations.

- **Entries** describe projects, papers, software, datasets, or blog posts. Each has
  a stable `id`, a `kind`, a `title`, a `primary_resource`, and a list of resources.
  Keep IDs stable when renaming work. `related_entries` can reference other entries
  without copying their records; the generator adds links to those entries.
  Optional `authors` is an ordered list of names. Optional `publication` contains
  a `venue` and integer `year`. These render below the summary when present.
- **Resources** have an ID local to their entry, a URL, a kind (such as `pdf` or
  `repository`), and a role (such as `manuscript`, `replication`, `blog`, or `press`).
  The primary resource uses the entry title as its label; other resources have
  their own `label`. Set `new_tab` to open a link in a new tab.
- **Sections** define the category hierarchy and display order. Their ordered
  `children` contain either nested sections or `{"entry": "entry-id"}` references.
  Every entry must appear in a section. An entry can appear in more than one.

`summary` and `description` are rich-text arrays. Strings are escaped as text;
`{"resource": "resource-id"}` inserts a link from the entry's resources. A small
set of formatting nodes preserves citations, coauthor wording, emphasis, and line
breaks. For example:

```json
{
  "id": "example-project",
  "kind": "project",
  "title": "Example Project",
  "primary_resource": "code",
  "resources": [
    {
      "id": "code",
      "kind": "repository",
      "role": "code",
      "url": "https://github.com/owner/project"
    },
    {
      "id": "blog",
      "kind": "webpage",
      "role": "blog",
      "label": "Project introduction",
      "url": "https://example.org/introduction"
    }
  ],
  "summary": [
    {"resource": "code"},
    {"tag": "br"},
    {"resource": "blog"}
  ],
  "description": ["What the project does."]
}
```

The migration retains existing coauthor and publication text in these arrays;
those strings are not a normalized author or bibliographic database. Resource
URLs and primary titles each have one authoritative field. Formatting nodes
allow `br`, `span`, `i`, `p`, `b`, `em`, `strong`, `sup`, and `sub`; non-break nodes
use a `children` array. Only the existing `highlight` and `coauthor` span classes
and `paper-toggle` entry class are supported. Arbitrary HTML is not accepted.

## Paper synchronization

A resource is synchronized only when it has an explicit `source`:

```json
{
  "id": "manuscript",
  "kind": "pdf",
  "role": "manuscript",
  "url": "papers/downloads_are_cheap.pdf",
  "source": {
    "repo": "recite/softverse",
    "ref": "main",
    "path": "paper/softverse.pdf"
  }
}
```

Use an existing local `papers/*.pdf` destination and the tracked PDF path in a
public source repository. A repository link by itself does not authorize syncing
any files. The catalog maps explicitly verified source PDFs. Unmapped papers remain
manual; this is not a freshness guarantee for the whole site. Missing Women
currently has only a local manuscript PDF, so it has no sync source.

Install Poppler (`brew install poppler` on macOS or `apt-get install poppler-utils`
on Debian/Ubuntu) for PDF validation and text extraction. Preview updates with:

```sh
python .github/scripts/sync_papers.py --report /tmp/paper-updates.md
```

Add `--apply` to replace local copies. The script resolves each repository/ref to
a commit, downloads its mapped PDFs, validates them, and compares first-page text.
It stages all downloads before replacing any local files, so a download or PDF
validation failure leaves the existing files intact. It does not infer which
version is newer from timestamps, and a changed PDF may still be an older draft.

The **Sync papers** workflow runs Mondays at 13:23 UTC, on relevant pushes to
`main`, or through **Run workflow**. It maintains one PR on
`automation/sync-papers`. Review the source commits, page counts, first-page diffs,
and full PDFs before merging. Check for anonymous drafts and update catalog titles
or abstracts if the paper changed. The workflow does not merge or publish a PR.

GitHub's **Allow GitHub Actions to create and approve pull requests** repository
setting must be enabled. The workflow uses the repository token with write access
only in the sync job. It does not approve PRs. PRs created using `GITHUB_TOKEN` do
not trigger additional push/PR workflows; validation runs before the bot creates
the PR. If fresh PR checks are required, close and reopen that PR manually.

## Local checks

```sh
python -m black --check .github/scripts
python -m isort --check-only --profile black .github/scripts
python -m flake8 --max-line-length=88 --extend-ignore=E203 .github/scripts
python .github/scripts/research_catalog.py --check
python -m unittest discover -s .github/scripts -p 'test_*.py' -v
codespell --check-hidden
```

The tests use local PDF fixtures and mock network requests. They cover generation,
invalid references, unsafe destinations, dry runs, repeated runs, corrupt PDFs,
and failure before replacement. They do not require a GitHub token or network.
