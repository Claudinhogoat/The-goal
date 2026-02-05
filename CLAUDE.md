# CLAUDE.md

This file provides guidance for AI assistants working on this repository.

## Project Overview

**Repository:** The-goal
**Description:** A simple website project
**Status:** Active development

## Repository Structure

```
The-goal/
├── CLAUDE.md          # AI assistant guidelines (this file)
└── index.html         # Main website page
```

## Development Guidelines

### Code Style

- Write clean, readable, and maintainable code
- Use meaningful variable and function names
- Keep functions small and focused on a single responsibility
- Add comments only when the logic isn't self-evident

### Git Workflow

1. **Branching:** Work on feature branches prefixed with `claude/`
2. **Commits:** Write clear, descriptive commit messages
3. **Push:** Always use `git push -u origin <branch-name>`

### Commit Message Format

```
<type>: <short description>

<optional body with more details>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

## Commands

```bash
# Open website in browser (if using a local server)
python3 -m http.server 8000

# Then visit http://localhost:8000
```

## Testing

Open `index.html` directly in a browser to test.

## Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | AI assistant guidelines |
| `index.html` | Main website page |

## Notes for AI Assistants

- Read existing code before making modifications
- Avoid over-engineering; keep solutions simple and focused
- Don't add features beyond what was requested
- Be careful not to introduce security vulnerabilities
- Update this CLAUDE.md file as the project evolves
